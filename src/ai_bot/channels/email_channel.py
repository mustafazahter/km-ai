"""E-mail channel: IMAP poller + SMTP replies (TLS only, certificates verified, no plaintext fallback).

Security model (docs/KANALLAR.md): the ``From`` header is trivially forged, so a mail is handled only when
  1. the sender address is in EMAIL_ALLOWED_SENDERS (exact, case-insensitive), AND
  2. (EMAIL_REQUIRE_AUTH_RESULTS, default on) the ``Authentication-Results`` header written by YOUR mail server
     shows an ALIGNED pass: spf=pass with ``smtp.mailfrom`` in the From domain (``smtp.helo`` never counts),
     dkim=pass with ``header.d`` equal to the From domain or its parent domain (never a public suffix such as
     ``com`` or ``co.uk``), or dmarc=pass with ``header.from`` equal to the From domain. A bare ``dkim=pass`` or a
     pass without the identifying property is a failure. EMAIL_AUTHSERV_ID (recommended) names your server: only
     the topmost header carrying that authserv-id counts, and a second header with it is refused as forged.
     Without it the topmost header is trusted, which is only safe when your server always stamps one.
  3. exactly one ``From`` header with exactly one address.
Even then a mail is outside content: turns started from e-mail are marked tainted (no memory/schedule writes
without confirmation) and have NO confirmer, so every confirmation-required tool is refused. A code-by-mail
confirmation would be answerable by anybody who can spoof the sender, which is exactly the attack to avoid.
imaplib/smtplib are blocking; they run in a thread (asyncio.to_thread).
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
import smtplib
import socket
import ssl
import time
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage, Message
from email.utils import formatdate, getaddresses, make_msgid, parseaddr

from ..htmltext import html_to_text
from .base import MAX_INPUT_CHARS, Channel, ChannelError

log = logging.getLogger("ai_bot.channels")

MAX_MAIL_BYTES = 1_000_000
MAX_PER_POLL = 10  # message bodies downloaded per poll
MAX_HEADERS_PER_POLL = 30  # header-only prechecks per poll
MAX_REPLY_CHARS = 20000
ADDRESS_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
NO_REPLY_RE = re.compile(r"(^|[._\-+])(no[._\-]?reply|do[._\-]?not[._\-]?reply|mailer[._\-]?daemon|postmaster|bounce[s]?)([._\-+@]|$)", re.I)
QUOTE_HEAD = [
    re.compile(r"^on .{3,300}$", re.I),  # "On <date>, X wrote:" possibly wrapped over two lines
    re.compile(r"^.{3,300}(tarihinde|tarihli).{0,300}yazdı:?\s*$", re.I),
    re.compile(r"^-{2,}\s*(original message|forwarded message|orijinal ileti|iletilen ileti)", re.I),
    re.compile(r"^_{8,}\s*$"),
    re.compile(r"^-- ?$"),
]
HEADER_KEYS = re.compile(r"^(sent|date|to|subject|gönderilen|tarih|kime|konu|cc):", re.I)


@dataclass
class ParsedMail:
    sender: str
    subject: str
    text: str
    message_id: str
    references: str


def _decode_part(part: Message) -> str:
    try:
        content = part.get_content()
        return content if isinstance(content, str) else ""
    except (LookupError, UnicodeDecodeError, ValueError, AssertionError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace")


def extract_text(msg: Message) -> str:
    plain: str | None = None
    markup: str | None = None
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = _decode_part(part)
        elif ctype == "text/html" and markup is None:
            markup = _decode_part(part)
    if plain is not None and plain.strip():
        return plain
    if markup:
        return html_to_text(markup)[1]
    return ""


def strip_quoted(text: str) -> str:
    """Drop quoted reply text, the 'On … wrote:' header, forwarded blocks and the signature."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(">"):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if QUOTE_HEAD[0].match(s) and (s.lower().endswith(("wrote:", "wrote")) or nxt.lower().startswith("wrote")):
            break
        if any(p.match(s) for p in QUOTE_HEAD[1:]):
            break
        if re.match(r"^(from|gönderen):\s", s, re.I) and any(HEADER_KEYS.match(x.strip()) for x in lines[i + 1:i + 4]):
            break
        out.append(line)
    return "\n".join(out).strip()


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].strip().lower().rstrip(">").lstrip("@")


# Small explicit list: a dkim pass for "co.uk" says nothing about the From domain below it.
PUBLIC_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "com.tr", "org.tr", "net.tr", "gen.tr", "edu.tr",
                   "gov.tr", "k12.tr", "com.au", "net.au", "org.au", "co.jp", "ne.jp", "or.jp", "co.in", "net.in",
                   "org.in", "com.br", "net.br", "org.br", "co.nz", "org.nz", "co.za", "org.za", "com.cn", "net.cn",
                   "org.cn", "com.mx", "com.ar", "co.kr", "com.sg", "com.hk", "co.id", "com.my", "com.ua", "com.pl"}


def parse_auth_results(value: str) -> tuple[str, list[tuple[str, str, dict[str, str]]]]:
    """``(authserv-id, [(method, result, {property: value})])`` of one Authentication-Results header."""
    text = " ".join(re.sub(r"\([^)]*\)", " ", str(value)).split())
    parts = text.split(";")
    ident = parts[0].split()[0].lower() if parts and parts[0].split() else ""
    results = []
    for seg in parts[1:]:
        toks = seg.split()
        m = re.match(r"^([A-Za-z0-9\-]+)=([A-Za-z]+)$", toks[0]) if toks else None
        if not m:
            continue
        props = {}
        for t in toks[1:]:
            if "=" in t:
                k, v = t.split("=", 1)
                props[k.lower()] = v.strip("\"").lower()
        results.append((m.group(1).lower(), m.group(2).lower(), props))
    return ident, results


def dkim_aligned(d: str, from_domain: str) -> bool:
    d = (d or "").strip().lower().strip(".")
    if not d or "." not in d or d in PUBLIC_SUFFIXES:
        return False
    return d == from_domain or from_domain.endswith("." + d)


def trusted_header(msg: Message, authserv_id: str) -> str | None:
    headers = [str(h) for h in (msg.get_all("Authentication-Results") or [])]
    if not headers:
        return None
    wanted = (authserv_id or "").strip().lower()
    if not wanted:
        return headers[0]  # topmost: written by the receiving server (risk documented)
    ours = [i for i, h in enumerate(headers) if parse_auth_results(h)[0] == wanted]
    if len(ours) != 1 or ours[0] != 0:
        return None  # missing, or a second header claiming our id from a position we did not write
    return headers[0]


def auth_results_ok(msg: Message, from_addr: str, authserv_id: str = "") -> bool:
    """An ALIGNED spf/dkim/dmarc pass in the header our own server wrote (see the module docstring)."""
    header = trusted_header(msg, authserv_id)
    if header is None:
        return False
    fd = _domain(from_addr)
    for method, result, props in parse_auth_results(header)[1]:
        if result != "pass":
            continue
        if method == "spf" and props.get("smtp.mailfrom") and _domain(props["smtp.mailfrom"]) == fd:
            return True
        if method == "dkim" and dkim_aligned(props.get("header.d", ""), fd):
            return True
        if method == "dmarc" and props.get("header.from") and _domain(props["header.from"]) == fd:
            return True
    return False


def is_automatic(msg: Message, from_addr: str) -> bool:
    auto = str(msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if str(msg.get("Precedence") or "").strip().lower() in ("bulk", "junk", "list", "auto_reply"):
        return True
    if msg.get("List-Id") or msg.get("List-Unsubscribe") or msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    local = from_addr.split("@", 1)[0]
    return bool(NO_REPLY_RE.search(local + "@"))


def evaluate(raw: bytes, *, allowed: set[str], require_auth: bool, own: str = "",
             authserv_id: str = "") -> tuple[str, ParsedMail | None]:
    """Decide what to do with one message. ``(verdict, mail)``: ok | not_allowed | spoofed | auto | empty | self.
    ``empty`` on a header-only message means every check passed (the body has not been read yet)."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    froms = msg.get_all("From") or []
    if len(froms) != 1 or len(getaddresses([str(froms[0])])) != 1:
        return "spoofed", None  # two From headers / two addresses: parsers disagree about who sent it
    name, addr = parseaddr(str(froms[0]))
    addr = addr.strip().lower()
    if not ADDRESS_RE.match(addr) or addr not in allowed:
        return "not_allowed", None
    if own and addr == own:
        return "self", None
    if require_auth and not auth_results_ok(msg, addr, authserv_id):
        return "spoofed", None
    if is_automatic(msg, addr):
        return "auto", None
    text = strip_quoted(extract_text(msg))[:MAX_INPUT_CHARS].strip()
    if not text:
        return "empty", None
    subject = " ".join(str(msg.get("Subject") or "").split())[:200]
    return "ok", ParsedMail(sender=addr, subject=subject, text=text, message_id=str(msg.get("Message-ID") or "").strip(),
                            references=" ".join(str(msg.get("References") or "").split())[:1500])


def reply_subject(subject: str) -> str:
    subject = " ".join((subject or "").split()) or "ai-bot"
    return subject if re.match(r"^re:", subject, re.I) else f"Re: {subject}"


def map_error(exc: BaseException) -> ChannelError:
    if isinstance(exc, ChannelError):
        return exc
    if isinstance(exc, ssl.SSLCertVerificationError):
        return ChannelError("email_tls", "Sunucunun TLS sertifikası doğrulanamadı (ana makine adı veya sertifika zinciri yanlış).")
    if isinstance(exc, ssl.SSLError):
        return ChannelError("email_tls", "TLS bağlantısı kurulamadı; port/şifreleme ayarını kontrol edin.")
    if isinstance(exc, (smtplib.SMTPAuthenticationError, imaplib.IMAP4.error)):
        text = str(exc).lower()
        if isinstance(exc, smtplib.SMTPAuthenticationError) or "auth" in text or "login" in text or "credentials" in text:
            return ChannelError("email_auth", "Posta sunucusu kullanıcı adı/parolayı reddetti (uygulama parolası gerekebilir).")
        return ChannelError("email_protocol", "IMAP sunucusu isteği kabul etmedi.")
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return ChannelError("email_starttls", "SMTP sunucusu STARTTLS desteklemiyor; düz metin bağlantıya izin verilmez.")
    if isinstance(exc, smtplib.SMTPException):
        return ChannelError("email_smtp", "SMTP sunucusu gönderimi kabul etmedi.")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ChannelError("email_timeout", "Posta sunucusuna ulaşılamadı (zaman aşımı).")
    if isinstance(exc, OSError):
        return ChannelError("email_unreachable", "Posta sunucusuna ulaşılamadı (ağ/DNS hatası veya çıkış izni yok).")
    return ChannelError("email_internal", "Beklenmeyen e-posta hatası.")


def check_host(host: str) -> None:
    """Refuse hosts that resolve to link-local/metadata/multicast/unspecified addresses (private LAN is allowed:
    a self-hosted mail server is a normal setup). Raises ChannelError."""
    from ..netpolicy import address_problem
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise ChannelError("email_unreachable", "Posta sunucusu adı çözümlenemedi.") from None
    for info in infos:
        if address_problem(info[4][0], allow_private=True):
            raise ChannelError("email_blocked", "Bu posta sunucusu adresi güvenlik politikası gereği engellendi.")


class Email(Channel):
    name = "email"
    title = "E-posta"
    prefix = "EMAIL"
    required = ("EMAIL_IMAP_HOST", "EMAIL_IMAP_USER", "EMAIL_SMTP_HOST", "EMAIL_FROM", "EMAIL_PASSWORD")
    max_chunk = MAX_REPLY_CHARS
    can_confirm = False  # sender spoofing: confirmation-required tools are refused (see module docstring)
    tainted = True
    hidden_tools = frozenset({"web.read"})  # no URL fetching from mail turns: a prompt-injected URL would leak memory
    paths: dict[str, str] = {}
    fields = [
        {"name": "EMAIL_IMAP_HOST", "label": "IMAP sunucusu", "kind": "text", "required": True,
         "hint": "Örn. imap.gmail.com, outlook.office365.com, imap.yandex.com.tr."},
        {"name": "EMAIL_IMAP_PORT", "label": "IMAP portu (SSL)", "kind": "int", "hint": "Genelde 993."},
        {"name": "EMAIL_IMAP_USER", "label": "Posta kutusu kullanıcı adı", "kind": "text", "required": True,
         "hint": "Çoğu sağlayıcıda tam e-posta adresi."},
        {"name": "EMAIL_PASSWORD", "label": "Uygulama parolası", "kind": "secret", "required": True,
         "hint": "Normal hesap parolanızı değil, sağlayıcıdan üretilen 'uygulama parolası'nı girin."},
        {"name": "EMAIL_SMTP_HOST", "label": "SMTP sunucusu", "kind": "text", "required": True,
         "hint": "Örn. smtp.gmail.com, smtp.office365.com."},
        {"name": "EMAIL_SMTP_PORT", "label": "SMTP portu", "kind": "int", "hint": "587 (STARTTLS) veya 465 (SSL)."},
        {"name": "EMAIL_FROM", "label": "Gönderen adresi", "kind": "text", "required": True,
         "hint": "Yanıtların 'Kimden' adresi; genelde posta kutusunun adresi."},
        {"name": "EMAIL_ALLOWED_SENDERS", "label": "İzinli gönderenler", "kind": "list",
         "hint": "Tam e-posta adresi. Boş liste = kimseden posta işlenmez."},
        {"name": "EMAIL_POLL_SECONDS", "label": "Yoklama aralığı (sn)", "kind": "int", "hint": "En az 30."},
        {"name": "EMAIL_AUTHSERV_ID", "label": "Posta sunucusu kimliği (Authentication-Results, önerilir)", "kind": "text",
         "hint": "Bir postanın başlığındaki 'Authentication-Results: <kimlik>;' değeri (ör. mx.google.com). Doluysa yalnız bu kimlikli, en üstteki başlık güvenilir sayılır; boşsa en üstteki başlık güvenilir sayılır (sahte başlık riski)."},
        {"name": "EMAIL_REQUIRE_AUTH_RESULTS", "label": "DKIM/SPF doğrulaması iste (önerilir)", "kind": "bool",
         "hint": "Kapatırsanız sahte 'Kimden' adresiyle bota komut verilebilir."},
    ]

    def __init__(self, manager):
        super().__init__(manager)
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._skip: set[tuple[int, int]] = set()  # (UIDVALIDITY, UID)
        self.last_poll: float | None = None

    def allow_setting(self) -> str:
        return "EMAIL_ALLOWED_SENDERS"

    def normalise(self, value: str) -> str:
        return parseaddr(value)[1].strip().lower() if "<" in value else value.strip().lower()

    def valid_entry(self, value: str) -> bool:
        return bool(ADDRESS_RE.match(self.normalise(value)))

    def entry_hint(self) -> str:
        return "Geçerli bir e-posta adresi yazın (ör. ad@ornek.com)."

    def view(self) -> dict:
        v = super().view()
        v["last_poll"] = self.last_poll
        return v

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            wait = 5.0
            try:
                if self.enabled() and self.configured() and self.allowed():
                    mails = await asyncio.to_thread(self.poll_sync)
                    self.last_poll = time.time()
                    for mail in mails:
                        await self.handle_mail(mail)
                    wait = float(max(30, self.cfg.number("EMAIL_POLL_SECONDS", 60)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                err = map_error(exc)
                self.note_error(err.code)
                wait = max(30.0, float(self.cfg.number("EMAIL_POLL_SECONDS", 60)))
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def handle_mail(self, mail: ParsedMail) -> None:
        ctx = {"to": mail.sender, "subject": mail.subject, "in_reply_to": mail.message_id,
               "references": (mail.references + " " + mail.message_id).strip()}
        inb = self.accept([mail.sender], mail.text, mail.message_id or None, ctx)
        if inb is not None:
            await self.process(inb)

    # ------------------------------------------------------------------ IMAP (blocking, run in a thread)
    def _open_imap(self):
        host = self.cfg.text("EMAIL_IMAP_HOST")
        check_host(host)
        conn = imaplib.IMAP4_SSL(host, self.cfg.number("EMAIL_IMAP_PORT", 993), ssl_context=ssl.create_default_context(),
                                 timeout=30)
        conn.login(self.cfg.text("EMAIL_IMAP_USER"), self.cfg.secret("EMAIL_PASSWORD"))
        return conn

    def _open_smtp(self):
        host, port = self.cfg.text("EMAIL_SMTP_HOST"), self.cfg.number("EMAIL_SMTP_PORT", 587)
        check_host(host)
        ctx = ssl.create_default_context()
        if port == 465:
            conn = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
            conn.ehlo()
        else:
            conn = smtplib.SMTP(host, port, timeout=30)
            conn.ehlo()
            if not conn.has_extn("starttls"):
                conn.close()
                raise smtplib.SMTPNotSupportedError("STARTTLS not supported")
            conn.starttls(context=ctx)
            conn.ehlo()
        conn.login(self.cfg.text("EMAIL_IMAP_USER"), self.cfg.secret("EMAIL_PASSWORD"))
        return conn

    @staticmethod
    def _fetch(imap, uid: bytes, what: str) -> tuple[bytes, bytes]:
        typ, data = imap.uid("FETCH", uid, what)
        if typ != "OK":
            return b"", b""
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2:
                return item[0], item[1]
        return b"", b""

    @staticmethod
    def _uidvalidity(imap) -> int:
        try:
            _, data = imap.response("UIDVALIDITY")
            return int(data[0]) if data and data[0] else 0
        except Exception:
            return 0

    def poll_sync(self) -> list[ParsedMail]:
        allowed = set(self.allowed())
        require = self.cfg.flag("EMAIL_REQUIRE_AUTH_RESULTS")
        own = parseaddr(self.cfg.text("EMAIL_FROM"))[1].lower()
        authserv = self.cfg.text("EMAIL_AUTHSERV_ID")
        found: list[ParsedMail] = []
        try:
            imap = self._open_imap()
        except Exception as exc:
            raise map_error(exc) from None
        try:
            imap.select("INBOX")
            validity = self._uidvalidity(imap)
            typ, data = imap.uid("SEARCH", None, "UNSEEN")
            uids = (data[0] or b"").split() if typ == "OK" and data else []
            if len(self._skip) > 5000:
                self._skip.clear()
            headers_read = bodies_read = 0
            for uid in uids:
                if headers_read >= MAX_HEADERS_PER_POLL or bodies_read >= MAX_PER_POLL:
                    break  # the rest waits for the next poll: a flood cannot make one poll download everything
                key = (validity, int(uid))
                if key in self._skip:
                    continue
                headers_read += 1
                meta, head = self._fetch(imap, uid, "(RFC822.SIZE BODY.PEEK[HEADER])")
                size = re.search(rb"RFC822\.SIZE\s+(\d+)", meta or b"")
                # every sender/authentication check runs on the headers alone, BEFORE any body is downloaded
                verdict, _ = evaluate(head, allowed=allowed, require_auth=require, own=own, authserv_id=authserv)
                if verdict in ("not_allowed", "self"):
                    self._skip.add(key)  # not ours: leave the mailbox untouched
                    continue
                if verdict == "spoofed":
                    self._skip.add(key)
                    log.warning("channel email: message dropped (sender not authenticated)")
                    continue
                if verdict == "auto":
                    self._skip.add(key)
                    imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
                    continue
                if size and int(size.group(1)) > MAX_MAIL_BYTES:
                    self._skip.add(key)
                    log.info("channel email: oversized message skipped")
                    continue
                bodies_read += 1
                _, raw = self._fetch(imap, uid, "(BODY.PEEK[])")
                verdict, mail = evaluate(raw, allowed=allowed, require_auth=require, own=own, authserv_id=authserv)
                self._skip.add(key)
                if verdict == "spoofed":
                    log.warning("channel email: message dropped (sender not authenticated)")
                    continue
                imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
                if verdict == "ok" and mail is not None:
                    found.append(mail)
        except Exception as exc:
            raise map_error(exc) from None
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return found

    # ------------------------------------------------------------------ SMTP
    def build_reply(self, ctx: dict, text: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.cfg.text("EMAIL_FROM")
        msg["To"] = ctx["to"]
        msg["Subject"] = reply_subject(str(ctx.get("subject") or ""))
        if ctx.get("in_reply_to"):
            msg["In-Reply-To"] = str(ctx["in_reply_to"]).replace("\r", "").replace("\n", "")
        if ctx.get("references"):
            msg["References"] = " ".join(str(ctx["references"]).split())
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=_domain(self.cfg.text("EMAIL_FROM")) or None)
        msg["Auto-Submitted"] = "auto-replied"  # other robots (and this one) must not answer it
        msg.set_content(text)
        return msg

    def _send_sync(self, msg: EmailMessage) -> None:
        try:
            smtp = self._open_smtp()
            try:
                smtp.send_message(msg)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
        except Exception as exc:
            raise map_error(exc) from None

    async def send(self, ctx: dict, text: str) -> None:
        await asyncio.to_thread(self._send_sync, self.build_reply(ctx, text))

    def target_for(self, chat_id: int, external: str) -> dict | None:
        return {"to": external, "subject": "ai-bot"}

    def _test_sync(self) -> None:
        try:
            imap = self._open_imap()
            try:
                imap.select("INBOX", readonly=True)
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
            smtp = self._open_smtp()
            try:
                smtp.noop()
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
        except Exception as exc:
            raise map_error(exc) from None

    async def test(self) -> str:
        if self.missing():
            raise ChannelError("email_not_configured", "Önce sunucu, kullanıcı, parola ve gönderen adresini kaydedin.")
        await asyncio.to_thread(self._test_sync)
        self.note_ok()
        return "IMAP girişi ve SMTP bağlantısı başarılı (hiçbir e-posta gönderilmedi)."
