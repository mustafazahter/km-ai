"""Error types and the stable error codes of protocol v1."""

from __future__ import annotations


class ErrorCode:
    """Stable ``error.code`` values. Clients may switch on these strings."""

    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"
    INVALID_ARGS = "invalid_args"
    CONFIRM_UNKNOWN = "confirm_unknown"  # unknown or already used confirm_id
    CONFIRM_EXPIRED = "confirm_expired"
    CONFIRM_MISMATCH = "confirm_mismatch"  # confirm_id issued for another tool or other args
    TOOL_ERROR = "tool_error"  # default code of ToolError
    TIMEOUT = "timeout"  # error also carries outcome_unknown: true for tool calls
    IN_PROGRESS = "in_progress"  # same idempotency_key still running
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"  # key reused with other tool/arguments
    INTERNAL = "internal_error"
    UNREACHABLE = "unreachable"  # Bridges: remote stack could not be reached
    REMOTE_ERROR = "remote_error"  # Bridges: remote answered with something unexpected
    NAME_CONFLICT = "name_conflict"


class ToolError(Exception):
    """Raise inside a tool to return ``{ok:false, error:{code, message}}`` to the caller.

    The message is shown to the caller verbatim, so keep it user-facing and free of secrets.
    Any other exception becomes ``internal_error`` with a generic message.
    """

    def __init__(self, message: str, code: str = ErrorCode.TOOL_ERROR):
        super().__init__(message)
        self.code = code
        self.message = message


class BridgeError(Exception):
    """Raised by :class:`stack_kit.Bridges` for configuration problems (bad ``BRIDGES``, name conflicts)."""

    def __init__(self, message: str, code: str = ErrorCode.BAD_REQUEST):
        super().__init__(message)
        self.code = code
        self.message = message


def error_body(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}
