"""Tool / hook / command registry and the :class:`Stack` container."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import inspect
import logging
import re
import typing
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, create_model
from pydantic_core import PydanticUndefined, to_jsonable_python

from .confirm import CONFIRM_TTL, ConfirmStore, IdempotencyStore, args_digest
from .errors import ErrorCode, ToolError, error_body
from .events import EventLog, WebhookDispatcher
from .redact import redact
from .settings import env_bool, env_float, env_int, env_str

log = logging.getLogger("stack_kit")

PROTOCOL_VERSION = 1
Scope = Literal["read", "trade", "admin"]
SCOPES = ("read", "trade", "admin")

TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
HOOK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
COMMAND_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
EVENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")


# --------------------------------------------------------------------------- specs


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    args_model: type[BaseModel]
    input_schema: dict
    confirm: bool = False
    scope: str = "read"
    preview: Callable[..., Any] | None = None
    timeout: float | None = None

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.func)

    def manifest(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "confirm": self.confirm,
            "scope": self.scope,
        }


@dataclass
class Hook:
    name: str
    func: Callable[..., Any]
    body_secret_field: str | None = None


@dataclass
class Command:
    name: str
    func: Callable[..., Any]
    help: str = ""


def _build_args_model(func: Callable[..., Any], model_name: str) -> type[BaseModel]:
    sig = inspect.signature(func)
    try:
        hints = typing.get_type_hints(func, include_extras=True)
    except Exception as exc:  # unresolved forward refs etc.
        raise TypeError(f"cannot read type hints of {func.__qualname__}: {exc}") from exc
    fields: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"tool {func.__qualname__}: *args/**kwargs are not supported")
        if param.kind is param.POSITIONAL_ONLY:
            raise TypeError(f"tool {func.__qualname__}: positional-only parameters are not supported")
        if pname.startswith("_"):
            raise TypeError(f"tool {func.__qualname__}: parameter names must not start with '_' ({pname})")
        annotation = hints.get(pname, Any)
        default = PydanticUndefined if param.default is param.empty else param.default
        fields[pname] = (annotation, default) if default is not PydanticUndefined else (annotation, ...)
    return create_model(model_name, __config__=ConfigDict(extra="forbid", allow_inf_nan=False), **fields)  # type: ignore[call-overload]


def _schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    for prop in schema["properties"].values():
        if isinstance(prop, dict):
            prop.pop("title", None)
    return schema


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_url=False, include_input=False):
        loc = ".".join(str(p) for p in err.get("loc", ())) or "args"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)[:500]


async def _maybe_await(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    result = await asyncio.to_thread(functools.partial(func, *args, **kwargs))
    if inspect.isawaitable(result):
        result = await result
    return result


# --------------------------------------------------------------------------- stack


class Stack:
    """Holds everything one stack exposes: tools, hooks, Telegram commands, events, lifecycle.

    Args:
        name: stack name shown in ``/health`` and the manifest (``STACK_NAME`` env if omitted).
        version: stack version (``STACK_VERSION`` env if omitted).
        events: event names this stack may emit (listed in the manifest).
        require_confirm_roundtrip: disable the HTTP ``confirmed:true`` shortcut.
            ``None`` reads ``BRIDGE_REQUIRE_CONFIRM_ROUNDTRIP`` (default false).
        tool_timeout: seconds before a tool call returns ``timeout`` (``TOOL_TIMEOUT``, default 60).
    """

    def __init__(
        self,
        name: str | None = None,
        version: str | None = None,
        *,
        events: list[str] | None = None,
        require_confirm_roundtrip: bool | None = None,
        tool_timeout: float | None = None,
        event_buffer: int | None = None,
    ):
        self.name = name or env_str("STACK_NAME", "stack") or "stack"
        self.version = version or env_str("STACK_VERSION", "0.0.0") or "0.0.0"
        self.tools: dict[str, Tool] = {}
        self.hooks: dict[str, Hook] = {}
        self.commands: dict[str, Command] = {}
        self.events: list[str] = []
        self.plugin_errors: list[dict] = []
        self.loaded_plugins: list[str] = []
        self._require_roundtrip = require_confirm_roundtrip
        self._allow_onestep: bool | None = None
        self.tool_timeout = tool_timeout if tool_timeout is not None else env_float("TOOL_TIMEOUT", 60.0)
        self.confirmations = ConfirmStore(ttl=CONFIRM_TTL)
        self.idempotency = IdempotencyStore()
        self.event_log = EventLog(maxlen=event_buffer or env_int("EVENTS_BUFFER", 500))
        self.webhooks = WebhookDispatcher()
        self._startup: list[Callable[[], Any]] = []
        self._shutdown: list[Callable[[], Any]] = []
        self._background: list[Callable[[], Awaitable[Any]]] = []
        self._bg_tasks: list[asyncio.Task] = []
        for event in events or []:
            self.declare_event(event)

    # -- config
    @property
    def require_confirm_roundtrip(self) -> bool:
        if self._require_roundtrip is None:
            return env_bool("BRIDGE_REQUIRE_CONFIRM_ROUNDTRIP", False)
        return self._require_roundtrip

    @require_confirm_roundtrip.setter
    def require_confirm_roundtrip(self, value: bool | None) -> None:
        self._require_roundtrip = value

    @property
    def allow_onestep_confirm(self) -> bool:
        """``confirmed:true`` shortcut for ``confirm=True`` tools with scope ``trade``/``admin``.
        Default **off** (two steps required); ``BRIDGE_ALLOW_ONESTEP_CONFIRM=1`` turns it on."""
        if self._allow_onestep is None:
            return env_bool("BRIDGE_ALLOW_ONESTEP_CONFIRM", False)
        return self._allow_onestep

    @allow_onestep_confirm.setter
    def allow_onestep_confirm(self, value: bool | None) -> None:
        self._allow_onestep = value

    # -- registration
    def add_tool(
        self,
        func: Callable[..., Any],
        name: str,
        description: str | None = None,
        *,
        confirm: bool = False,
        scope: Scope = "read",
        preview: Callable[..., Any] | None = None,
        timeout: float | None = None,
    ) -> Tool:
        if not isinstance(name, str) or len(name) > 64 or not TOOL_NAME_RE.match(name):
            raise ValueError(f"invalid tool name {name!r}: use lowercase '<stack>.<action>' (a-z, 0-9, _), max 64")
        if scope not in SCOPES:
            raise ValueError(f"invalid scope {scope!r} for {name}: use one of {SCOPES}")
        if name in self.tools:
            raise ValueError(f"tool {name!r} is already registered")
        mcp_name = name.replace(".", "_")
        for other in self.tools.values():
            if other.name.replace(".", "_") == mcp_name:
                raise ValueError(f"tool {name!r} collides with {other.name!r} after '.'->'_' (MCP name)")
        desc = description if description is not None else (inspect.getdoc(func) or "").strip()
        model = _build_args_model(func, "args_" + mcp_name)
        t = Tool(name=name, description=desc, func=func, args_model=model, input_schema=_schema(model),
                 confirm=bool(confirm), scope=scope, preview=preview, timeout=timeout)
        self.tools[name] = t
        return t

    def tool(
        self,
        name: str,
        description: str | None = None,
        *,
        confirm: bool = False,
        scope: Scope = "read",
        preview: Callable[..., Any] | None = None,
        timeout: float | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_tool(func, name, description, confirm=confirm, scope=scope, preview=preview, timeout=timeout)
            return func

        return deco

    def hook(self, name: str, *, body_secret_field: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if not HOOK_NAME_RE.match(name or ""):
            raise ValueError(f"invalid hook name {name!r}: a-z, 0-9, '_' or '-', max 64")

        def deco(func: Callable[..., Any]) -> Callable[..., Any]:
            if name in self.hooks:
                raise ValueError(f"hook {name!r} is already registered")
            self.hooks[name] = Hook(name, func, body_secret_field)
            return func

        return deco

    def command(self, name: str, help: str = "") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        name = name.lstrip("/").lower()
        if not COMMAND_NAME_RE.match(name):
            raise ValueError(f"invalid command name {name!r}: a-z, 0-9, '_', max 32")

        def deco(func: Callable[..., Any]) -> Callable[..., Any]:
            if name in self.commands:
                raise ValueError(f"command /{name} is already registered")
            self.commands[name] = Command(name, func, help)
            return func

        return deco

    def declare_event(self, name: str) -> None:
        if not EVENT_NAME_RE.match(name or ""):
            raise ValueError(f"invalid event name {name!r}")
        if name not in self.events:
            self.events.append(name)

    def on_startup(self, func: Callable[[], Any]) -> Callable[[], Any]:
        self._startup.append(func)
        return func

    def on_shutdown(self, func: Callable[[], Any]) -> Callable[[], Any]:
        self._shutdown.append(func)
        return func

    def background(self, func: Callable[[], Awaitable[Any]]) -> Callable[[], Awaitable[Any]]:
        """Register an ``async def`` without arguments, started on startup and cancelled on shutdown."""
        if not inspect.iscoroutinefunction(func):
            raise TypeError("background tasks must be 'async def' functions without arguments")
        self._background.append(func)
        return func

    # -- events
    def emit(self, event: str, data: Any = None) -> dict:
        """Append to the ring buffer and queue delivery to ``WEBHOOKS_OUT``. Safe from any thread."""
        self.declare_event(event)
        record = self.event_log.append(event, data, self.name)
        self.webhooks.enqueue(record)
        return record

    # -- calling
    async def call(
        self,
        name: str,
        args: dict | None = None,
        *,
        confirm_id: str | None = None,
        confirmed: bool = False,
        allow_shortcut: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Run a tool and return a protocol v1 result dict (never raises for tool problems).

        ``confirmed=True`` skips the preview step for ``confirm=True`` tools unless the
        shortcut is disabled (``allow_shortcut=False`` or ``require_confirm_roundtrip``).
        ``idempotency_key`` (``trade``/``admin`` tools only; ignored for ``read``): the same key
        with the same tool + arguments within 10 minutes returns the earlier outcome instead
        of running the tool again.
        """
        token = _current.set(self)  # module-level emit()/get_stack() inside the tool target this stack
        try:
            return await self._call(name, args, confirm_id, confirmed, allow_shortcut, idempotency_key)
        finally:
            _current.reset(token)

    async def _call(self, name: str, args: Any, confirm_id: str | None, confirmed: bool,
                    allow_shortcut: bool | None, idempotency_key: str | None = None) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return error_body(ErrorCode.NOT_FOUND, f"unknown tool: {name}"[:200])
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return error_body(ErrorCode.INVALID_ARGS, "args must be a JSON object")
        try:
            validated = tool.args_model.model_validate(args)
        except ValidationError as exc:
            return error_body(ErrorCode.INVALID_ARGS, _format_validation_error(exc))
        kwargs = {k: getattr(validated, k) for k in tool.args_model.model_fields}
        dumped = validated.model_dump(mode="json")  # preview (JSON-safe)
        exact = validated.model_dump(mode="python")  # digests (keeps inf/nan distinct)

        key = None
        if idempotency_key is not None and tool.scope != "read":
            if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
                return error_body(ErrorCode.INVALID_ARGS, "idempotency_key must be a string of 1-200 characters")
            key = idempotency_key
            state, previous = self.idempotency.begin(key, tool.name, args_digest(tool.name, exact))
            if state == "done":
                return {**previous, "idempotent_replay": True}  # type: ignore[dict-item]
            if state == "running":
                body = error_body(ErrorCode.IN_PROGRESS,
                                  "a call with this idempotency_key is still running; outcome unknown")
                body["error"]["outcome_unknown"] = True
                return body
            if state == "conflict":
                return error_body(ErrorCode.IDEMPOTENCY_CONFLICT,
                                  "idempotency_key was already used with a different tool or arguments")
        result = await self._guarded(tool, kwargs, dumped, exact, confirm_id, confirmed, allow_shortcut, key)
        return result

    async def _guarded(self, tool: Tool, kwargs: dict, dumped: dict, exact: dict, confirm_id: str | None,
                       confirmed: bool, allow_shortcut: bool | None, key: str | None) -> dict:
        """Confirmation step + execution. Releases the idempotency key if the tool never started."""
        if tool.confirm:
            if confirm_id:
                problem = self.confirmations.consume(confirm_id, tool.name, exact)
                if problem is not None:
                    if key:
                        self.idempotency.forget(key)
                    return error_body(problem, {
                        ErrorCode.CONFIRM_UNKNOWN: "confirm_id is unknown or was already used",
                        ErrorCode.CONFIRM_EXPIRED: "confirm_id expired; request a new preview",
                        ErrorCode.CONFIRM_MISMATCH: "confirm_id was issued for a different tool or arguments",
                    }[problem])
            else:
                if allow_shortcut is None:
                    shortcut = not self.require_confirm_roundtrip
                    if tool.scope in ("trade", "admin") and not self.allow_onestep_confirm:
                        shortcut = False  # money-moving / admin tools: two steps unless explicitly allowed
                else:
                    shortcut = allow_shortcut
                if not (confirmed and shortcut):
                    if key:
                        self.idempotency.forget(key)
                    return await self._needs_confirmation(tool, kwargs, dumped, exact)
        return await self._execute(tool, kwargs, key)

    async def _needs_confirmation(self, tool: Tool, kwargs: dict, dumped: dict, exact: dict) -> dict:
        preview: Any = {"tool": tool.name, "description": tool.description, "args": dumped}
        if tool.preview is not None:
            try:
                preview = to_jsonable_python(await _maybe_await(tool.preview, **kwargs), fallback=str)
            except ToolError as exc:
                return error_body(exc.code, redact(exc.message))
            except Exception:
                log.exception("preview of %s failed", tool.name)
                return error_body(ErrorCode.INTERNAL, "preview failed")
        confirm_id = self.confirmations.issue(tool.name, exact)
        return {"ok": False, "needs_confirmation": True, "confirm_id": confirm_id,
                "preview": preview, "expires_in": int(self.confirmations.ttl)}

    async def _execute(self, tool: Tool, kwargs: dict, key: str | None = None) -> dict:
        timeout = tool.timeout if tool.timeout is not None else self.tool_timeout
        task = asyncio.ensure_future(self._run_tool(tool, kwargs))
        if key:
            # record the real outcome even if it arrives after the caller saw "timeout"
            task.add_done_callback(lambda t: self.idempotency.finish(key, t.result())
                                   if not t.cancelled() else self.idempotency.forget(key))
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout or None)
        except asyncio.TimeoutError:
            if tool.is_async:
                task.cancel()  # coroutines can be stopped; a sync tool keeps running in its thread
            body = error_body(ErrorCode.TIMEOUT,
                              f"tool timed out after {timeout:g}s; the outcome is unknown (it may still "
                              "complete). Check the state before retrying"
                              + (" or retry with the same idempotency_key." if key else "."))
            body["error"]["outcome_unknown"] = True
            return body

    async def _run_tool(self, tool: Tool, kwargs: dict) -> dict:
        try:
            result = await _maybe_await(tool.func, **kwargs)
            return {"ok": True, "result": to_jsonable_python(result, fallback=str)}
        except ToolError as exc:
            return error_body(exc.code, redact(exc.message))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tool %s failed", tool.name)
            return error_body(ErrorCode.INTERNAL, "tool failed with an internal error")

    # -- manifest
    def manifest(self) -> dict:
        return {
            "protocol": PROTOCOL_VERSION,
            "stack": self.name,
            "version": self.version,
            "tools": [t.manifest() for t in self.tools.values()],
            "events": list(self.events),
            "commands": [{"name": c.name, "help": c.help} for c in self.commands.values()],
            "hooks": list(self.hooks),
            "plugin_errors": list(self.plugin_errors),
        }

    # -- lifecycle
    async def startup(self) -> None:
        await self.webhooks.start()
        token = _current.set(self)
        try:
            for func in self._startup:
                await _maybe_await(func)
            for func in self._background:
                self._bg_tasks.append(asyncio.create_task(self._run_bg(func), name=f"bg:{func.__qualname__}"))
        finally:
            _current.reset(token)

    async def _run_bg(self, func: Callable[[], Awaitable[Any]]) -> None:
        try:
            await func()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background task %s crashed", func.__qualname__)

    async def shutdown(self) -> None:
        for task in self._bg_tasks:
            task.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()
        for func in reversed(self._shutdown):
            try:
                await _maybe_await(func)
            except Exception:
                log.exception("shutdown hook %s failed", getattr(func, "__qualname__", func))
        await self.webhooks.stop()


# --------------------------------------------------------------------------- default stack

_default_stack = Stack()
_current: contextvars.ContextVar[Stack | None] = contextvars.ContextVar("stack_kit_current", default=None)


def get_stack() -> Stack:
    """The stack module-level decorators register into (plugin loading temporarily overrides it)."""
    return _current.get() or _default_stack


def set_default_stack(stack: Stack) -> Stack:
    global _default_stack
    _default_stack = stack
    return stack


def configure(name: str | None = None, version: str | None = None, *, events: list[str] | None = None) -> Stack:
    """Set name/version (and declared events) of the current stack. Returns it."""
    stack = get_stack()
    if name:
        stack.name = name
    if version:
        stack.version = version
    for event in events or []:
        stack.declare_event(event)
    return stack


@contextlib.contextmanager
def use_stack(stack: Stack):
    """Make ``stack`` the target of module-level ``@tool``/``@hook``/``emit`` inside the block."""
    token = _current.set(stack)
    try:
        yield stack
    finally:
        _current.reset(token)


def tool(name: str, description: str | None = None, *, confirm: bool = False, scope: Scope = "read",
         preview: Callable[..., Any] | None = None, timeout: float | None = None):
    """Register a function as a tool on the current stack. See :meth:`Stack.tool`."""
    return get_stack().tool(name, description, confirm=confirm, scope=scope, preview=preview, timeout=timeout)


def hook(name: str, *, body_secret_field: str | None = None):
    """Register an incoming webhook handler on the current stack. See :meth:`Stack.hook`."""
    return get_stack().hook(name, body_secret_field=body_secret_field)


def emit(event: str, data: Any = None) -> dict:
    """Emit an event on the current stack. See :meth:`Stack.emit`."""
    return get_stack().emit(event, data)


def on_startup(func: Callable[[], Any]) -> Callable[[], Any]:
    return get_stack().on_startup(func)


def on_shutdown(func: Callable[[], Any]) -> Callable[[], Any]:
    return get_stack().on_shutdown(func)


def background(func: Callable[[], Awaitable[Any]]) -> Callable[[], Awaitable[Any]]:
    return get_stack().background(func)
