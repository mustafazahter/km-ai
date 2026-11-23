"""stack_kit — shared bridge library of komuta-stack (bridge protocol v1).

Self-contained: depends only on fastapi, uvicorn, httpx and pydantic>=2, so stack repositories
can vendor this directory as-is.
"""

__version__ = "1.0.0"

from .app import BridgeConfig, FailLimiter, HookRequest, create_app, run
from .bridges import Bridge, Bridges, bridges_from_env, parse_bridges
from .confirm import CONFIRM_TTL, ConfirmStore
from .errors import BridgeError, ErrorCode, ToolError
from .events import (
    EventLog,
    WebhookDispatcher,
    WebhookTarget,
    hook_signature,
    parse_webhooks_out,
    webhooks_out_from_env,
    sign,
    verify_hook_signature,
    verify_signature,
)
from .gate import SURFACES as GATE_SURFACES, BridgeGate
from .plugins import load_plugins
from .redact import redact, register_secret
from .registry import (
    PROTOCOL_VERSION,
    Command,
    Hook,
    Stack,
    Tool,
    background,
    configure,
    emit,
    get_stack,
    hook,
    on_shutdown,
    on_startup,
    set_default_stack,
    tool,
    use_stack,
)
from .settings import env_bool, env_float, env_int, env_list, env_str, load_dotenv, secret

__all__ = [
    "__version__", "PROTOCOL_VERSION", "CONFIRM_TTL",
    # registry
    "Stack", "Tool", "Hook", "Command", "tool", "hook", "emit", "on_startup", "on_shutdown",
    "background", "configure", "get_stack", "set_default_stack", "use_stack", "load_plugins",
    # app
    "create_app", "run", "BridgeConfig", "HookRequest", "FailLimiter",
    # errors
    "ToolError", "BridgeError", "ErrorCode",
    # events
    "EventLog", "WebhookDispatcher", "WebhookTarget", "parse_webhooks_out", "webhooks_out_from_env", "sign",
    "verify_signature", "hook_signature", "verify_hook_signature",
    # gate
    "BridgeGate", "GATE_SURFACES",
    # bridges
    "Bridges", "Bridge", "parse_bridges", "bridges_from_env",
    # confirm
    "ConfirmStore",
    # settings / secrets
    "env_str", "env_int", "env_float", "env_bool", "env_list", "secret", "load_dotenv",
    "redact", "register_secret",
]
