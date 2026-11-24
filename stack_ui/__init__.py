"""Small admin UI for stacks, protected by Komuta sign-in (or a password when self-hosted).

    from stack_kit import create_app
    from stack_ui import mount_ui

    app = create_app(stack)
    mount_ui(app, stack, title="My stack", static_dir="ui", api_router=my_router)

* ``/ui/``            the stack's own static pages (plain HTML/CSS/JS, no build step)
* ``/ui/_kit/``       shared design system: ``komuta-ui.css``, ``komuta-ui.js``, ``icons.svg``, ``example/``
* ``/ui/api/*``       protected JSON API (auth + CSRF on state-changing methods, ``Cache-Control: no-store``)
* ``/ui/login``       password mode only

Auth (``UI_AUTH``): ``komuta`` (default; verifies the ``x-komuta-identity`` ES256 JWT),
``password`` (``UI_PASSWORD``, signed session cookie) or ``off`` (only with ``UI_ALLOW_INSECURE=1``).
Anything misconfigured closes the UI (403) instead of opening it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from stack_kit.app import FailLimiter
from stack_kit.redact import REVEAL_ATTR, register_secret
from stack_kit.gate import SURFACES as GATE_SURFACES
from stack_kit.registry import Stack, get_stack
from stack_kit.settings import env_bool, env_float, env_int, env_list, env_str
from . import identity as _identity
from .identity import DEFAULT_KEYS_URL, IdentityError, KeySet, KomutaIdentity, verify_identity
from .auth import (
    DISABLE_PHRASE, MIN_PASSWORD, SCRYPT_N, SCRYPT_P, SCRYPT_R, AuthStore, new_setup_code, normalize_code,
    password_problem,
)
from .quickstart import CLIENTS as QUICK_CLIENTS, build_prompt, safe_base_url, safe_host
from .stores import SecretStore, Setting, SettingsStore, data_dir, mask, secret_store, settings_store

log = logging.getLogger("stack_ui")

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
KIT_URL = "/ui/_kit"  # URL prefix of ASSETS_DIR
SESSION_COOKIE = "skui_session"
CSRF_COOKIE = "skui_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

__all__ = [
    "mount_ui", "inline_icons", "build_id", "stamp_build", "UIConfig", "UIUser", "UIRuntime", "UIAuthError", "safe_next", "KOMUTA_SETUP_STEPS", "current_user", "require_csrf", "settings_router",
    "secrets_router", "SecretStore", "SettingsStore", "Setting", "secret_store", "settings_store", "mask",
    "data_dir", "build_prompt", "ASSETS_DIR", "KIT_URL", "assets_dir", "details_html", "AuthStore", "password_problem", "DISABLE_PHRASE", "KomutaIdentity", "IdentityError", "verify_identity", "KeySet", "DEFAULT_KEYS_URL",
]


# --------------------------------------------------------------------------- config / user


@dataclass
class UIConfig:
    """UI settings. :meth:`from_env` reads the ``UI_*`` variables.

    ``auth=None`` means "not configured": ``mount_ui(default_auth=...)`` decides (default ``komuta``).
    """

    auth: str | None = None  # komuta | password | auto | off
    allow_insecure: bool = False
    identity_keys_url: str = DEFAULT_KEYS_URL
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_emails: list[str] = field(default_factory=list)
    password: str | None = None
    session_secret: str | None = None
    session_ttl: int = 12 * 3600
    cookie_secure: str = "auto"  # auto | true | false
    login_fail_limit: int = 5
    login_fail_window: float = 300.0
    key_cache_ttl: float = 300.0
    leeway: int = 60
    force_protection: bool = False
    setup_code: str | None = None
    setup_fail_limit: int = 5
    setup_fail_window: float = 900.0
    setup_lock_after: int = 200
    setup_lock_seconds: float = 900.0
    setup_code_relog_seconds: float = 300.0
    reset_code_ttl: float = 900.0            # a password-reset code is valid this long (one use)
    reset_code_relog_seconds: float = 30.0   # the same code is written to the log again at most this often
    trust_forwarded_host: bool = False
    auth_path: str | None = None  # default DATA_DIR/ui-auth.json
    public_url: str | None = None  # UI_PUBLIC_URL: trusted public address (quick-connect prompt)
    keys_transport: httpx.AsyncBaseTransport | None = None  # tests

    @classmethod
    def from_env(cls) -> "UIConfig":
        auth = env_str("UI_AUTH")
        return cls(
            auth=auth.lower() if auth else None,
            allow_insecure=env_bool("UI_ALLOW_INSECURE", False),
            identity_keys_url=env_str("UI_IDENTITY_KEYS_URL", DEFAULT_KEYS_URL) or DEFAULT_KEYS_URL,
            allowed_hosts=env_list("UI_ALLOWED_HOSTS"),
            allowed_emails=env_list("UI_ALLOWED_EMAILS"),
            password=env_str("UI_PASSWORD"),
            session_secret=env_str("UI_SESSION_SECRET"),
            session_ttl=env_int("UI_SESSION_TTL", 12 * 3600),
            cookie_secure=(env_str("UI_COOKIE_SECURE", "auto") or "auto").lower(),
            force_protection=env_bool("UI_FORCE_PROTECTION", False),
            setup_code=env_str("UI_SETUP_CODE"),
            public_url=env_str("UI_PUBLIC_URL"),
            trust_forwarded_host=env_bool("UI_TRUST_FORWARDED_HOST", False),
            setup_code_relog_seconds=env_float("UI_SETUP_CODE_RELOG_SECONDS", 300.0),
            reset_code_ttl=env_float("UI_RESET_CODE_TTL", 900.0),
            reset_code_relog_seconds=env_float("UI_RESET_CODE_RELOG_SECONDS", 30.0),
        )


@dataclass(frozen=True)
class UIUser:
    """The UI user. ``method``: ``komuta`` | ``password`` | ``off`` | ``unprotected`` (protection switched off)."""

    id: str
    email: str | None
    method: str
    session: str | None = None

    @property
    def label(self) -> str:
        return self.email or self.id

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "method": self.method}


class UIAuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _host_only(value: str | None) -> str | None:
    if not value:
        return None
    value = value.split(",")[0].strip().lower()
    if value.startswith("["):  # IPv6 literal
        return value.split("]")[0] + "]"
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _same(a: str, b: str) -> bool:
    """Constant-time string comparison that does not leak length via early exit."""
    return hmac.compare_digest(hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest())


# --------------------------------------------------------------------------- runtime


class UIRuntime:
    """State of one mounted UI (``app.state.ui``)."""

    def __init__(self, stack: Stack, config: UIConfig, title: str, static_dir: Path | None, index: str,
                 default_auth: str = "komuta"):
        self.stack = stack
        self.config = config
        config.auth = (config.auth or default_auth or "komuta").lower()
        self.title = title
        self.static_dir = static_dir
        self.index = index
        self.disabled_reason: str | None = None
        self.keys = KeySet(config.identity_keys_url, ttl=config.key_cache_ttl, transport=config.keys_transport)
        self.login_limiter = FailLimiter(config.login_fail_limit, config.login_fail_window)
        self.setup_limiter = FailLimiter(config.setup_fail_limit, config.setup_fail_window)
        self.setup_failures = 0
        self.setup_locked_until = 0.0
        self.reset_limiter = FailLimiter(config.setup_fail_limit, config.setup_fail_window)
        self.reset_failures = 0
        self.reset_locked_until = 0.0
        self.reset_code: str | None = None
        self.reset_expires = 0.0
        self.reset_logged_at = 0.0  # time.monotonic() of the last log line with the reset code
        self.revoked: dict[str, float] = {}
        self.started = time.time()
        self.store = AuthStore(config.auth_path)
        self.allowed_emails = {e.strip().lower() for e in config.allowed_emails if e.strip()}
        register_secret(config.password)
        register_secret(config.session_secret)
        self._secret = config.session_secret or self._persisted_session_secret()
        self.setup_code: str | None = None
        self.setup_code_from_env = False
        self.setup_code_logged_at = 0.0  # time.monotonic() of the last log line with the code
        self.fresh_tokens: dict[str, tuple[str, float]] = {}  # session -> (new bridge token, expiry), memory only
        self._check_config()

    # -- configuration
    @property
    def password_source(self) -> str:
        """``env`` (UI_PASSWORD), ``stored`` (set in the UI, scrypt hash) or ``none``."""
        if self.config.password:
            return "env"
        return "stored" if self.store.has_password else "none"

    @property
    def uses_password(self) -> bool:
        return self.config.auth in ("password", "auto")

    @property
    def setup_required(self) -> bool:
        return self.uses_password and self.password_source == "none" and not self.disabled_reason

    @property
    def protection_enabled(self) -> bool:
        return True if self.config.force_protection else self.store.protection

    def _check_config(self) -> None:
        cfg = self.config
        if cfg.auth not in ("komuta", "password", "auto", "off"):
            self.disabled_reason = "UI_AUTH must be komuta, password, auto or off"
        elif cfg.auth == "komuta" and not _identity.HAVE_CRYPTOGRAPHY:
            self.disabled_reason = "UI_AUTH=komuta needs the 'cryptography' package (pip install 'komuta-stack-ui')"
        elif self.uses_password and cfg.password is not None and len(cfg.password) < 8:
            self.disabled_reason = "UI_PASSWORD must be at least 8 characters"
        elif cfg.auth == "off" and not cfg.allow_insecure:
            self.disabled_reason = "UI_AUTH=off is refused unless UI_ALLOW_INSECURE=1"
        if self.disabled_reason:
            log.error("admin UI is CLOSED: %s", self.disabled_reason)
            return
        if cfg.auth == "off":
            log.warning("!" * 72)
            log.warning("!!  ADMIN UI WITHOUT AUTHENTICATION (UI_AUTH=off, UI_ALLOW_INSECURE=1).")
            log.warning("!!  Anyone who can reach /ui can change settings and secrets.")
            log.warning("!!  Use only on localhost / a trusted network.")
            log.warning("!" * 72)
        if cfg.auth in ("komuta", "auto") and self.host_warning:
            log.warning("UI_ALLOWED_HOSTS / UI_PUBLIC_URL not set: the Komuta identity audience is checked against the "
                        "request Host header. Set UI_ALLOWED_HOSTS=<public host> to tighten identity checks.")
        if not self.store.protection:
            if cfg.force_protection:
                log.warning("admin UI protection was switched off in the panel, but UI_FORCE_PROTECTION=1 keeps it ON")
            else:
                log.warning("!" * 72)
                log.warning("!!  ADMIN UI PROTECTION IS OFF (switched off in the panel).")
                log.warning("!!  Anyone who knows the link can open /ui. Turn it back on in the panel.")
                log.warning("!" * 72)
        if self.setup_required:
            code = normalize_code(cfg.setup_code) if cfg.setup_code else ""
            if len(code) >= 8:
                self.setup_code = code
                self.setup_code_from_env = True
                log.warning("admin UI first-time setup: use the setup code from UI_SETUP_CODE")
            else:
                self.setup_code = new_setup_code()
                log.warning("admin UI first-time setup required. Kurulum kodu / setup code (one-time, changes on "
                            "restart): %s", self.setup_code, extra={REVEAL_ATTR: True})
                self.setup_code_logged_at = time.monotonic()
            register_secret(self.setup_code)

    # -- passwords
    def check_password(self, password: Any) -> bool:
        if not isinstance(password, str) or not password:
            return False
        if self.config.password:
            return _same(password, self.config.password)
        return self.store.verify(password)

    def relog_setup_code(self) -> bool:
        """Write the setup code to the log again (setup page opened), at most once per
        ``setup_code_relog_seconds`` for the whole process. Never for a UI_SETUP_CODE code. Returns True if logged."""
        if not self.setup_required or not self.setup_code or self.setup_code_from_env:
            return False
        now = time.monotonic()
        if now - self.setup_code_logged_at < self.config.setup_code_relog_seconds:
            return False
        self.setup_code_logged_at = now
        log.warning("Kurulum kodu / setup code: %s (kurulum sayfası açıldı / setup page opened)",
                    self.setup_code, extra={REVEAL_ATTR: True})
        return True

    # -- forgotten password: a one-time code that only the service log shows
    @property
    def reset_available(self) -> bool:
        """A stored (UI-set) password can be reset with a log code; a UI_PASSWORD password only changes in the environment."""
        return self.uses_password and self.password_source == "stored" and not self.disabled_reason

    def issue_reset_code(self) -> bool:
        """Make sure a valid reset code exists and that it is in the log. A still-valid code is never replaced (an
        outsider opening the page cannot rotate it), only written again at most every ``reset_code_relog_seconds``.
        Returns True when a line was written."""
        if not self.reset_available:
            return False
        now_wall, now = time.time(), time.monotonic()
        if self.reset_code and now_wall < self.reset_expires:
            if now - self.reset_logged_at < self.config.reset_code_relog_seconds:
                return False
            self.reset_logged_at = now
            log.warning("Parola sıfırlama kodu / password reset code: %s (sıfırlama sayfası açıldı / reset page opened; "
                        "geçerli: %d dk)", self.reset_code, max(1, int((self.reset_expires - now_wall) // 60)),
                        extra={REVEAL_ATTR: True})
            return True
        self.reset_code = new_setup_code()
        self.reset_expires = now_wall + self.config.reset_code_ttl
        self.reset_logged_at = now
        register_secret(self.reset_code)
        log.warning("Parola sıfırlama kodu / password reset code (one-time, %d min): %s",
                    max(1, int(self.config.reset_code_ttl // 60)), self.reset_code, extra={REVEAL_ATTR: True})
        return True

    def check_reset_code(self, code: Any) -> bool:
        if not self.reset_code or time.time() >= self.reset_expires:
            return False
        return _same(normalize_code(code), normalize_code(self.reset_code))

    def check_setup_code(self, code: Any) -> bool:
        if not self.setup_code:
            return False
        return _same(normalize_code(code), normalize_code(self.setup_code))

    # -- sessions
    def _persisted_session_secret(self) -> str:
        """Random session key kept next to the auth file (mode 600), so a restart or a rolling update does not sign
        everybody out. Without a writable data dir it falls back to a per-process key (sessions end on restart)."""
        path = Path(self.store.path).with_name("ui-session.key")
        try:
            if path.exists():
                value = path.read_text(encoding="utf-8").strip()
                if len(value) >= 32:
                    return value
            value = secrets.token_urlsafe(32)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(value)
            return value
        except OSError:
            log.warning("session key could not be stored in %s; sessions will not survive a restart", path.parent)
            return secrets.token_urlsafe(32)

    def _session_key(self) -> bytes:
        if self.config.password:
            fingerprint = hashlib.sha256(self.config.password.encode()).hexdigest()
        else:
            fingerprint = self.store.password_fingerprint
        # changing the password (or UI_SESSION_SECRET) invalidates every session
        return hmac.new(self._secret.encode(), f"stack-kit-ui-session:{fingerprint}".encode(), hashlib.sha256).digest()

    def make_session(self) -> tuple[str, str, str]:
        sid = secrets.token_urlsafe(16)
        csrf = secrets.token_urlsafe(24)
        payload = _b64e(json.dumps({"sid": sid, "exp": int(time.time()) + self.config.session_ttl,
                                    "v": self.store.session_version}).encode())
        sig = _b64e(hmac.new(self._session_key(), payload.encode(), hashlib.sha256).digest())
        return f"{payload}.{sig}", sid, csrf

    def read_session(self, value: str | None) -> str | None:
        if not value or value.count(".") != 1 or len(value) > 1024:
            return None
        payload, sig = value.split(".")
        expected = _b64e(hmac.new(self._session_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected.encode(), sig.encode()):
            return None
        try:
            data = json.loads(_b64d(payload))
        except (ValueError, RecursionError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("exp"), int) or data["exp"] < time.time():
            return None
        if data.get("v") != self.store.session_version:  # "sign out everywhere" / password change
            return None
        sid = data.get("sid")
        if not isinstance(sid, str) or sid in self.revoked:
            return None
        return sid

    def revoke(self, sid: str) -> None:
        now = time.time()
        self.revoked = {k: v for k, v in self.revoked.items() if v > now}
        self.revoked[sid] = now + self.config.session_ttl

    # -- quick-connect helpers
    FRESH_TOKEN_TTL = 600.0

    def remember_fresh_token(self, user: "UIUser", token: str) -> None:
        now = time.time()
        self.fresh_tokens = {k: v for k, v in self.fresh_tokens.items() if v[1] > now}
        self.fresh_tokens[user.session or user.label] = (token, now + self.FRESH_TOKEN_TTL)

    def take_fresh_token(self, user: "UIUser") -> str | None:
        """One-shot: returns the token created in this session (if still fresh) and forgets it."""
        item = self.fresh_tokens.pop(user.session or user.label, None)
        return item[0] if item and item[1] > time.time() else None

    def has_fresh_token(self, user: "UIUser") -> bool:
        item = self.fresh_tokens.get(user.session or user.label)
        return bool(item and item[1] > time.time())

    @property
    def host_warning(self) -> bool:
        """True when the identity audience falls back to the request's Host (no UI_ALLOWED_HOSTS/UI_PUBLIC_URL)."""
        return not self.config.allowed_hosts and not safe_base_url(self.config.public_url)

    def public_base_url(self, request: Request) -> tuple[str | None, bool]:
        """``(url, verified)``: ``UI_PUBLIC_URL`` or ``UI_ALLOWED_HOSTS`` (verified) else the ``Host`` header (not verified)."""
        configured = safe_base_url(self.config.public_url)
        if configured:
            return configured, True
        for host in self.config.allowed_hosts:
            if safe_host(host):
                return f"https://{safe_host(host)}", True
        host = safe_host(request.headers.get("host"))
        if not host:
            return None, False
        proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        scheme = "https" if request.url.scheme == "https" or proto == "https" else "http"
        return f"{scheme}://{host}", False

    # -- helpers
    def audiences(self, request: Request) -> set[str]:
        """Accepted JWT ``aud`` values: ``UI_ALLOWED_HOSTS`` → host of ``UI_PUBLIC_URL`` → the ``Host`` header.
        ``X-Forwarded-Host`` is client-controlled and only used with ``UI_TRUST_FORWARDED_HOST=1``."""
        if self.config.allowed_hosts:
            return {h.strip().lower() for h in self.config.allowed_hosts if h.strip()}
        public = safe_base_url(self.config.public_url)
        if public:
            return {_host_only(urlsplit(public).netloc) or ""}
        host = None
        if self.config.trust_forwarded_host:
            host = _host_only(request.headers.get("x-forwarded-host"))
        host = host or _host_only(request.headers.get("host"))
        return {host} if host else set()

    def secure_cookie(self, request: Request) -> bool:
        mode = self.config.cookie_secure
        if mode in ("true", "1", "yes"):
            return True
        if mode in ("false", "0", "no"):
            return False
        proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        return request.url.scheme == "https" or proto == "https"

    def origin_ok(self, request: Request) -> bool:
        """``Origin`` (or ``Referer``) must name this host when present."""
        source = request.headers.get("origin") or request.headers.get("referer")
        if not source:
            return True
        if source == "null":
            return False
        host = _host_only(urlsplit(source).netloc)
        allowed = self.audiences(request) | {_host_only(request.headers.get("host")) or ""}
        return bool(host) and host in allowed

    # -- authentication
    async def _komuta_user(self, request: Request) -> UIUser:
        ident = await verify_identity(request.headers.get("x-komuta-identity"), keys=self.keys,
                                      audiences=self.audiences(request), leeway=self.config.leeway)
        if self.allowed_emails and (ident.email or "").lower() not in self.allowed_emails:
            log.info("UI access denied: email not in UI_ALLOWED_EMAILS")
            raise UIAuthError(403, "ui_forbidden", "this account is not allowed to use the admin UI")
        return UIUser(id=ident.sub, email=ident.email, method="komuta", session=ident.sid)

    async def authenticate(self, request: Request) -> UIUser:
        if self.disabled_reason:
            raise UIAuthError(403, "ui_disabled", "admin UI is disabled: " + self.disabled_reason)
        mode = self.config.auth
        if mode == "off":
            return UIUser(id="local", email=None, method="off")
        has_identity = bool(request.headers.get("x-komuta-identity"))
        if mode == "komuta" or (mode == "auto" and has_identity and _identity.HAVE_CRYPTOGRAPHY):
            try:
                return await self._komuta_user(request)
            except IdentityError as exc:
                log.info("UI access denied: %s", exc.reason)
                if mode == "komuta" and self.protection_enabled:
                    raise UIAuthError(403, "ui_unauthorized",
                                      "Komuta sign-in required (no valid x-komuta-identity)") from None
        if not self.protection_enabled:
            return UIUser(id="anonymous", email=None, method="unprotected")
        if mode == "komuta":
            raise UIAuthError(403, "ui_unauthorized", "Komuta sign-in required (no valid x-komuta-identity)")
        if self.setup_required:
            raise UIAuthError(401, "ui_setup_required", "first-time setup: open /ui/setup")
        sid = self.read_session(request.cookies.get(SESSION_COOKIE))
        if sid is None:
            raise UIAuthError(401, "ui_login_required", "sign in at /ui/login")
        return UIUser(id="admin", email=None, method="password", session=sid)


# --------------------------------------------------------------------------- dependencies


async def current_user(request: Request) -> UIUser:
    """FastAPI dependency: the authenticated UI user (raises 401/403 otherwise)."""
    cached = getattr(request.state, "ui_user", None)
    if cached is not None:
        return cached
    runtime: UIRuntime = request.app.state.ui
    user = await runtime.authenticate(request)
    request.state.ui_user = user
    return user


async def require_csrf(request: Request) -> None:
    """FastAPI dependency: on POST/PUT/PATCH/DELETE require ``X-CSRF-Token`` == ``skui_csrf`` cookie
    (double submit) and a same-host ``Origin``/``Referer`` when the browser sends one."""
    if request.method in SAFE_METHODS:
        return
    runtime: UIRuntime = request.app.state.ui
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    header = request.headers.get(CSRF_HEADER) or ""
    if not cookie or not header or not hmac.compare_digest(cookie.encode(), header.encode()):
        raise UIAuthError(403, "csrf_failed", "missing or invalid CSRF token")
    if not runtime.origin_ok(request):
        raise UIAuthError(403, "csrf_failed", "cross-origin request refused")


# --------------------------------------------------------------------------- ready-made routers


async def _json_body(request: Request) -> dict:
    try:
        body = json.loads(await request.body() or b"{}")
    except (ValueError, RecursionError):
        raise UIAuthError(400, "bad_request", "body must be JSON") from None
    if not isinstance(body, dict):
        raise UIAuthError(400, "bad_request", "body must be a JSON object")
    return body


def settings_router(store: SettingsStore, *, prefix: str = "/settings") -> APIRouter:
    """``GET {prefix}``, ``PUT {prefix}/{name}`` ``{"value": ...}``, ``DELETE {prefix}/{name}`` (back to
    env/default), ``GET {prefix}/audit``. Include via ``mount_ui(api_router=...)``."""
    router = APIRouter(prefix=prefix)

    @router.get("")
    async def list_settings(user: UIUser = Depends(current_user)):
        return {"ok": True, "settings": store.all()}

    @router.get("/audit")
    async def audit(limit: int = 100, user: UIUser = Depends(current_user)):
        return {"ok": True, "audit": store.audit(limit)}

    @router.put("/{name}")
    async def put_setting(name: str, request: Request, user: UIUser = Depends(current_user)):
        body = await _json_body(request)
        if name not in store.schema:
            raise UIAuthError(404, "not_found", "unknown setting")
        try:
            value = store.set(name, body.get("value"), actor=user.label)
        except ValueError as exc:
            raise UIAuthError(422, "invalid_value", str(exc)) from None
        return {"ok": True, "setting": {**store.resolve(name), "value": value}}

    @router.delete("/{name}")
    async def reset_setting(name: str, user: UIUser = Depends(current_user)):
        if name not in store.schema:
            raise UIAuthError(404, "not_found", "unknown setting")
        store.reset(name, actor=user.label)
        return {"ok": True, "setting": store.resolve(name)}

    return router


def secrets_router(store: SecretStore, names: Iterable[str], *, prefix: str = "/secrets") -> APIRouter:
    """Write-only secrets: ``GET {prefix}`` (``name, set, preview, source``), ``PUT {prefix}/{name}``
    ``{"value": "..."}``, ``DELETE {prefix}/{name}``. Values are never returned. Only ``names`` are accepted."""
    allowed = list(names)
    router = APIRouter(prefix=prefix)

    @router.get("")
    async def list_secrets(user: UIUser = Depends(current_user)):
        return {"ok": True, "secrets": store.describe(allowed)}

    @router.put("/{name}")
    async def put_secret(name: str, request: Request, user: UIUser = Depends(current_user)):
        if name not in allowed:
            raise UIAuthError(404, "not_found", "unknown secret")
        body = await _json_body(request)
        try:
            store.set(name, body.get("value"))
        except ValueError as exc:
            raise UIAuthError(422, "invalid_value", str(exc)) from None
        log.info("UI secret %s updated by %s", name, user.label)
        return {"ok": True, "secret": store.describe([name])[0]}

    @router.delete("/{name}")
    async def delete_secret(name: str, user: UIUser = Depends(current_user)):
        if name not in allowed:
            raise UIAuthError(404, "not_found", "unknown secret")
        store.delete(name)
        log.info("UI secret %s deleted by %s", name, user.label)
        return {"ok": True, "secret": store.describe([name])[0]}

    return router


# --------------------------------------------------------------------------- mounting


def _safe_file(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``root``; refuses traversal, dotfiles and missing files."""
    rel = rel.lstrip("/")
    if any(part.startswith(".") for part in Path(rel).parts):
        return None
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate if candidate.is_file() else None


def _file_response(path: Path) -> Response:
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix == ".js":
        ctype = "text/javascript"
    if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
        ctype += "; charset=utf-8"
    return Response(path.read_bytes(), media_type=ctype, headers={"Cache-Control": "no-cache"})


def build_id(*roots: Path) -> str:
    """Short fingerprint of every UI file (kit assets and the app's own). Changes with each new version."""
    h = hashlib.sha1()
    for base in roots:
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file() and f.suffix in (".js", ".css", ".html", ".svg", ".json"):
                try:
                    h.update(f.relative_to(base).as_posix().encode())
                    h.update(f.read_bytes())
                except OSError:
                    pass
    return h.hexdigest()[:12]


_HEAD_OPEN = re.compile(r"<head\b[^>]*>", re.I)


def stamp_build(page: str, build: str) -> str:
    """Put ``<meta name="kui-build">`` into a page so the open tab can tell when the server moved on."""
    m = _HEAD_OPEN.search(page)
    if not m or 'name="kui-build"' in page:
        return page
    return page[:m.end()] + f'<meta name="kui-build" content="{build}">' + page[m.end():]


_SPRITE_CACHE: dict[str, tuple[float, str]] = {}
_BODY_OPEN = re.compile(r"<body\b[^>]*>", re.I)
_SPRITE_HREF = re.compile(r'href="(?:/ui/_kit/|/ui/|\./)?icons\.svg#')


def _sprite_symbols(path: Path) -> str:
    """``<symbol>`` elements of an icon sprite file (comments and the outer ``<svg>`` removed); cached by mtime."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    cached = _SPRITE_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    symbols = "".join(re.findall(r"<symbol\b.*?</symbol>", text, flags=re.S))
    _SPRITE_CACHE[str(path)] = (mtime, symbols)
    return symbols


def inline_icons(page: str, root: Path | None = None) -> str:
    """Embed the kit sprite (and the app's own ``icons.svg`` in ``root``) in an HTML page.

    Icons then paint with the first frame instead of waiting for a second request (slow networks showed
    blank icons). ``<use href=".../icons.svg#x">`` becomes ``<use href="#x">``. Pages without a
    ``<body>`` are returned unchanged.
    """
    match = _BODY_OPEN.search(page)
    if not match or "kui-sprite" in page:
        return page
    symbols = _sprite_symbols(ASSETS_DIR / "icons.svg")
    if root is not None:
        symbols += _sprite_symbols(Path(root) / "icons.svg")
    if not symbols:
        return page
    sprite = f'<svg class="kui-sprite" width="0" height="0" aria-hidden="true" focusable="false">{symbols}</svg>'
    page = _SPRITE_HREF.sub('href="#', page)
    match = _BODY_OPEN.search(page)
    return page[: match.end()] + sprite + page[match.end():]


KOMUTA_SETUP_STEPS = [
    "Komuta konsolunda (veya Komuta MCP ile) bu servis için girişi açın. / Turn on sign-in for this service.",
    "Kimliği uygulamaya iletmeyi açın (x-komuta-identity). / Turn on identity forwarding.",
    "Makine yollarını girişsiz açın: /health, /bridge/, /mcp, /hook/ (kendi token'ları sürer). "
    "/ Exempt machine paths from sign-in; they keep their own tokens.",
]


def details_html(steps: list[str] | None = None, code: str | None = None,
                 summary: str = "Ayrıntı / Details") -> str:
    """Escaped ``<details>`` block (steps + error code) for no-access pages; empty when nothing to show."""
    inner = ""
    if steps:
        inner += '<ol class="steps">' + "".join(f"<li>{html.escape(step)}</li>" for step in steps) + "</ol>"
    if code:
        inner += f'<p class="hint">Kod / code: <code>{html.escape(code)}</code></p>'
    if not inner:
        return ""
    return f'<details class="details"><summary>{html.escape(summary)}</summary>{inner}</details>'


def _message_page(title: str, heading: str, message: str, status: int, steps: list[str] | None = None,
                  hint: str | None = None, code: str | None = None) -> Response:
    template = (ASSETS_DIR / "message.html").read_text(encoding="utf-8")
    details = ""
    if hint:
        details += f'<p class="hint t-center">{html.escape(hint)}</p>'
    details += details_html(steps, code)
    body = (template.replace("{{title}}", html.escape(title)).replace("{{heading}}", html.escape(heading))
            .replace("{{message}}", html.escape(message)).replace("{{details}}", details))
    return Response(body, status_code=status, media_type="text/html; charset=utf-8")


def assets_dir() -> Path:
    """Folder of the shared design system (served at ``/ui/_kit/``)."""
    return ASSETS_DIR


def safe_next(value: str | None) -> str:
    """A post-login redirect target limited to this app's own ``/ui/`` paths (no open redirect)."""
    if not value or not isinstance(value, str) or len(value) > 512:
        return "/ui/"
    if not value.startswith("/ui/") or value.startswith("//") or "\\" in value or any(ord(ch) < 32 for ch in value):
        return "/ui/"
    path = value.split("#", 1)[0].split("?", 1)[0]
    if "/../" in path or path.endswith("/..") or "/./" in path or "%2e" in path.lower():
        return "/ui/"
    if value.startswith(("/ui/login", "/ui/logout", "/ui/setup", "/ui/reset", "/ui/api/")):
        return "/ui/"
    return value


def mount_ui(
    app: FastAPI,
    stack: Stack | None = None,
    *,
    title: str | None = None,
    static_dir: str | Path | None = None,
    api_router: APIRouter | Iterable[APIRouter] | None = None,
    config: UIConfig | None = None,
    index: str = "index.html",
    no_access_page: Any = None,
    setup_hint: str | None = None,
    root_redirect: bool = True,
    default_auth: str = "komuta",
    bridge_panel: bool = True,
    allow_protection_off: bool = True,
    login_card_width: int | None = None,
    setup_card_width: int | None = None,
    login_hint: str | None = None,
) -> UIRuntime:
    """Mount the admin UI on ``app`` (call once, after ``create_app``).

    Args:
        app: the FastAPI app from :func:`stack_kit.create_app`.
        stack: stack shown in ``/ui/api/status`` (default: current stack).
        title: product name shown in the UI (default: stack name).
        static_dir: folder with the stack's own pages, served at ``/ui/``. ``None`` serves the kit's
            example page (``stack_ui/assets/example``).
        api_router: one or more ``APIRouter`` mounted under ``/ui/api`` with authentication and CSRF
            protection added automatically.
        config: :class:`UIConfig` (default :meth:`UIConfig.from_env`).
        index: file served for ``/ui/``.
        no_access_page: replaces the 403 "no access" page. Either an HTML string with the
            placeholders ``{{title}}``, ``{{message}}``, ``{{code}}`` and ``{{hint}}`` (values are
            HTML-escaped), or a callable ``(request, error) -> Response | str`` (sync or async;
            ``error`` has ``status``, ``code``, ``message``).
        setup_hint: one extra line shown on the default no-access page (e.g. how to enable this stack's UI).
        root_redirect: answer ``GET``/``HEAD /`` with a 303 to ``/ui/`` (no auth needed; ``/ui/``
            checks identity itself). A ``/`` route registered before ``mount_ui`` always wins.
        default_auth: auth mode when ``UI_AUTH`` (``config.auth``) is not set: ``komuta`` (default),
            ``password`` (first-time setup with a one-time setup code) or ``auto`` (Komuta identity if
            present, else password session).
        bridge_panel: mount ``/ui/api/bridge`` (bridge gate panel). ``False``: the endpoints do not exist
            (404) and the example access page hides the section; use it where a panel-made bridge
            token would be too powerful (e.g. trading stacks with the ``confirmed:true`` shortcut).
        allow_protection_off: ``False`` removes the "turn protection off" flow entirely (same effect as
            ``UI_FORCE_PROTECTION=1``).
        login_card_width / setup_card_width: width in px (280-960) of the sign-in / first-time setup card
            (default 440). Served as CSS variables from ``/ui/_kit/auth-vars.css`` (CSP-safe).
        login_hint: replaces the hint under the sign-in form (default depends on where the password comes from).
    Returns the :class:`UIRuntime` (also ``app.state.ui``). Raises ``RuntimeError`` if the app
    already has a UI (mount once; see "birleşik servis" in docs/UI.md).
    """
    if getattr(app.state, "ui", None) is not None:
        raise RuntimeError("mount_ui was already called for this app; mount once and pass every "
                           "stack's router via api_router")
    stack = stack or get_stack()
    config = config or UIConfig.from_env()
    if not allow_protection_off:
        config.force_protection = True
    for label, width in (("login_card_width", login_card_width), ("setup_card_width", setup_card_width)):
        if width is not None and (not isinstance(width, int) or not 280 <= width <= 960):
            raise ValueError(f"{label} must be an int between 280 and 960")
    root = Path(static_dir).resolve() if static_dir else ASSETS_DIR / "example"
    runtime = UIRuntime(stack, config, title or stack.name, root, index, default_auth=default_auth)
    app.state.ui = runtime

    async def _no_access(request: Request, exc: UIAuthError) -> Response:
        if callable(no_access_page):
            result = no_access_page(request, exc)
            if hasattr(result, "__await__"):
                result = await result
            if isinstance(result, Response):
                return result
            return Response(str(result), status_code=exc.status, media_type="text/html; charset=utf-8")
        if isinstance(no_access_page, str):
            steps = KOMUTA_SETUP_STEPS if config.auth == "komuta" and exc.code == "ui_unauthorized" else None
            body = (no_access_page.replace("{{title}}", html.escape(runtime.title))
                    .replace("{{message}}", html.escape(exc.message)).replace("{{code}}", html.escape(exc.code))
                    .replace("{{hint}}", html.escape(setup_hint or ""))
                    .replace("{{details}}", details_html(steps, exc.code)))
            return Response(body, status_code=exc.status, media_type="text/html; charset=utf-8")
        steps = KOMUTA_SETUP_STEPS if config.auth == "komuta" and exc.code == "ui_unauthorized" else None
        return _message_page(runtime.title, "Erişim yok / No access", exc.message, exc.status, steps, setup_hint,
                             exc.code)

    async def _ui_error(request: Request, exc: UIAuthError):
        wants_html = request.method == "GET" and not request.url.path.startswith("/ui/api/")
        if wants_html:
            if exc.code in ("ui_login_required", "ui_setup_required"):
                page = "/ui/setup" if exc.code == "ui_setup_required" else "/ui/login"
                target = safe_next(request.url.path + (f"?{request.url.query}" if request.url.query else ""))
                location = page if target == "/ui/" else f"{page}?next={quote(target, safe='/')}"
                return RedirectResponse(location, status_code=303)
            return await _no_access(request, exc)
        return JSONResponse({"ok": False, "error": {"code": exc.code, "message": exc.message}},
                            status_code=exc.status)

    app.add_exception_handler(UIAuthError, _ui_error)  # type: ignore[arg-type]

    ui_build = build_id(ASSETS_DIR, root)

    @app.middleware("http")
    async def _ui_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/ui" or path.startswith("/ui/"):
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
            response.headers["X-UI-Build"] = ui_build
            if path.startswith(("/ui/api/", "/ui/login", "/ui/setup")) or path == "/ui/logout":
                response.headers["Cache-Control"] = "no-store"
                response.headers["Pragma"] = "no-cache"
            if runtime.secure_cookie(request):
                response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def _set_csrf_cookie(request: Request, response: Response, value: str | None = None) -> None:
        if value is None and request.cookies.get(CSRF_COOKIE):
            return
        response.set_cookie(CSRF_COOKIE, value or secrets.token_urlsafe(24), path="/ui", samesite="strict",
                            secure=runtime.secure_cookie(request), httponly=False, max_age=config.session_ttl)

    # -- built-in API
    api = APIRouter(prefix="/ui/api", dependencies=[Depends(current_user), Depends(require_csrf)])

    @api.get("/me")
    async def me(user: UIUser = Depends(current_user)):
        return {"ok": True, "user": user.public(), "title": runtime.title, "auth": config.auth,
                "protection": runtime.protection_enabled, "password": runtime.password_source,
                "warnings": ["allowed_hosts_unset"] if config.auth in ("komuta", "auto") and runtime.host_warning else [],
                "stack": {"name": stack.name, "version": stack.version}}

    @api.get("/status")
    async def status(user: UIUser = Depends(current_user)):
        events = stack.event_log.since(max(0, stack.event_log.last_id - 20), 20)
        return {
            "ok": True,
            "stack": {"name": stack.name, "version": stack.version, "uptime": int(time.time() - runtime.started)},
            "tools": [{"name": t.name, "description": t.description, "scope": t.scope, "confirm": t.confirm}
                      for t in stack.tools.values()],
            "hooks": list(stack.hooks),
            "commands": [{"name": c.name, "help": c.help} for c in stack.commands.values()],
            "plugin_errors": list(stack.plugin_errors),
            "events": events[::-1],
            "webhooks": {"targets": len(stack.webhooks.targets), **stack.webhooks.stats},
        }

    def client_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def require_identified(user: UIUser) -> None:
        if user.method == "unprotected":
            raise UIAuthError(403, "ui_identity_required",
                              "protection is off: turn it back on and sign in to change credentials")

    def verify_password_or_fail(request: Request, password: Any) -> None:
        key = client_key(request)
        if wait := runtime.login_limiter.retry_after(key):
            raise UIAuthError(429, "rate_limited", f"too many failed attempts; retry in {wait}s")
        if not runtime.check_password(password):
            runtime.login_limiter.fail(key)
            raise UIAuthError(401, "invalid_password", "wrong password")

    def session_cookie(request: Request, response: Response) -> None:
        token, _sid, csrf = runtime.make_session()
        response.set_cookie(SESSION_COOKIE, token, path="/ui", httponly=True, samesite="strict",
                            secure=runtime.secure_cookie(request), max_age=config.session_ttl)
        _set_csrf_cookie(request, response, csrf)

    @api.get("/auth")
    async def auth_status(user: UIUser = Depends(current_user)):
        return {"ok": True, "mode": config.auth, "user": user.public(),
                "password": {"source": runtime.password_source,
                             "can_change": runtime.password_source != "env" and user.method != "unprotected",
                             "min_length": MIN_PASSWORD},
                "protection": {"enabled": runtime.protection_enabled, "forced": config.force_protection,
                               "disable_phrase": DISABLE_PHRASE},
                "scrypt": {"n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}}

    @api.get("/auth/audit")
    async def auth_audit(limit: int = 50, user: UIUser = Depends(current_user)):
        return {"ok": True, "audit": runtime.store.audit(max(1, min(limit, 200)))}

    @api.post("/auth/password")
    async def change_password(request: Request, user: UIUser = Depends(current_user)):
        require_identified(user)
        if runtime.password_source == "env":
            raise UIAuthError(409, "password_managed_by_env", "the password is set by UI_PASSWORD; change it there")
        body = await _json_body(request)
        if runtime.password_source == "stored":
            verify_password_or_fail(request, body.get("current"))
        new = body.get("new")
        if (problem := password_problem(new)) is not None:
            raise UIAuthError(422, "weak_password", problem)
        runtime.store.set_password(new, actor=user.label)  # bumps the session version: everyone else is out
        log.warning("admin UI password changed by %s; all sessions were signed out", user.label)
        response = JSONResponse({"ok": True})
        if user.method == "password":
            session_cookie(request, response)
        return response

    @api.post("/auth/sessions/revoke")
    async def revoke_sessions(request: Request, user: UIUser = Depends(current_user)):
        require_identified(user)
        runtime.store.revoke_sessions(actor=user.label)
        log.warning("admin UI: all sessions signed out by %s", user.label)
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/ui")
        return response

    @api.post("/auth/protection")
    async def set_protection(request: Request, user: UIUser = Depends(current_user)):
        body = await _json_body(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise UIAuthError(400, "bad_request", "expected {enabled: bool}")
        if enabled:
            runtime.store.set_protection(True, actor=user.label)
            log.warning("admin UI protection turned ON by %s", user.label)
            return {"ok": True, "protection": True}
        if config.force_protection:
            raise UIAuthError(403, "protection_forced", "UI_FORCE_PROTECTION=1: protection cannot be turned off")
        require_identified(user)
        if runtime.password_source == "none":
            raise UIAuthError(409, "password_required", "set a password first; it is needed to turn protection off")
        verify_password_or_fail(request, body.get("password"))
        if not isinstance(body.get("confirm"), str) or body["confirm"].strip() != DISABLE_PHRASE:
            raise UIAuthError(422, "confirm_phrase", f"type exactly: {DISABLE_PHRASE}")
        runtime.store.set_protection(False, actor=user.label)
        log.warning("!!  admin UI protection turned OFF by %s: anyone with the link can open /ui", user.label)
        return {"ok": True, "protection": False}

    gate = getattr(app.state, "gate", None)
    bridge_config = getattr(app.state, "config", None)
    if gate is not None and bridge_panel:
        @api.get("/bridge")
        async def bridge_status(user: UIUser = Depends(current_user)):
            env_token = bool(bridge_config and bridge_config.token and not bridge_config.token_generated)
            generated = bool(bridge_config and bridge_config.token_generated and not gate.has_panel_token)
            return {"ok": True, **gate.status(),
                    "env_token": env_token, "generated_token": generated,
                    "env_hook_secret": bool(bridge_config and bridge_config.hook_secret),
                    "audit": gate.audit(20)}

        @api.post("/bridge")
        async def bridge_change(request: Request, user: UIUser = Depends(current_user)):
            body = await _json_body(request)
            action = body.get("action")
            result: dict[str, Any] = {"ok": True}
            env_token = bool(bridge_config and bridge_config.token and not bridge_config.token_generated)
            if action in ("set", "set_all"):
                enabled = body.get("enabled")
                if not isinstance(enabled, bool):
                    raise UIAuthError(400, "bad_request", "expected {enabled: bool}")
                surfaces = list(GATE_SURFACES) if action == "set_all" else [body.get("surface")]
                if any(name not in GATE_SURFACES for name in surfaces):
                    raise UIAuthError(400, "bad_request", f"surface must be one of {', '.join(GATE_SURFACES)}")
                for name in surfaces:
                    gate.set(name, enabled, actor=user.label)
                if enabled and not env_token and not gate.has_panel_token and {"bridge", "mcp"} & set(surfaces):
                    result["token"] = gate.rotate_token(actor=user.label)  # shown once
            elif action == "rotate_token":
                result["token"] = gate.rotate_token(actor=user.label)
            elif action == "revoke_token":
                gate.revoke_token(actor=user.label)
            elif action == "rotate_hook_secret":
                result["hook_secret"] = gate.rotate_hook_secret(actor=user.label)
            elif action == "revoke_hook_secret":
                gate.revoke_hook_secret(actor=user.label)
            else:
                raise UIAuthError(400, "bad_request", "action: set | set_all | rotate_token | revoke_token | "
                                                      "rotate_hook_secret | revoke_hook_secret")
            if result.get("token"):
                runtime.remember_fresh_token(user, result["token"])  # for one "copy prompt with token"
            log.info("bridge gate %s by %s", action, user.label)
            result.update(gate.status())
            return JSONResponse(result)

        @api.get("/bridge/quickstart")
        async def bridge_quickstart(request: Request, client: str = "claude-code", with_token: int = 0,
                                    user: UIUser = Depends(current_user)):
            if client not in QUICK_CLIENTS:
                raise UIAuthError(400, "bad_request", f"client: {', '.join(QUICK_CLIENTS)}")
            base_url, verified = runtime.public_base_url(request)
            token = runtime.take_fresh_token(user) if with_token else None
            prompt = build_prompt(stack, base_url, gate.status(), client, token, server_name=stack.name,
                                  url_verified=verified,
                                  komuta_sign_in=True if config.auth in ("komuta", "auto") else None)
            return {"ok": True, "client": client, "prompt": prompt, "token_included": bool(token),
                    "base_url": base_url, "url_verified": verified,
                    "fresh_token_available": runtime.has_fresh_token(user)}

    routers = [api_router] if isinstance(api_router, APIRouter) else list(api_router or [])
    for router in routers:
        api.include_router(router)
    app.include_router(api)

    # -- login (password mode)
    @app.get("/ui/login", include_in_schema=False)
    async def login_page(request: Request):
        if not runtime.uses_password or runtime.disabled_reason:
            return RedirectResponse("/ui/", status_code=303)
        if runtime.setup_required:
            return RedirectResponse("/ui/setup" + (f"?{request.url.query}" if request.url.query else ""), status_code=303)
        if login_hint:
            hint = login_hint
        elif runtime.password_source == "env":
            hint = "Parola sunucudaki UI_PASSWORD ortam değişkenidir. / The password is the UI_PASSWORD env var."
        else:
            hint = ""  # a password set in the UI: only the "forgot my password" link under the form
        forgot = ('<p class="hint t-center"><a href="/ui/reset">Parolamı unuttum / Forgot my password</a></p>'
                  if runtime.reset_available else "")
        page = ((ASSETS_DIR / "login.html").read_text(encoding="utf-8").replace("{{hint}}", f'<p class="hint t-center">{html.escape(hint)}</p>' if hint else "")
                .replace("{{forgot}}", forgot))
        response = Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})
        _set_csrf_cookie(request, response)
        return response

    @app.post("/ui/login", include_in_schema=False)
    async def login(request: Request):
        is_json = (request.headers.get("content-type") or "").startswith("application/json")

        def fail(status: int, code: str, message: str, headers: dict | None = None) -> Response:
            if is_json:
                return JSONResponse({"ok": False, "error": {"code": code, "message": message}},
                                    status_code=status, headers=headers)
            return RedirectResponse(f"/ui/login?error={code}", status_code=303)

        if not runtime.uses_password or runtime.disabled_reason:
            return fail(403, "ui_disabled", "password sign-in is not enabled")
        if runtime.setup_required:
            return fail(409, "ui_setup_required", "first-time setup: open /ui/setup")
        if not runtime.origin_ok(request):
            return fail(403, "csrf_failed", "cross-origin request refused")
        key = request.client.host if request.client else "unknown"
        if wait := runtime.login_limiter.retry_after(key):
            return fail(429, "rate_limited", "too many failed attempts; try again later",
                        {"Retry-After": str(wait)})
        raw = await request.body()
        if len(raw) > 4096:
            return fail(413, "payload_too_large", "body too large")
        password, next_url = "", None
        try:
            if is_json:
                data = json.loads(raw or b"{}")
                password = data.get("password", "") if isinstance(data, dict) else ""
                next_url = data.get("next") if isinstance(data, dict) else None
            else:
                form = parse_qs(raw.decode("utf-8", "replace"))
                password = (form.get("password") or [""])[0]
                next_url = (form.get("next") or [None])[0]
        except (ValueError, RecursionError):
            password = ""
        target = safe_next(next_url if isinstance(next_url, str) else None)
        if not runtime.check_password(password):
            runtime.login_limiter.fail(key)
            log.info("UI login failed")
            return fail(401, "invalid_password", "wrong password")
        token, _sid, csrf = runtime.make_session()
        response: Response = (JSONResponse({"ok": True, "next": target}) if is_json
                              else RedirectResponse(target, status_code=303))
        response.set_cookie(SESSION_COOKIE, token, path="/ui", httponly=True, samesite="strict",
                            secure=runtime.secure_cookie(request), max_age=config.session_ttl)
        _set_csrf_cookie(request, response, csrf)
        log.info("UI login succeeded")
        return response

    @app.post("/ui/logout", include_in_schema=False, dependencies=[Depends(require_csrf)])
    async def logout(request: Request, user: UIUser = Depends(current_user)):
        if user.method == "password" and user.session:
            runtime.revoke(user.session)
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/ui")
        response.delete_cookie(CSRF_COOKIE, path="/ui")
        return response

    # -- first-time setup (password modes without UI_PASSWORD and without a stored password)
    @app.get("/ui/setup", include_in_schema=False)
    async def setup_page(request: Request):
        if not runtime.setup_required:
            return RedirectResponse("/ui/login" if runtime.uses_password else "/ui/", status_code=303)
        runtime.relog_setup_code()  # the code goes to the log only, never into this page
        extra = f'<p class="hint t-center">{html.escape(setup_hint)}</p>' if setup_hint else ""
        page = (ASSETS_DIR / "setup.html").read_text(encoding="utf-8").replace("{{hint}}", extra)
        response = Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})
        _set_csrf_cookie(request, response)
        return response

    @app.post("/ui/setup", include_in_schema=False)
    async def setup(request: Request):
        is_json = (request.headers.get("content-type") or "").startswith("application/json")

        def fail(status: int, code: str, message: str, headers: dict | None = None) -> Response:
            if is_json:
                return JSONResponse({"ok": False, "error": {"code": code, "message": message}},
                                    status_code=status, headers=headers)
            return RedirectResponse(f"/ui/setup?error={code}", status_code=303)

        if not runtime.setup_required:
            return fail(409, "setup_done", "setup is already complete; sign in at /ui/login")
        if not runtime.origin_ok(request):
            return fail(403, "csrf_failed", "cross-origin request refused")
        if (wait := runtime.setup_locked_until - time.time()) > 0:
            return fail(423, "setup_locked", "too many wrong setup codes; try again later or restart for a new code",
                        {"Retry-After": str(int(wait) + 1)})
        raw = await request.body()  # read first: no await between the limiter check and recording a failure
        if len(raw) > 4096:
            return fail(413, "payload_too_large", "body too large")
        key = client_key(request)
        if wait := runtime.setup_limiter.retry_after(key):
            return fail(429, "rate_limited", "too many attempts; try again later", {"Retry-After": str(wait)})
        try:
            if is_json:
                data = json.loads(raw or b"{}")
                data = data if isinstance(data, dict) else {}
            else:
                data = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        except (ValueError, RecursionError):
            data = {}
        if not runtime.check_setup_code(data.get("code")):
            runtime.setup_limiter.fail(key)
            runtime.setup_failures += 1
            log.warning("admin UI setup: wrong setup code (%d/%d)", runtime.setup_failures, config.setup_lock_after)
            if runtime.setup_failures >= config.setup_lock_after:
                runtime.setup_locked_until = time.time() + config.setup_lock_seconds
                runtime.setup_failures = 0
                log.error("admin UI setup locked for %ds after too many wrong codes (restart = new code)",
                          int(config.setup_lock_seconds))
            return fail(401, "invalid_setup_code", "wrong setup code")
        password = data.get("password")
        if data.get("password2") is not None and data.get("password2") != password:
            return fail(422, "password_mismatch", "passwords do not match")
        if (problem := password_problem(password)) is not None:
            return fail(422, "weak_password", problem)
        runtime.store.set_password(password, actor="setup")
        runtime.setup_code = None  # single use
        log.warning("admin UI first-time setup completed; the setup code is no longer valid")
        target = safe_next(data.get("next") if isinstance(data.get("next"), str) else None)
        response: Response = (JSONResponse({"ok": True, "next": target}) if is_json
                              else RedirectResponse(target, status_code=303))
        session_cookie(request, response)
        return response

    # -- forgotten password (password modes with a password set in the UI)
    @app.get("/ui/reset", include_in_schema=False)
    async def reset_page(request: Request):
        if not runtime.uses_password or runtime.disabled_reason:
            return RedirectResponse("/ui/", status_code=303)
        if runtime.setup_required:
            return RedirectResponse("/ui/setup", status_code=303)
        if not runtime.reset_available:
            return _message_page(runtime.title, "Parola ortamdan geliyor / The password is set in the environment",
                                 "Bu parola sunucudaki UI_PASSWORD ortam değişkenidir; orada değiştirip servisi yeniden "
                                 "başlatın. / It is the UI_PASSWORD environment variable: change it there and restart.", 409)
        runtime.issue_reset_code()  # the code goes to the log only, never into this page
        page = (ASSETS_DIR / "reset.html").read_text(encoding="utf-8")
        response = Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})
        _set_csrf_cookie(request, response)
        return response

    @app.post("/ui/reset", include_in_schema=False)
    async def reset(request: Request):
        is_json = (request.headers.get("content-type") or "").startswith("application/json")

        def fail(status: int, code: str, message: str, headers: dict | None = None) -> Response:
            if is_json:
                return JSONResponse({"ok": False, "error": {"code": code, "message": message}},
                                    status_code=status, headers=headers)
            return RedirectResponse(f"/ui/reset?error={code}", status_code=303)

        if not runtime.reset_available:
            return fail(409, "reset_unavailable", "password reset is not available (UI_PASSWORD, no password yet or not a password mode)")
        if not runtime.origin_ok(request):
            return fail(403, "csrf_failed", "cross-origin request refused")
        if (wait := runtime.reset_locked_until - time.time()) > 0:
            return fail(423, "reset_locked", "too many wrong reset codes; try again later",
                        {"Retry-After": str(int(wait) + 1)})
        raw = await request.body()  # read first: no await between the limiter check and recording a failure
        if len(raw) > 4096:
            return fail(413, "payload_too_large", "body too large")
        key = client_key(request)
        if wait := runtime.reset_limiter.retry_after(key):
            return fail(429, "rate_limited", "too many attempts; try again later", {"Retry-After": str(wait)})
        try:
            if is_json:
                data = json.loads(raw or b"{}")
                data = data if isinstance(data, dict) else {}
            else:
                data = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        except (ValueError, RecursionError):
            data = {}
        if not runtime.check_reset_code(data.get("code")):
            runtime.reset_limiter.fail(key)
            runtime.reset_failures += 1
            log.warning("admin UI password reset: wrong or expired reset code (%d/%d)", runtime.reset_failures,
                        config.setup_lock_after)
            if runtime.reset_failures >= config.setup_lock_after:
                runtime.reset_locked_until = time.time() + config.setup_lock_seconds
                runtime.reset_failures = 0
                runtime.reset_code = None  # a locked-out guesser gets a fresh code next time
                log.error("admin UI password reset locked for %ds after too many wrong codes", int(config.setup_lock_seconds))
            return fail(401, "invalid_reset_code", "wrong or expired reset code")
        password = data.get("password")
        if data.get("password2") is not None and data.get("password2") != password:
            return fail(422, "password_mismatch", "passwords do not match")
        if (problem := password_problem(password)) is not None:
            return fail(422, "weak_password", problem)  # the code stays valid: fix the password and try again
        runtime.store.set_password(password, actor="reset-code")  # bumps the session version: every old session is out
        runtime.reset_code = None  # single use
        runtime.reset_failures = 0
        log.warning("admin UI password was reset with a reset code; all earlier sessions were signed out")
        target = safe_next(data.get("next") if isinstance(data.get("next"), str) else None)
        response: Response = (JSONResponse({"ok": True, "next": target}) if is_json
                              else RedirectResponse(target, status_code=303))
        session_cookie(request, response)
        return response

    # -- static files
    @app.get("/ui", include_in_schema=False)
    async def ui_root():
        return RedirectResponse("/ui/", status_code=307)

    @app.get("/ui/_kit/auth-vars.css", include_in_schema=False)
    async def auth_vars():
        css = ":root{" + "".join(f"--kui-{name}:{value}px;" for name, value in
                                 (("login-card-width", login_card_width), ("setup-card-width", setup_card_width))
                                 if value) + "}"
        return Response(css, media_type="text/css; charset=utf-8", headers={"Cache-Control": "no-cache"})

    @app.get("/ui/_kit/{path:path}", include_in_schema=False)
    async def kit_asset(path: str):
        # the shared design system is public (it is open source); pages and APIs are not
        file = _safe_file(ASSETS_DIR, path)
        if file is None or file.suffix not in (".css", ".js", ".svg", ".html", ".png", ".ico", ".woff2"):
            return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "not found"}},
                                status_code=404)
        return _file_response(file)

    @app.api_route("/ui/api/{path:path}", methods=["POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def ui_api_unknown(path: str, request: Request):
        await current_user(request)  # identity first: do not reveal which API routes exist
        # unknown (or switched-off, e.g. bridge_panel=False) API routes: 404, not 405
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "not found"}}, status_code=404)

    @app.get("/ui/{path:path}", include_in_schema=False)
    async def ui_page(path: str, request: Request):
        await current_user(request)  # identity first, also for unknown /ui/api/* routes
        if path.startswith("api/"):
            return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "not found"}},
                                status_code=404)
        file = _safe_file(root, path or index)
        if file is None:
            return _message_page(runtime.title, "Bulunamadı / Not found",
                                 "Bu sayfa yok. / This page does not exist.", 404)
        if file.suffix == ".html":
            page = stamp_build(inline_icons(file.read_text(encoding="utf-8"), root), ui_build)
            response = Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})
            _set_csrf_cookie(request, response)
            return response
        return _file_response(file)

    if root_redirect:
        # appended last: any "/" route the app already has keeps precedence
        async def _root_to_ui():
            return RedirectResponse("/ui/", status_code=303)

        app.add_api_route("/", _root_to_ui, methods=["GET", "HEAD"], include_in_schema=False)

    log.info("admin UI mounted at /ui (auth=%s)", config.auth)
    return runtime
