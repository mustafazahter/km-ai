"""WhatsApp Cloud API (Meta): webhook verification, signed inbound messages, text replies.

GET  /channels/whatsapp/webhook   hub.mode=subscribe + hub.verify_token (constant time) -> hub.challenge
POST /channels/whatsapp/webhook   X-Hub-Signature-256 = sha256 HMAC of the RAW body with the app secret;
                                  checked BEFORE the body is parsed; 200 is returned at once, work runs in a task.
Outbound: https://graph.facebook.com/v21.0/{phone_number_id}/messages (bearer token). Free-form text only works
inside WhatsApp's 24-hour window after the user's last message (no template messages in v1).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .base import Channel, ChannelError, http_problem, read_body, safe_equal, too_old

GRAPH = "https://graph.facebook.com/v21.0"
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,200}$")


def signature_ok(app_secret: str, raw: bytes, header: str | None) -> bool:
    if not app_secret or not header:
        return False
    header = header.strip()
    if not header.lower().startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), header[7:].strip().lower().encode("utf-8", "replace"))


def digits(value: str) -> str:
    d = re.sub(r"\D", "", value or "")
    return d[2:] if d.startswith("00") else d


class WhatsApp(Channel):
    name = "whatsapp"
    title = "WhatsApp"
    prefix = "WHATSAPP"
    required = ("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN")
    max_chunk = 4000  # the API accepts 4096
    paths = {"webhook": "/channels/whatsapp/webhook"}
    fields = [
        {"name": "WHATSAPP_PHONE_NUMBER_ID", "label": "Telefon numarası kimliği (Phone number ID)", "kind": "text",
         "hint": "Meta for Developers > WhatsApp > API Setup sayfasında, gönderen numaranın altında yazar.", "required": True},
        {"name": "WHATSAPP_TOKEN", "label": "Erişim belirteci (Access token)", "kind": "secret",
         "hint": "Kalıcı olması için Business Settings > System Users altından üretin.", "required": True},
        {"name": "WHATSAPP_APP_SECRET", "label": "Uygulama gizli anahtarı (App secret)", "kind": "secret",
         "hint": "App settings > Basic sayfasında. Gelen isteklerin imzasını doğrulamak için kullanılır.", "required": True},
        {"name": "WHATSAPP_VERIFY_TOKEN", "label": "Doğrulama kodu (Verify token)", "kind": "secret", "generate": True,
         "hint": "Aşağıdaki Üret düğmesiyle oluşturun; aynı değeri Meta'daki webhook ekranına yapıştırın.", "required": True},
        {"name": "WHATSAPP_ALLOWED_NUMBERS", "label": "İzinli numaralar", "kind": "list",
         "hint": "Ülke koduyla, ör. 905551112233. Boş liste = kimse yazamaz."},
    ]

    def allow_setting(self) -> str:
        return "WHATSAPP_ALLOWED_NUMBERS"

    def normalise(self, value: str) -> str:
        return digits(value)

    def valid_entry(self, value: str) -> bool:
        return 6 <= len(digits(value)) <= 15

    def entry_hint(self) -> str:
        return "Numara ülke koduyla, yalnız rakamlarla yazılmalı (ör. 905551112233)."

    # ------------------------------------------------------------------ inbound
    def register(self, router: APIRouter) -> None:
        path = self.paths["webhook"]

        @router.get(path)
        async def verify(request: Request):
            self.require_enabled()
            q = request.query_params
            expected = self.cfg.secret("WHATSAPP_VERIFY_TOKEN")
            challenge = q.get("hub.challenge") or ""
            if (q.get("hub.mode") != "subscribe" or not expected
                    or not safe_equal(q.get("hub.verify_token") or "", expected)):
                raise HTTPException(403, "forbidden")
            if not CHALLENGE_RE.match(challenge):
                raise HTTPException(400, "bad challenge")
            return PlainTextResponse(challenge)

        @router.post(path)
        async def receive(request: Request):
            self.require_enabled()
            raw = await read_body(request)
            if not signature_ok(self.cfg.secret("WHATSAPP_APP_SECRET"), raw, request.headers.get("x-hub-signature-256")):
                raise HTTPException(403, "bad signature")
            try:
                data = json.loads(raw)
            except (ValueError, RecursionError):
                raise HTTPException(400, "bad json") from None
            self.handle_payload(data)
            return JSONResponse({"ok": True})

    def handle_payload(self, data: object) -> int:
        """Queue every acceptable text message; returns how many were queued."""
        queued = 0
        if not isinstance(data, dict):
            return 0
        phone_id = self.cfg.text("WHATSAPP_PHONE_NUMBER_ID")
        for entry in data.get("entry") or []:
            for change in (entry.get("changes") or []) if isinstance(entry, dict) else []:
                value = change.get("value") if isinstance(change, dict) else None
                if not isinstance(value, dict):
                    continue
                meta = value.get("metadata") or {}
                if phone_id and str(meta.get("phone_number_id") or "") != phone_id:
                    continue  # another number of the same Meta app
                for msg in value.get("messages") or []:
                    if not isinstance(msg, dict) or msg.get("type") != "text":
                        continue
                    sender = digits(str(msg.get("from") or ""))
                    body = (msg.get("text") or {}).get("body") if isinstance(msg.get("text"), dict) else None
                    if not sender or not isinstance(body, str):
                        continue
                    try:
                        sent_at = float(msg["timestamp"]) if msg.get("timestamp") not in (None, "") else None
                    except (TypeError, ValueError):
                        sent_at = None
                    if too_old(sent_at):
                        continue  # replayed old delivery (a signed body can be re-sent by anyone who captured it)
                    inb = self.accept([sender], body, str(msg.get("id") or "") or None, {"to": sender})
                    if inb is not None:
                        self.spawn(inb)
                        queued += 1
        return queued

    # ------------------------------------------------------------------ outbound
    async def send(self, ctx: dict, text: str) -> None:
        phone_id = self.cfg.text("WHATSAPP_PHONE_NUMBER_ID")
        token = self.cfg.secret("WHATSAPP_TOKEN")
        if not phone_id or not token:
            raise ChannelError("whatsapp_not_configured", "WhatsApp ayarları eksik.")
        resp = await self.http("POST", f"{GRAPH}/{phone_id}/messages",
                               headers={"Authorization": f"Bearer {token}"},
                               json={"messaging_product": "whatsapp", "to": ctx["to"], "type": "text",
                                     "text": {"body": text[:4096], "preview_url": False}})
        if resp.status_code >= 400:
            raise http_problem(self.name, resp.status_code)

    def target_for(self, chat_id: int, external: str) -> dict | None:
        return {"to": external}

    async def test(self) -> str:
        phone_id = self.cfg.text("WHATSAPP_PHONE_NUMBER_ID")
        token = self.cfg.secret("WHATSAPP_TOKEN")
        if not phone_id or not token:
            raise ChannelError("whatsapp_not_configured", "Önce telefon numarası kimliğini ve erişim belirtecini kaydedin.")
        resp = await self.http("GET", f"{GRAPH}/{phone_id}", params={"fields": "display_phone_number"},
                               headers={"Authorization": f"Bearer {token}"})
        if resp.status_code >= 400:
            raise http_problem(self.name, resp.status_code)
        self.note_ok()
        return "Belirteç geçerli ve numaraya erişilebiliyor. Webhook'u Meta tarafında ayrıca doğrulamanız gerekir."
