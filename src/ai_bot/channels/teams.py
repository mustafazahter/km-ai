"""Microsoft Teams through Azure Bot Service (Bot Framework): personal chat only in v1.

POST /channels/teams/messages
    The Authorization bearer JWT is validated like the Bot Framework docs say, with ``cryptography`` only:
    RS256, key (by ``kid``) from the OpenID metadata ``https://login.botframework.com/v1/.well-known/openidconfiguration``
    -> ``jwks_uri`` (https only, cached, refreshed on an unknown ``kid`` at most every 30 s),
    ``iss == https://api.botframework.com``, ``aud == TEAMS_APP_ID``, ``exp``/``nbf`` with 60 s leeway, and the
    ``serviceurl`` claim must equal the activity's ``serviceUrl``.
SSRF guard of the reply: ``serviceUrl`` must be https, port 443, a DNS name (no IP, no credentials) whose host
    ends with a Bot Framework suffix; the bearer token for the reply is never sent anywhere else. (A
    ``*.trafficmanager.net`` name can be registered by anyone, so the signed ``serviceurl`` claim is what makes
    that suffix safe: only the Bot Connector can produce a token that names it.)
Outbound token: client_credentials at login.microsoftonline.com, scope https://api.botframework.com/.default.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from stack_kit import register_secret

from .base import Channel, ChannelError, http_problem, log, read_body, too_old

OPENID_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
ISSUER = "https://api.botframework.com"
LEEWAY = 60
KEYS_TTL = 12 * 3600
UNKNOWN_KID_REFRESH_GAP = 30
SERVICE_SUFFIXES = (".botframework.com", ".trafficmanager.net", ".azure.com")
SMBA_SUFFIXES = (".microsoft.com", ".microsoft.us", ".azure.us")  # smba.* hosts of sovereign clouds
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
FRACTION = re.compile(r"(\.\d{6})\d+")
AT_RE = re.compile(r"<at>.*?</at>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]{1,200}>")


class JwtError(Exception):
    """Token refused; ``reason`` is a short code for tests/logs (never shown to the caller)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def parse_jwt(token: str) -> tuple[dict, dict, bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise JwtError("malformed")
    try:
        header = json.loads(b64url(parts[0]))
        payload = json.loads(b64url(parts[1]))
        sig = b64url(parts[2])
    except (ValueError, TypeError):
        raise JwtError("malformed") from None
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise JwtError("malformed")
    return header, payload, f"{parts[0]}.{parts[1]}".encode("ascii"), sig


def check_signature(key, signing_input: bytes, sig: bytes) -> None:
    try:
        key.verify(sig, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise JwtError("bad_signature") from None


def check_claims(payload: dict, app_id: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    if payload.get("iss") != ISSUER:
        raise JwtError("bad_iss")
    aud = payload.get("aud")
    if not app_id or aud != app_id:
        raise JwtError("bad_aud")
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or now > exp + LEEWAY:
        raise JwtError("expired")
    nbf = payload.get("nbf")
    if nbf is not None and (isinstance(nbf, bool) or not isinstance(nbf, (int, float)) or now < nbf - LEEWAY):
        raise JwtError("not_yet_valid")


def claim_service_url(payload: dict) -> str:
    for k, v in payload.items():
        if isinstance(k, str) and k.lower() == "serviceurl" and isinstance(v, str):
            return v
    return ""


def norm_service_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def service_url_ok(url: str) -> bool:
    """https, port 443, DNS name with a Bot Framework suffix; no credentials, query or fragment."""
    try:
        parts = urlsplit(url or "")
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment:
        return False
    if port not in (None, 443):
        return False
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or "." not in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False  # IP literal
    except ValueError:
        pass
    if host.endswith(SERVICE_SUFFIXES):
        return True
    return host.startswith("smba.") and host.endswith(SMBA_SUFFIXES)


def activity_time(value: object) -> float | None:
    """Activity ``timestamp`` (ISO 8601, any fraction length) as unix seconds; ``None`` when absent/unparsable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = FRACTION.sub(r"\1", value.strip())
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class KeyCache:
    """Bot Framework signing keys by ``kid`` (OpenID metadata -> jwks_uri), cached with a TTL."""

    def __init__(self, channel: "Teams"):
        self.channel = channel
        self.keys: dict[str, object] = {}
        self.fetched = 0.0
        self.attempted = 0.0

    async def get(self, kid: str):
        now = time.time()
        stale = now - self.fetched > KEYS_TTL
        if (stale or kid not in self.keys) and now - self.attempted > UNKNOWN_KID_REFRESH_GAP:
            await self.refresh()
        return self.keys.get(kid)

    async def refresh(self) -> None:
        self.attempted = time.time()
        try:
            meta = await self.channel.http("GET", OPENID_URL)
            if meta.status_code != 200:
                raise JwtError("keys_unavailable")
            uri = (meta.json() or {}).get("jwks_uri")
            if not isinstance(uri, str) or not uri.startswith("https://"):
                raise JwtError("keys_unavailable")
            resp = await self.channel.http("GET", uri)
            if resp.status_code != 200:
                raise JwtError("keys_unavailable")
            keys: dict[str, object] = {}
            for jwk in (resp.json() or {}).get("keys") or []:
                if not isinstance(jwk, dict) or jwk.get("kty") != "RSA" or not jwk.get("kid"):
                    continue
                n = int.from_bytes(b64url(jwk["n"]), "big")
                e = int.from_bytes(b64url(jwk["e"]), "big")
                keys[str(jwk["kid"])] = RSAPublicNumbers(e, n).public_key()
        except (ChannelError, ValueError, KeyError, TypeError):
            raise JwtError("keys_unavailable") from None
        if keys:
            self.keys, self.fetched = keys, time.time()


class Teams(Channel):
    name = "teams"
    title = "Microsoft Teams"
    prefix = "TEAMS"
    required = ("TEAMS_APP_ID", "TEAMS_APP_PASSWORD")
    max_chunk = 4000
    paths = {"messages": "/channels/teams/messages"}
    fields = [
        {"name": "TEAMS_APP_ID", "label": "Microsoft uygulama kimliği (App ID)", "kind": "text",
         "hint": "Azure Bot kaynağı > Configuration > Microsoft App ID.", "required": True},
        {"name": "TEAMS_APP_PASSWORD", "label": "İstemci gizli anahtarı (client secret)", "kind": "secret",
         "hint": "Configuration > Manage Password > Certificates & secrets > New client secret (değeri bir kez gösterilir).",
         "required": True},
        {"name": "TEAMS_TENANT_ID", "label": "Kiracı kimliği (Tenant ID)", "kind": "text",
         "hint": "Tek kiracılı bot için zorunludur (Configuration sayfasında). Boş bırakılırsa botframework.com kullanılır."},
        {"name": "TEAMS_ALLOWED_USERS", "label": "İzinli kullanıcılar", "kind": "list",
         "hint": "Kullanıcının Entra Object ID'si (GUID). Boş liste = kimse yazamaz. Kimlik bilmiyorsanız bota yazdırıp aşağıdaki 'Reddedilen son gönderenler' listesinden ekleyin."},
    ]

    def __init__(self, manager):
        super().__init__(manager)
        self.keys = KeyCache(self)
        self._token: tuple[str, float] | None = None

    def allow_setting(self) -> str:
        return "TEAMS_ALLOWED_USERS"

    def normalise(self, value: str) -> str:
        return value.strip().lower()

    def valid_entry(self, value: str) -> bool:
        return bool(GUID.match(value.strip()))

    def entry_hint(self) -> str:
        return "Teams kullanıcısı Entra Object ID (GUID) ile eklenir, ör. 00000000-0000-0000-0000-000000000000."

    # ------------------------------------------------------------------ inbound
    async def authenticate(self, auth_header: str | None, activity_service_url: str) -> dict:
        """Full Bot Framework token check. Returns the claims or raises :class:`JwtError`."""
        if not auth_header or not auth_header.lower().startswith("bearer "):
            raise JwtError("no_bearer")
        header, payload, signing_input, sig = parse_jwt(auth_header[7:].strip())
        if header.get("alg") != "RS256":
            raise JwtError("bad_alg")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise JwtError("no_kid")
        key = await self.keys.get(kid)
        if key is None:
            raise JwtError("unknown_kid")
        check_signature(key, signing_input, sig)
        check_claims(payload, self.cfg.text("TEAMS_APP_ID"))
        claimed = claim_service_url(payload)
        if not claimed or norm_service_url(claimed) != norm_service_url(activity_service_url):
            raise JwtError("service_url_mismatch")
        return payload

    def register(self, router: APIRouter) -> None:
        @router.post(self.paths["messages"])
        async def messages(request: Request):
            self.require_enabled()
            raw = await read_body(request)
            try:
                activity = json.loads(raw)
            except (ValueError, RecursionError):
                raise HTTPException(400, "bad json") from None
            if not isinstance(activity, dict):
                raise HTTPException(400, "bad json")
            service_url = str(activity.get("serviceUrl") or "")
            try:
                await self.authenticate(request.headers.get("authorization"), service_url)
            except JwtError as exc:
                log.info("teams: token refused (%s)", exc.reason)
                raise HTTPException(401, "unauthorized") from None
            if not service_url_ok(service_url):
                raise HTTPException(403, "service url not allowed")
            self.handle_activity(activity, service_url)
            return JSONResponse({})

    def handle_activity(self, act: dict, service_url: str) -> bool:
        if act.get("type") != "message" or act.get("channelId") != "msteams":
            return False
        tenant = self.cfg.text("TEAMS_TENANT_ID")
        if tenant:
            data = act.get("channelData") if isinstance(act.get("channelData"), dict) else {}
            seen = (data.get("tenant") or {}).get("id") if isinstance(data.get("tenant"), dict) else None
            if not isinstance(seen, str) or seen.strip().lower() != tenant.lower():
                return False  # another Microsoft tenant talking to our bot
        if too_old(activity_time(act.get("timestamp"))):
            return False  # replay
        conv = act.get("conversation") if isinstance(act.get("conversation"), dict) else {}
        if conv.get("isGroup") or conv.get("conversationType") != "personal":
            return False  # v1: personal chat only
        sender = act.get("from") if isinstance(act.get("from"), dict) else {}
        if sender.get("role") == "bot":
            return False
        conv_id = conv.get("id")
        text = act.get("text")
        if not isinstance(conv_id, str) or not conv_id or not isinstance(text, str):
            return False
        text = TAG_RE.sub("", AT_RE.sub("", text)).strip()
        rcpt = act.get("recipient") if isinstance(act.get("recipient"), dict) else {}
        ctx = {"serviceUrl": service_url, "conversationId": conv_id, "botId": str(rcpt.get("id") or ""),
               "userId": str(sender.get("id") or ""), "replyTo": str(act.get("id") or "")}
        inb = self.accept([str(sender.get("aadObjectId") or "")], text,  # Entra object id only: from.id is channel scoped
                          str(act.get("id") or "") or None, ctx)
        if inb is None:
            return False
        self.m.save_ref("teams", inb.sender, {k: ctx[k] for k in ("serviceUrl", "conversationId", "botId", "userId")})
        self.spawn(inb)
        return True

    # ------------------------------------------------------------------ outbound
    async def bot_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        app_id, password = self.cfg.text("TEAMS_APP_ID"), self.cfg.secret("TEAMS_APP_PASSWORD")
        if not app_id or not password:
            raise ChannelError("teams_not_configured", "Teams uygulama kimliği ve parolası eksik.")
        tenant = self.cfg.text("TEAMS_TENANT_ID") or "botframework.com"
        if not re.fullmatch(r"[A-Za-z0-9.\-]{1,100}", tenant):
            raise ChannelError("teams_bad_tenant", "Kiracı kimliği geçersiz.")
        resp = await self.http("POST", f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                               data={"grant_type": "client_credentials", "client_id": app_id, "client_secret": password,
                                     "scope": "https://api.botframework.com/.default"})
        if resp.status_code in (400, 401):
            raise ChannelError("teams_auth", "Microsoft kimlik bilgilerini reddetti: uygulama kimliği, gizli anahtar veya kiracı kimliği yanlış.")
        if resp.status_code >= 400:
            raise http_problem(self.name, resp.status_code)
        try:
            data = resp.json()
            token, ttl = str(data["access_token"]), float(data.get("expires_in") or 3000)
        except (ValueError, KeyError, TypeError):
            raise ChannelError("teams_bad_token", "Microsoft beklenmeyen bir yanıt verdi.") from None
        register_secret(token)
        self._token = (token, time.time() + ttl)
        return token

    async def send(self, ctx: dict, text: str) -> None:
        service_url = str(ctx.get("serviceUrl") or "")
        if not service_url_ok(service_url):
            raise ChannelError("teams_service_url", "Teams yanıt adresi güvenli görünmüyor; yanıt gönderilmedi.")
        token = await self.bot_token()
        activity = {"type": "message", "text": text,
                    "from": {"id": ctx.get("botId", "")}, "recipient": {"id": ctx.get("userId", "")},
                    "conversation": {"id": ctx["conversationId"]}}
        if ctx.get("replyTo"):
            activity["replyToId"] = ctx["replyTo"]
        base = service_url if service_url.endswith("/") else service_url + "/"
        resp = await self.http("POST", f"{base}v3/conversations/{quote(ctx['conversationId'], safe='')}/activities",
                               headers={"Authorization": f"Bearer {token}"}, json=activity)
        if resp.status_code in (401, 403):
            self._token = None
        if resp.status_code >= 400:
            raise http_problem(self.name, resp.status_code)

    def target_for(self, chat_id: int, external: str) -> dict | None:
        ref = self.m.load_ref("teams", external)
        return dict(ref) if ref else None

    async def test(self) -> str:
        await self.bot_token()
        self.note_ok()
        return "Microsoft'tan bot belirteci alındı: uygulama kimliği ve gizli anahtar doğru görünüyor."
