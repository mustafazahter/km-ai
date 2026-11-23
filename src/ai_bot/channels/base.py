"""Shared channel logic: allow-list, dedupe, rate limit, chunking, human confirmation, sending.

chat_id mapping
    A channel sender has no Telegram chat id. ``chat_id_for(channel, external_id)`` derives a stable
    NEGATIVE integer from ``sha256(channel + ":" + external_id)``: ``-(2**52 + h)`` with ``h`` the first
    8 bytes of the digest modulo ``2**52 - 1``. Magnitudes lie in [2**52, 2**53 - 2], so the id is a
    JavaScript-safe integer (the panel's memory/tasks pages parse JSON numbers) and fits SQLite. It cannot
    collide with Telegram ids: users are positive and below 2**52, groups/channels are about -1e12..-1e13.
    Memory, history and schedules of a channel user therefore never mix with a Telegram chat. The same
    sender always gets the same id; ``ChannelManager`` accepts such an id only while the sender is in the
    allow-list of an ENABLED channel.

Rules every channel obeys (tests: tests/test_channels_*.py)
    * disabled channel: inbound URLs answer 404;
    * only allow-listed senders reach the agent (an empty list = nobody);
    * dedupe by provider message id, 20 messages/minute per sender, 4000 characters at most;
    * message bodies and secrets are never logged;
    * human confirmation by one-time code (``EVET <kod>`` / ``HAYIR``) from the SAME sender.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets as _secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from stack_kit import redact

from .. import commands

if TYPE_CHECKING:
    from .manager import ChannelManager

log = logging.getLogger("ai_bot.channels")

CHAT_BASE = 1 << 52  # channel chat ids: -(2**52 + h), h < 2**52 - 1 (JS-safe integers)
CHAT_SPAN = (1 << 52) - 1
MAX_PREVIEW = 4000  # a confirmation shows everything or the action is refused; never a cut-off view
PREVIEW_REFUSED = "İşlemin ayrıntısı çok uzun; güvenle gösterilemediği için onay istenmedi ve işlem yapılmadı."
MAX_MESSAGE_AGE = 600.0  # seconds: older provider timestamps are replays
MAX_INPUT_CHARS = 4000
RATE_LIMIT = 20  # messages per sender per minute
RATE_WINDOW = 60.0
DEDUPE_TTL = 3600.0
DEDUPE_MAX = 4000
MAX_BODY_BYTES = 1_000_000
CONFIRM_RE = re.compile(r"^\s*(evet|hay[ıi]r)(?:\s+([0-9]{6}))?\s*$", re.IGNORECASE)


def chat_id_for(channel: str, external_id: str) -> int:
    digest = hashlib.sha256(f"{channel}:{external_id}".encode("utf-8")).digest()
    return -(CHAT_BASE + int.from_bytes(digest[:8], "big") % CHAT_SPAN)


def is_channel_chat(chat_id: Any) -> bool:
    return (isinstance(chat_id, int) and not isinstance(chat_id, bool)
            and -(CHAT_BASE + CHAT_SPAN) <= chat_id <= -CHAT_BASE)


def chunk_text(text: str, limit: int) -> list[str]:
    """Split ``text`` into pieces of at most ``limit`` characters, preferring paragraph/line/space breaks."""
    text = (text or "").strip() or "(boş yanıt)"
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = max(rest.rfind("\n\n", 0, limit), rest.rfind("\n", 0, limit), rest.rfind(" ", 0, limit))
        if cut < limit // 2:
            cut = limit
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return [c for c in out if c]


class ChannelError(Exception):
    """A provider/setup problem with a short safe ``code`` and a Turkish ``message`` (never a provider body)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def http_problem(channel: str, status: int) -> ChannelError:
    if status in (401, 403):
        return ChannelError(f"{channel}_auth", "Sağlayıcı erişimi reddetti: token/parola geçersiz, süresi dolmuş veya yetkisiz.")
    if status == 404:
        return ChannelError(f"{channel}_not_found", "Sağlayıcı kaynağı bulamadı: kimlik (ör. telefon numarası kimliği) yanlış olabilir.")
    if status == 429:
        return ChannelError(f"{channel}_rate_limited", "Sağlayıcı istek sınırına ulaştı; biraz sonra tekrar deneyin.")
    if 400 <= status < 500:
        return ChannelError(f"{channel}_bad_request", "Sağlayıcı isteği kabul etmedi; ayarları kontrol edin.")
    return ChannelError(f"{channel}_provider_down", "Sağlayıcı şu an yanıt vermiyor; biraz sonra tekrar deneyin.")


async def read_body(request: Request, limit: int = MAX_BODY_BYTES) -> bytes:
    """Raw body with a hard size cap (the signature is computed over exactly these bytes)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "too large")
        chunks.append(chunk)
    return b"".join(chunks)


def too_old(sent_at: float | None, now: float | None = None) -> bool:
    """True when a provider timestamp (unix seconds) is more than MAX_MESSAGE_AGE old: a replay. Unknown = fine."""
    if sent_at is None:
        return False
    return (time.time() if now is None else now) - sent_at > MAX_MESSAGE_AGE


def safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


@dataclass
class Inbound:
    sender: str  # canonical external id, already checked against the allow-list
    text: str
    msg_id: str | None
    ctx: dict  # where the reply goes (channel specific)


@dataclass
class Pending:
    code: str
    future: "asyncio.Future[bool]"
    expires: float


class Channel:
    name = ""
    title = ""
    prefix = ""  # setting-name prefix, e.g. WHATSAPP
    required: tuple[str, ...] = ()  # settings that must be filled for the channel to be usable
    max_chunk = 4000
    tainted = False  # turns started here carry untrusted outside content
    hidden_tools: frozenset[str] = frozenset()  # tools the model does not even see in turns started here
    can_confirm = True
    min_confirm_timeout = 15.0  # CONFIRM_TIMEOUT is clamped to this at least
    paths: dict[str, str] = {}
    fields: list[dict] = []  # panel metadata (see panel_channels)

    def __init__(self, manager: "ChannelManager"):
        self.m = manager
        self.ai = manager.ai
        self.last_inbound: float | None = None
        self.last_error: dict | None = None
        self.denied: deque[dict] = deque(maxlen=5)
        self.pending: dict[int, Pending] = {}
        self._hits: dict[str, deque[float]] = {}
        self._seen: "OrderedDict[str, float]" = OrderedDict()

    # ------------------------------------------------------------------ configuration
    @property
    def cfg(self):
        return self.m.config

    def enabled(self) -> bool:
        return self.cfg.flag(f"{self.prefix}_ENABLED")

    def missing(self) -> list[str]:
        out = []
        for name in self.required:
            if name in self.m.secret_names:
                if not self.cfg.secret(name):
                    out.append(name)
            elif not self.cfg.text(name):
                out.append(name)
        return out

    def configured(self) -> bool:
        return not self.missing()

    def allow_setting(self) -> str:
        raise NotImplementedError

    def normalise(self, value: str) -> str:
        return value.strip()

    def valid_entry(self, value: str) -> bool:
        return bool(value)

    def entry_hint(self) -> str:
        return "geçersiz değer"

    def allowed(self) -> list[str]:
        out: list[str] = []
        for item in self.cfg.items(self.allow_setting()):
            norm = self.normalise(item)
            if norm and norm not in out:
                out.append(norm)
        return out

    def status(self) -> str:
        if not self.enabled():
            return "off"
        if self.missing():
            return "missing"
        if self.last_error:
            return "error"
        return "ready"

    def view(self) -> dict:
        return {"name": self.name, "title": self.title, "enabled": self.enabled(), "configured": self.configured(),
                "missing": self.missing(), "status": self.status(), "allowed": self.allowed(),
                "last_inbound": self.last_inbound, "last_error": self.last_error,
                "denied": list(self.denied), "can_confirm": self.can_confirm}

    def note_ok(self) -> None:
        self.last_error = None

    def note_error(self, code: str) -> None:
        self.last_error = {"code": str(code)[:60], "at": time.time()}
        log.warning("channel %s: %s", self.name, code)

    def require_enabled(self) -> None:
        if not self.enabled():
            raise HTTPException(404, "Not Found")

    # ------------------------------------------------------------------ inbound pipeline
    def register(self, router: APIRouter) -> None:  # pragma: no cover - overridden
        """Add this channel's inbound routes."""

    def _dedupe(self, msg_id: str | None) -> bool:
        """True when ``msg_id`` was seen before (and remembers it)."""
        if not msg_id:
            return False
        now = time.time()
        while self._seen and (len(self._seen) > DEDUPE_MAX or next(iter(self._seen.values())) < now - DEDUPE_TTL):
            self._seen.popitem(last=False)
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = now
        return False

    def _rate_ok(self, sender: str) -> bool:
        now = time.time()
        hits = self._hits.setdefault(sender, deque())
        while hits and hits[0] < now - RATE_WINDOW:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            return False
        hits.append(now)
        if len(self._hits) > 500:  # forget idle senders
            for key in [k for k, v in self._hits.items() if not v or v[-1] < now - RATE_WINDOW][:200]:
                self._hits.pop(key, None)
        return True

    def accept(self, candidates: list[str], text: str, msg_id: str | None, ctx: dict) -> Inbound | None:
        """Pre-checks that need no I/O: dedupe, allow-list, rate limit. ``None`` = drop silently."""
        if self._dedupe(f"{self.name}:{msg_id}" if msg_id else None):
            return None
        allowed = set(self.allowed())
        sender = None
        for cand in candidates:
            norm = self.normalise(cand or "")
            if norm and norm in allowed:
                sender = norm
                break
        if sender is None:
            first = next((self.normalise(c) for c in candidates if c), "")
            if first:
                self.denied.append({"id": first[:80], "at": time.time()})
            log.info("channel %s: message from a sender that is not allowed was dropped", self.name)
            return None
        if not self._rate_ok(sender):
            log.info("channel %s: rate limit hit, message dropped", self.name)
            return None
        return Inbound(sender=sender, text=text or "", msg_id=msg_id, ctx=ctx)

    def spawn(self, inbound: Inbound) -> None:
        self.m.spawn(self.process(inbound))

    async def process(self, inb: Inbound) -> None:
        chat_id = chat_id_for(self.name, inb.sender)
        try:
            self.last_inbound = time.time()
            self.m.remember(chat_id, self.name, inb.sender, inb.ctx)
            if chat_id in self.pending:
                await self._answer_confirmation(chat_id, inb)
                return
            text = inb.text.strip()
            if not text:
                return
            if len(text) > MAX_INPUT_CHARS:
                await self.send_text(inb.ctx, f"Mesajın çok uzun ({len(text)} karakter). En fazla {MAX_INPUT_CHARS} karakter yazabilirsin.")
                return
            if await commands.dispatch(self, inb, chat_id, text):  # "/hafiza", "!hafiza"...: registered slash commands
                return
            answer = await self.ai.run_ai(chat_id, text)
            await self.send_text(inb.ctx, answer)
        except asyncio.CancelledError:
            raise
        except ChannelError:
            pass  # send_text already recorded the safe code
        except Exception as exc:  # one broken message must not stop the channel
            self.note_error(f"{self.name}_internal")
            log.warning("channel %s: processing failed (%s)", self.name, type(exc).__name__)

    # ------------------------------------------------------------------ confirmation
    def confirm_ctx(self, ctx: dict, sender: str) -> dict:
        """Where the confirmation prompt goes (default: the conversation the request came from)."""
        return ctx

    async def confirm(self, chat_id: int, tool_name: str, preview: Any, *, intro: str | None = None,
                      timeout: float | None = None) -> bool:
        """Human confirmation by one-time code. Only the same sender (same chat id) can answer, once."""
        if not self.can_confirm:
            return False
        entry = self.m.context(chat_id)
        if entry is None:
            return False
        sender, ctx = entry
        body = preview if isinstance(preview, str) else json.dumps(preview, ensure_ascii=False, indent=2, default=str)
        body = redact(body)
        if len(body) > MAX_PREVIEW:
            # never ask about something the user cannot see in full: refuse (nothing is executed)
            try:
                await self.send_text(self.confirm_ctx(ctx, sender), f"{tool_name}: {PREVIEW_REFUSED}")
            except ChannelError:
                pass
            return False
        loop = asyncio.get_running_loop()
        timeout = max(self.min_confirm_timeout, float(timeout or getattr(self.ai.settings, "confirm_timeout", 120.0) or 120.0))
        code = f"{_secrets.randbelow(10 ** 6):06d}"
        pend = Pending(code, loop.create_future(), time.time() + timeout)
        old = self.pending.pop(chat_id, None)
        if old is not None and not old.future.done():
            old.future.set_result(False)
        self.pending[chat_id] = pend
        prompt = (f"{intro or 'AI şu işlemi yapmak istiyor: ' + tool_name}\n\n{body}\n\n"
                  f"Onaylamak için aynen şunu yaz: EVET {code}\nVazgeçmek için: HAYIR\n"
                  f"{int(timeout)} saniye içinde yanıt gelmezse işlem yapılmaz.")
        try:
            await self.send_text(self.confirm_ctx(ctx, sender), prompt)
            return await asyncio.wait_for(pend.future, timeout)
        except asyncio.TimeoutError:
            if self.pending.get(chat_id) is pend:   # free the slot first: a message arriving now is a normal message
                self.pending.pop(chat_id, None)
            try:
                await self.send_text(self.confirm_ctx(ctx, sender), "Süre doldu; işlem yapılmadı.")
            except Exception:
                pass
            return False
        except ChannelError:
            return False
        finally:
            if self.pending.get(chat_id) is pend:
                self.pending.pop(chat_id, None)

    async def _answer_confirmation(self, chat_id: int, inb: Inbound) -> None:
        pend = self.pending.get(chat_id)
        if pend is None or pend.future.done():
            return
        match = CONFIRM_RE.match(inb.text or "")
        approved = bool(match and match.group(1).lower() == "evet" and match.group(2)
                        and safe_equal(match.group(2), pend.code) and time.time() <= pend.expires)
        pend.future.set_result(approved)  # one try: a wrong code or any other text is a refusal
        await self.send_text(inb.ctx, "Onaylandı." if approved else "İşlem iptal edildi; hiçbir şey yapılmadı.")

    # ------------------------------------------------------------------ sending
    async def send(self, ctx: dict, text: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def send_text(self, ctx: dict, text: str) -> None:
        """Send in provider-sized chunks. A failure is recorded (safe code) and re-raised as ChannelError."""
        try:
            for part in chunk_text(text, self.max_chunk):
                await self.send(ctx, part)
        except ChannelError as exc:
            self.note_error(exc.code)
            raise
        else:
            self.note_ok()

    def target_for(self, chat_id: int, external: str) -> dict | None:
        """Reply context for a message the bot starts itself (reminders). ``None`` = unknown."""
        return None

    async def test(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    # ------------------------------------------------------------------ http
    async def http(self, method: str, url: str, **kw: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(transport=self.m.transport, timeout=15.0, follow_redirects=False,
                                         trust_env=False) as client:
                resp = await client.request(method, url, **kw)
        except httpx.TimeoutException:
            raise ChannelError(f"{self.name}_timeout", "Sağlayıcıya ulaşılamadı (zaman aşımı).") from None
        except httpx.HTTPError:
            raise ChannelError(f"{self.name}_unreachable", "Sağlayıcıya ulaşılamadı (ağ hatası).") from None
        if 300 <= resp.status_code < 400:  # redirects are never followed (the bearer token must not travel on)
            raise ChannelError(f"{self.name}_redirect", "Sağlayıcı yönlendirme döndürdü; izlenmedi.")
        return resp
