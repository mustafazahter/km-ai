"""Slack Events API: signed events (message.im, app_mention), replies with chat.postMessage.

POST /channels/slack/events   X-Slack-Signature = v0=HMAC-SHA256("v0:{ts}:{raw body}") with the signing secret
                              (constant time; |now - ts| > 300 s is refused). url_verification is answered only
                              after the signature is valid. Retries (X-Slack-Retry-Num) get 200 without work.
Confirmations are always sent to the user's DM, never to a public channel.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .base import Channel, ChannelError, http_problem, read_body

API = "https://slack.com/api"
MAX_SKEW = 300
USER_RE = re.compile(r"^[UW][A-Z0-9]{2,20}$")
MENTION_RE = re.compile(r"<@[UW][A-Z0-9]+(\|[^>]*)?>")


def signature_ok(secret: str, timestamp: str | None, raw: bytes, header: str | None, now: float | None = None) -> bool:
    if not secret or not timestamp or not header:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW:
        return False
    base = b"v0:" + str(ts).encode() + b":" + raw
    expected = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), header.strip().lower().encode("utf-8", "replace"))


class Slack(Channel):
    name = "slack"
    title = "Slack"
    prefix = "SLACK"
    required = ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET")
    max_chunk = 3000
    paths = {"events": "/channels/slack/events"}
    fields = [
        {"name": "SLACK_BOT_TOKEN", "label": "Bot kullanıcı belirteci (xoxb-…)", "kind": "secret",
         "hint": "api.slack.com/apps > uygulamanız > OAuth & Permissions > Bot User OAuth Token.", "required": True},
        {"name": "SLACK_SIGNING_SECRET", "label": "İmza anahtarı (Signing Secret)", "kind": "secret",
         "hint": "Basic Information > App Credentials > Signing Secret.", "required": True},
        {"name": "SLACK_TEAM_ID", "label": "Çalışma alanı kimliği (Team ID, isteğe bağlı)", "kind": "text",
         "hint": "T… ile başlar (Slack'te çalışma alanı adı > Ayarlar). Doluysa başka çalışma alanlarından gelen olaylar reddedilir; önerilir."},
        {"name": "SLACK_ALLOWED_USERS", "label": "İzinli kullanıcı kimlikleri", "kind": "list",
         "hint": "U… ile başlar (profil > ⋯ > Üye kimliğini kopyala). Boş liste = kimse yazamaz."},
    ]

    def allow_setting(self) -> str:
        return "SLACK_ALLOWED_USERS"

    def normalise(self, value: str) -> str:
        return value.strip().upper()

    def valid_entry(self, value: str) -> bool:
        return bool(USER_RE.match(value.strip().upper()))

    def entry_hint(self) -> str:
        return "Slack kullanıcı kimliği U ile başlar (ör. U024BE7LH)."

    def confirm_ctx(self, ctx: dict, sender: str) -> dict:
        return {"channel": sender}  # DM with the user, whatever channel the request came from

    # ------------------------------------------------------------------ inbound
    def register(self, router: APIRouter) -> None:
        @router.post(self.paths["events"])
        async def events(request: Request):
            self.require_enabled()
            raw = await read_body(request)
            if not signature_ok(self.cfg.secret("SLACK_SIGNING_SECRET"), request.headers.get("x-slack-request-timestamp"),
                                raw, request.headers.get("x-slack-signature")):
                raise HTTPException(403, "bad signature")
            try:
                data = json.loads(raw)
            except (ValueError, RecursionError):
                raise HTTPException(400, "bad json") from None
            if not isinstance(data, dict):
                raise HTTPException(400, "bad json")
            if data.get("type") == "url_verification":
                challenge = data.get("challenge")
                if not isinstance(challenge, str) or len(challenge) > 300:
                    raise HTTPException(400, "bad challenge")
                return PlainTextResponse(challenge)
            if request.headers.get("x-slack-retry-num"):
                return JSONResponse({"ok": True, "retry": "ignored"})
            self.handle_event(data)
            return JSONResponse({"ok": True})

    def handle_event(self, data: dict) -> bool:
        if data.get("type") != "event_callback":
            return False
        ev = data.get("event")
        if not isinstance(ev, dict):
            return False
        kind = ev.get("type")
        if kind not in ("message", "app_mention"):
            return False
        if ev.get("bot_id") or ev.get("subtype"):
            return False
        if kind == "message" and ev.get("channel_type") != "im":
            return False
        team = self.cfg.text("SLACK_TEAM_ID")
        if team:
            auths = data.get("authorizations")
            if data.get("team_id") != team or (isinstance(auths, list) and auths and not any(
                    isinstance(a, dict) and a.get("team_id") == team for a in auths)):
                return False  # another workspace
        user, text, channel = ev.get("user"), ev.get("text"), ev.get("channel")
        if not isinstance(user, str) or not isinstance(text, str) or not isinstance(channel, str):
            return False
        thread = ev.get("thread_ts") or (ev.get("ts") if kind == "app_mention" else None)
        ctx = {"channel": channel}
        if isinstance(thread, str) and thread:
            ctx["thread_ts"] = thread
        public_mention = kind == "app_mention"
        if public_mention:
            # an answer can contain memory or tool output: never post it where the whole channel reads it
            ctx = {"channel": user}
        text = MENTION_RE.sub("", text).strip()
        msg_id = str(data.get("event_id") or "") or None
        inb = self.accept([user], text, msg_id, ctx)
        if inb is None:
            return False
        self.spawn(inb)
        if public_mention:
            self.m.spawn(self._note_dm(channel, thread if isinstance(thread, str) else None))
        return True

    async def _note_dm(self, channel: str, thread: str | None) -> None:
        """One neutral line in the channel; the real answer goes to the asker's DM."""
        try:
            ctx = {"channel": channel}
            if thread:
                ctx["thread_ts"] = thread
            await self.send(ctx, "Yanıtı sana özel mesajla (DM) gönderdim.")
        except ChannelError:
            pass

    # ------------------------------------------------------------------ outbound
    async def _call(self, method: str, payload: dict | None = None) -> dict:
        token = self.cfg.secret("SLACK_BOT_TOKEN")
        if not token:
            raise ChannelError("slack_not_configured", "Slack bot belirteci eksik.")
        resp = await self.http("POST", f"{API}/{method}", headers={"Authorization": f"Bearer {token}"}, json=payload or {})
        if resp.status_code >= 400:
            raise http_problem(self.name, resp.status_code)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not data.get("ok"):
            err = str(data.get("error") if isinstance(data, dict) else "")
            if err in ("invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired"):
                raise ChannelError("slack_auth", "Slack belirteci geçersiz veya iptal edilmiş.")
            if err in ("missing_scope", "not_allowed_token_type"):
                raise ChannelError("slack_scope", "Slack uygulamasında gerekli izin (chat:write, im:history…) eksik.")
            if err in ("channel_not_found", "not_in_channel", "is_archived"):
                raise ChannelError("slack_channel", "Slack kanalı/kişisi bulunamadı veya bot orada değil.")
            if err == "ratelimited":
                raise ChannelError("slack_rate_limited", "Slack istek sınırına ulaştı; biraz sonra tekrar deneyin.")
            raise ChannelError("slack_error", "Slack isteği kabul etmedi.")
        return data

    async def send(self, ctx: dict, text: str) -> None:
        payload = {"channel": ctx["channel"], "text": text}
        if ctx.get("thread_ts"):
            payload["thread_ts"] = ctx["thread_ts"]
        await self._call("chat.postMessage", payload)

    def target_for(self, chat_id: int, external: str) -> dict | None:
        return {"channel": external}

    async def test(self) -> str:
        await self._call("auth.test")
        self.note_ok()
        return "Slack belirteci geçerli. Olay adresini (Event Subscriptions) Slack'te ayrıca doğrulamanız gerekir."
