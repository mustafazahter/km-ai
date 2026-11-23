"""FastAPI application implementing bridge protocol v1."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic_core import to_jsonable_python
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import ErrorCode, ToolError, error_body
from .events import WebhookTarget, webhooks_out_from_env, verify_hook_signature, verify_signature
from .mcp import SUPPORTED_PROTOCOL_VERSIONS, McpServer, _error as rpc_error, PARSE_ERROR, INVALID_REQUEST
from .gate import BridgeGate
from .plugins import load_plugins as _load_plugins
from .redact import REVEAL_ATTR, install_log_redaction, redact, register_secret
from .registry import Stack, _current, _maybe_await, get_stack
from .settings import env_bool, env_float, env_int, env_str, load_dotenv

log = logging.getLogger("stack_kit")


# --------------------------------------------------------------------------- config


@dataclass
class BridgeConfig:
    """Settings of the HTTP bridge. :meth:`from_env` reads the protocol v1 variables."""

    token: str | None = None
    hook_secret: str | None = None
    webhooks_out: list[WebhookTarget] = field(default_factory=list)
    require_confirm_roundtrip: bool | None = None
    max_body: int = 1_048_576
    hook_max_body: int = 262_144
    hook_timeout: float = 10.0
    hook_tolerance: int = 300
    hook_require_timestamp: bool = False
    auth_fail_limit: int = 10
    auth_fail_window: float = 60.0
    webhook_queue_size: int = 1000
    webhook_max_attempts: int = 5
    webhook_backoff: float = 1.0
    webhook_drain_timeout: float = 5.0
    mcp_max_batch: int = 100
    mcp_max_response_bytes: int = 1_048_576
    allow_onestep_confirm: bool | None = None
    token_generated: bool = False

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        rr = env_str("BRIDGE_REQUIRE_CONFIRM_ROUNDTRIP")
        return cls(
            token=env_str("BRIDGE_TOKEN"),
            hook_secret=env_str("HOOK_SECRET"),
            webhooks_out=webhooks_out_from_env(),
            require_confirm_roundtrip=None if rr is None else env_bool("BRIDGE_REQUIRE_CONFIRM_ROUNDTRIP"),
            max_body=env_int("BRIDGE_MAX_BODY", 1_048_576),
            hook_max_body=env_int("HOOK_MAX_BODY", 262_144),
            hook_timeout=env_float("HOOK_TIMEOUT", 10.0),
            hook_tolerance=env_int("HOOK_TOLERANCE", 300),
            hook_require_timestamp=env_bool("HOOK_REQUIRE_TIMESTAMP", False),
            auth_fail_limit=env_int("AUTH_FAIL_LIMIT", 10),
            auth_fail_window=env_float("AUTH_FAIL_WINDOW", 60.0),
            webhook_queue_size=env_int("WEBHOOKS_QUEUE_SIZE", 1000),
            webhook_max_attempts=env_int("WEBHOOKS_MAX_ATTEMPTS", 5),
            webhook_backoff=env_float("WEBHOOKS_BACKOFF", 1.0),
            webhook_drain_timeout=env_float("WEBHOOKS_DRAIN_TIMEOUT", 5.0),
            mcp_max_batch=env_int("MCP_MAX_BATCH", 100),
            mcp_max_response_bytes=env_int("MCP_MAX_RESPONSE_BYTES", 1_048_576),
            allow_onestep_confirm=None if env_str("BRIDGE_ALLOW_ONESTEP_CONFIRM") is None
            else env_bool("BRIDGE_ALLOW_ONESTEP_CONFIRM"),
        )


class FailLimiter:
    """Blocks a client after ``limit`` failed authentications within ``window`` seconds."""

    def __init__(self, limit: int = 10, window: float = 60.0, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._fails[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if not q:
            self._fails.pop(key, None)
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until ``key`` may try again; 0 when not blocked."""
        if self.limit <= 0:
            return 0
        now = self._clock()
        with self._lock:
            q = self._prune(key, now)
            if len(q) >= self.limit:
                return max(1, int(self.window - (now - q[0])) + 1)
        return 0

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails[key].append(self._clock())
            if len(self._fails) > 10_000:  # bound memory under scanning
                self._fails.pop(next(iter(self._fails)))


class HookRequest:
    """What a ``@hook`` handler receives."""

    def __init__(self, name: str, body: bytes, headers: dict[str, str], query: dict[str, str], via: str):
        self.name = name
        self.body = body
        self.headers = headers
        self.query = query
        self.via = via  # "signature" | "token" | "stack-signature" | "body-secret"
        self._json: Any = None
        self._parsed = False

    def json(self) -> Any:
        """Parsed JSON body (raises ``ValueError`` for invalid JSON)."""
        if not self._parsed:
            self._json = json.loads(self.body.decode("utf-8")) if self.body else None
            self._parsed = True
        return self._json

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- helpers


class _BodyTooLarge(Exception):
    pass


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise _BodyTooLarge
        except ValueError:
            raise _BodyTooLarge from None
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > limit:
            raise _BodyTooLarge
    return bytes(chunks)


def _json_error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(error_body(code, message), status_code=status, headers=headers)


_STATUS = {
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INVALID_ARGS: 422,
    ErrorCode.CONFIRM_UNKNOWN: 409,
    ErrorCode.CONFIRM_EXPIRED: 409,
    ErrorCode.CONFIRM_MISMATCH: 409,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.IN_PROGRESS: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.INTERNAL: 500,
}


def result_status(body: dict) -> int:
    if body.get("ok") or body.get("needs_confirmation"):
        return 200
    return _STATUS.get((body.get("error") or {}).get("code", ""), 400)


def _fresh(timestamp: str, tolerance: int) -> bool:
    try:
        return abs(time.time() - int(timestamp.strip())) <= tolerance
    except ValueError:
        return False


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _supplied_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    token = request.headers.get("x-bridge-token")
    return token.strip() if token else None


def _token_ok(supplied: str | None, expected: str) -> bool:
    if not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


# --------------------------------------------------------------------------- app


def create_app(
    stack: Stack | None = None,
    *,
    config: BridgeConfig | None = None,
    load_plugins: bool = True,
    plugins_dir: str | None = None,
    gates: BridgeGate | None = None,
) -> FastAPI:
    """Build the FastAPI app for ``stack`` (default: current stack).

    Loads plugins (unless ``load_plugins=False``), applies ``config`` (default
    :meth:`BridgeConfig.from_env`), generates ``BRIDGE_TOKEN`` when missing (logged once; only while
    a machine surface is open and no panel token exists), and wires startup/shutdown hooks,
    background tasks and the webhook worker to the app lifespan.

    ``gates``: :class:`stack_kit.gate.BridgeGate` switching ``/bridge/*``, ``/mcp`` and ``/hook/*`` on
    and off (closed = 404). Default ``BridgeGate()``: everything open unless ``BRIDGE_ENABLED`` /
    ``MCP_ENABLED`` / ``HOOKS_ENABLED`` or the persisted panel state say otherwise.
    """
    install_log_redaction()
    stack = stack or get_stack()
    config = config or BridgeConfig.from_env()
    gate = gates if gates is not None else BridgeGate()
    if not config.token and gate.any_enabled() and not gate.has_panel_token:
        config.token = secrets.token_urlsafe(32)
        config.token_generated = True
        register_secret(config.token)
        log.warning(
            "BRIDGE_TOKEN is not set; generated a random token for this process (shown once): %s",
            config.token, extra={REVEAL_ATTR: True},
        )
    register_secret(config.token)
    register_secret(config.hook_secret)
    if config.require_confirm_roundtrip is not None:
        stack.require_confirm_roundtrip = config.require_confirm_roundtrip
    if config.allow_onestep_confirm is not None:
        stack.allow_onestep_confirm = config.allow_onestep_confirm
    if config.webhooks_out:
        stack.webhooks.targets = list(config.webhooks_out)
    stack.webhooks.queue_size = config.webhook_queue_size
    stack.webhooks.max_attempts = config.webhook_max_attempts
    stack.webhooks.backoff_base = config.webhook_backoff
    stack.webhooks.drain_timeout = config.webhook_drain_timeout

    if load_plugins and not getattr(stack, "_plugins_loaded", False):
        _load_plugins(stack, plugins_dir)
        stack._plugins_loaded = True  # type: ignore[attr-defined]  # create_app twice must not re-import

    def bridge_token_ok(supplied: str | None) -> bool:
        """Env/config token or the panel token (both may be valid at the same time)."""
        if not supplied:
            return False
        env_ok = False
        if config.token and not (config.token_generated and gate.has_panel_token):
            env_ok = _token_ok(supplied, config.token)
        panel_ok = gate.verify_token(supplied)
        return env_ok or panel_ok

    def closed(surface: str) -> JSONResponse | None:
        # a closed surface looks exactly like a route that does not exist
        return None if gate.enabled(surface) else _json_error(404, ErrorCode.NOT_FOUND, "Not Found")

    # separate counters: failed webhook signatures must not lock out bridge/MCP callers (and vice versa)
    limiter = FailLimiter(config.auth_fail_limit, config.auth_fail_window)
    hook_limiter = FailLimiter(config.auth_fail_limit, config.auth_fail_window)
    app_limiters = (limiter, hook_limiter)
    mcp = McpServer(stack, max_batch=config.mcp_max_batch, max_response_bytes=config.mcp_max_response_bytes)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        await stack.startup()
        try:
            yield
        finally:
            await stack.shutdown()

    app = FastAPI(title=f"{stack.name} bridge", version=stack.version, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.stack = stack
    app.state.config = config
    app.state.limiters = app_limiters
    app.state.gate = gate

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_request: Request, exc: StarletteHTTPException):
        code = {404: ErrorCode.NOT_FOUND, 405: "method_not_allowed"}.get(exc.status_code, ErrorCode.BAD_REQUEST)
        return _json_error(exc.status_code, code, str(exc.detail)[:200], getattr(exc, "headers", None))

    def auth_failure(request: Request) -> JSONResponse | None:
        # a valid token always passes; the rate limit only applies to failed authentication
        if bridge_token_ok(_supplied_token(request)):
            return None
        key = _client_key(request)
        if wait := limiter.retry_after(key):
            return _json_error(429, ErrorCode.RATE_LIMITED, "too many failed authentication attempts",
                               {"Retry-After": str(wait)})
        limiter.fail(key)
        return _json_error(401, ErrorCode.UNAUTHORIZED, "missing or invalid bridge token",
                           {"WWW-Authenticate": "Bearer"})

    @app.get("/health")
    async def health():
        return {"ok": True, "stack": stack.name, "version": stack.version}

    @app.get("/bridge/manifest")
    async def manifest(request: Request):
        if (denied := closed("bridge") or auth_failure(request)) is not None:
            return denied
        return stack.manifest()

    @app.post("/bridge/call/{name}")
    async def call(name: str, request: Request):
        if (denied := closed("bridge") or auth_failure(request)) is not None:
            return denied
        try:
            raw = await _read_body(request, config.max_body)
        except _BodyTooLarge:
            return _json_error(413, ErrorCode.PAYLOAD_TOO_LARGE, f"body larger than {config.max_body} bytes")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except (ValueError, RecursionError):
            return _json_error(400, ErrorCode.BAD_REQUEST, "body must be JSON")
        if not isinstance(body, dict):
            return _json_error(400, ErrorCode.BAD_REQUEST, "body must be a JSON object")
        args = body.get("args", {})
        confirmed = body.get("confirmed", False)
        confirm_id = body.get("confirm_id")
        idem = body.get("idempotency_key")
        if args is None:
            args = {}
        if not isinstance(args, dict) or not isinstance(confirmed, bool) or not (
            confirm_id is None or isinstance(confirm_id, str)
        ) or not (idem is None or isinstance(idem, str)):
            return _json_error(400, ErrorCode.BAD_REQUEST,
                               "expected {args: object, confirmed?: bool, confirm_id?: string, "
                               "idempotency_key?: string}")
        result = await stack.call(name, args, confirm_id=confirm_id or None, confirmed=confirmed,
                                  idempotency_key=idem)
        return JSONResponse(result, status_code=result_status(result))

    @app.get("/bridge/events")
    async def events(request: Request, since: int = 0, limit: int = 100):
        if (denied := closed("bridge") or auth_failure(request)) is not None:
            return denied
        limit = max(1, min(limit, 500))
        return {"ok": True, "events": stack.event_log.since(since, limit), "last_id": stack.event_log.last_id}

    @app.post("/hook/{name}")
    async def incoming_hook(name: str, request: Request):
        if (denied := closed("hook")) is not None:
            return denied
        key = _client_key(request)
        hook = stack.hooks.get(name)
        try:
            raw = await _read_body(request, config.hook_max_body)
        except _BodyTooLarge:
            return _json_error(413, ErrorCode.PAYLOAD_TOO_LARGE, f"body larger than {config.hook_max_body} bytes")

        via = None
        hook_secrets = [x for x in (config.hook_secret, gate.hook_secret) if x]
        ts_header = request.headers.get("x-timestamp")
        sig_header = request.headers.get("x-signature")
        ts_fresh = ts_header is not None and _fresh(ts_header, config.hook_tolerance)
        for secret in hook_secrets:
            if sig_header and ts_fresh and verify_signature(
                secret, raw, ts_header, sig_header, tolerance=config.hook_tolerance
            ):
                via = "signature"  # replay-protected form: hmac(secret, timestamp + "." + body)
            elif not config.hook_require_timestamp and verify_hook_signature(secret, raw, sig_header):
                via = "signature"  # v1 form: hmac(secret, body); replayable, see SECURITY.md
            if via:
                break
        if via is None and bridge_token_ok(_supplied_token(request)):
            via = "token"
        if via is None and request.headers.get("x-stack-signature"):
            for secret in hook_secrets:
                if verify_signature(secret, raw, request.headers.get("x-stack-timestamp"),
                                    request.headers.get("x-stack-signature"), tolerance=config.hook_tolerance):
                    via = "stack-signature"
                    break
        if (via is None and hook_secrets and hook is not None and hook.body_secret_field
                and not config.hook_require_timestamp and (ts_header is None or ts_fresh)):
            try:
                parsed = json.loads(raw)
            except (ValueError, RecursionError):
                parsed = None
            if isinstance(parsed, dict):
                supplied = parsed.get(hook.body_secret_field)
                if isinstance(supplied, str) and any(_token_ok(supplied, x) for x in hook_secrets):
                    via = "body-secret"
                    parsed.pop(hook.body_secret_field, None)
                    raw = json.dumps(parsed, ensure_ascii=False).encode()
        if via is None:
            if wait := hook_limiter.retry_after(key):
                return _json_error(429, ErrorCode.RATE_LIMITED, "too many failed authentication attempts",
                                   {"Retry-After": str(wait)})
            hook_limiter.fail(key)
            return _json_error(401, ErrorCode.UNAUTHORIZED, "missing or invalid webhook signature/token")
        if hook is None:
            return _json_error(404, ErrorCode.NOT_FOUND, f"unknown hook: {name}"[:200])

        req = HookRequest(name, raw, dict(request.headers), dict(request.query_params), via)
        ctx_token = _current.set(stack)
        try:
            result = await asyncio.wait_for(_maybe_await(hook.func, req), timeout=config.hook_timeout)
        except ToolError as exc:
            return _json_error(400, exc.code, redact(exc.message))
        except asyncio.TimeoutError:
            resp = error_body(ErrorCode.TIMEOUT, f"hook timed out after {config.hook_timeout:g}s; the outcome "
                                                 "is unknown (a sync handler may still complete)")
            resp["error"]["outcome_unknown"] = True
            return JSONResponse(resp, status_code=504)
        except (ValueError, RecursionError):
            return _json_error(400, ErrorCode.BAD_REQUEST, "invalid hook payload")
        except Exception:
            log.exception("hook %s failed", name)
            return _json_error(500, ErrorCode.INTERNAL, "hook failed with an internal error")
        finally:
            _current.reset(ctx_token)
        return {"ok": True, "result": to_jsonable_python(result, fallback=str)}

    @app.post("/mcp")
    async def mcp_post(request: Request):
        if (denied := closed("mcp") or auth_failure(request)) is not None:
            return denied
        version = request.headers.get("mcp-protocol-version")
        if version and version not in SUPPORTED_PROTOCOL_VERSIONS:
            return JSONResponse(rpc_error(None, INVALID_REQUEST, f"unsupported MCP-Protocol-Version: {version}"[:200]),
                                status_code=400)
        try:
            raw = await _read_body(request, config.max_body)
        except _BodyTooLarge:
            return JSONResponse(rpc_error(None, INVALID_REQUEST, "request too large"), status_code=413)
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError):
            return JSONResponse(rpc_error(None, PARSE_ERROR, "parse error"), status_code=400)
        status, body = await mcp.handle_payload(payload)
        if body is None:
            return Response(status_code=202)
        return JSONResponse(body, status_code=status)

    @app.get("/mcp")
    async def mcp_get():
        if (denied := closed("mcp")) is not None:
            return denied
        return _json_error(405, "method_not_allowed", "this server does not offer an SSE stream; use POST",
                           {"Allow": "POST"})

    @app.delete("/mcp")
    async def mcp_delete():
        if (denied := closed("mcp")) is not None:
            return denied
        return _json_error(405, "method_not_allowed", "stateless server: no session to delete", {"Allow": "POST"})

    return app


def run(stack: Stack | None = None, *, host: str | None = None, port: int | None = None,
        dotenv: str | None = ".env", **uvicorn_kwargs: Any) -> None:
    """Load ``.env`` (optional), build the app and serve it with uvicorn.

    ``HTTP_HOST`` (default ``0.0.0.0``) and ``HTTP_PORT`` (default ``8080``) are used unless
    ``host``/``port`` are given. ``LOG_LEVEL`` sets the log level (default ``INFO``).
    """
    import uvicorn

    if dotenv:
        load_dotenv(dotenv)
    level = (env_str("LOG_LEVEL", "INFO") or "INFO").upper()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(stack)
    bind = host or env_str("HTTP_HOST", "0.0.0.0") or "0.0.0.0"
    if bind == "0.0.0.0":  # nosec B104 - containers (Komuta) need all interfaces
        log.info("listening on 0.0.0.0 (all interfaces); outside a container set HTTP_HOST=127.0.0.1")
    uvicorn.run(app, host=bind,
                port=port or env_int("HTTP_PORT", 8080), log_level=level.lower(), **uvicorn_kwargs)
