"""Minimal MCP server over Streamable HTTP (JSON responses only, stateless, single POST endpoint).

Supported: ``initialize``, ``notifications/initialized``, ``ping``, ``tools/list``, ``tools/call``,
JSON-RPC batches. Tool names are exposed with ``.`` replaced by ``_`` (``mt5.buy`` -> ``mt5_buy``).
``confirm=True`` tools are always two-step: the first call returns a preview and a ``_confirm_id``,
the second call must repeat the same arguments plus ``_confirm_id``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .registry import Stack, Tool

log = logging.getLogger("stack_kit.mcp")

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

CONFIRM_ARG = "_confirm_id"


def mcp_name(tool_name: str) -> str:
    return tool_name.replace(".", "_")


def negotiate_version(requested: Any) -> str:
    return requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION


def _error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _tool_entry(tool: Tool) -> dict:
    schema = json.loads(json.dumps(tool.input_schema))
    if tool.confirm:
        schema.setdefault("properties", {})[CONFIRM_ARG] = {
            "type": "string",
            "description": "Leave empty on the first call. To execute, repeat the call with the "
                           "confirm_id returned by the preview (single use, 120 s).",
        }
    description = tool.description or tool.name
    if tool.confirm:
        description += " (Requires confirmation: first call returns a preview and a _confirm_id.)"
    return {
        "name": mcp_name(tool.name),
        "title": tool.name,
        "description": description,
        "inputSchema": schema,
        "annotations": {
            "title": tool.name,
            "readOnlyHint": tool.scope == "read",
            "destructiveHint": tool.scope != "read",
            "openWorldHint": True,
        },
    }


def _text(text: str, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


class McpServer:
    """``max_batch``: JSON-RPC batch length limit; ``max_response_bytes``: limit on the serialised
    response (single or batch). Exceeding either returns ``-32600`` with HTTP 400."""

    def __init__(self, stack: Stack, *, max_batch: int = 100, max_response_bytes: int = 1_048_576):
        self.stack = stack
        self.max_batch = max_batch
        self.max_response_bytes = max_response_bytes

    def _tools_by_mcp_name(self) -> dict[str, Tool]:
        return {mcp_name(t.name): t for t in self.stack.tools.values()}

    async def handle_payload(self, payload: Any) -> tuple[int, Any]:
        """Return ``(http_status, json_body_or_None)``; ``None`` body means 202 Accepted."""
        if isinstance(payload, list):
            if not payload:
                return 400, _error(None, INVALID_REQUEST, "empty batch")
            if len(payload) > self.max_batch:
                return 400, _error(None, INVALID_REQUEST, f"batch too large (max {self.max_batch})")
            responses: list[dict] = []
            size = 2
            for message in payload:
                response = await self.handle_message(message)
                if response is None:
                    continue
                size += len(json.dumps(response, ensure_ascii=False)) + 1
                if size > self.max_response_bytes:
                    return 400, _error(None, INVALID_REQUEST, "response too large; send smaller batches")
                responses.append(response)
            return (200, responses) if responses else (202, None)
        if isinstance(payload, dict):
            response = await self.handle_message(payload)
            if response is not None and len(json.dumps(response, ensure_ascii=False)) > self.max_response_bytes:
                return 400, _error(payload.get("id") if isinstance(payload.get("id"), (str, int)) else None,
                                   INVALID_REQUEST, "response too large")
            return (200, response) if response is not None else (202, None)
        return 400, _error(None, INVALID_REQUEST, "request must be a JSON object or array")

    async def handle_message(self, msg: Any) -> dict | None:
        if not isinstance(msg, dict):
            return _error(None, INVALID_REQUEST, "invalid request")
        has_id = "id" in msg
        id_ = msg.get("id")
        if has_id and (isinstance(id_, bool) or not isinstance(id_, (str, int))):
            return _error(None, INVALID_REQUEST, "id must be a string or integer")
        method = msg.get("method")
        if msg.get("jsonrpc") != "2.0" or not isinstance(method, str):
            if msg.get("jsonrpc") == "2.0" and "method" not in msg and ("result" in msg or "error" in msg):
                return None  # a response from the client; nothing to answer
            return _error(id_ if has_id else None, INVALID_REQUEST, "invalid request")
        params = msg.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(id_, INVALID_PARAMS, "params must be an object") if has_id else None
        if not has_id:
            return None  # notifications (notifications/initialized, cancelled, ...) need no answer
        try:
            if method == "initialize":
                return _result(id_, {
                    "protocolVersion": negotiate_version(params.get("protocolVersion")),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.stack.name, "version": self.stack.version},
                    "instructions": f"Tools of the '{self.stack.name}' stack. Tools that move money or "
                                    "change state need a second call with _confirm_id.",
                })
            if method == "ping":
                return _result(id_, {})
            if method == "tools/list":
                return _result(id_, {"tools": [_tool_entry(t) for t in self.stack.tools.values()]})
            if method == "tools/call":
                return await self._tools_call(id_, params)
            return _error(id_, METHOD_NOT_FOUND, f"method not found: {method}"[:200])
        except Exception:
            log.exception("MCP %s failed", method)
            return _error(id_, INTERNAL_ERROR, "internal error")

    async def _tools_call(self, id_: Any, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(id_, INVALID_PARAMS, "tools/call needs name (string) and arguments (object)")
        tool = self._tools_by_mcp_name().get(name) or self.stack.tools.get(name)
        if tool is None:
            return _error(id_, INVALID_PARAMS, f"unknown tool: {name}"[:200])
        args = dict(arguments)
        confirm_id = args.pop(CONFIRM_ARG, None)
        if confirm_id is not None and not isinstance(confirm_id, str):
            return _result(id_, _text("error [invalid_args]: _confirm_id must be a string", True))
        result = await self.stack.call(tool.name, args, confirm_id=confirm_id or None,
                                       confirmed=False, allow_shortcut=False)
        if result.get("ok"):
            value = result.get("result")
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
            return _result(id_, _text(text, False))
        if result.get("needs_confirmation"):
            preview = json.dumps(result.get("preview"), ensure_ascii=False, indent=2)
            text = (
                "CONFIRMATION REQUIRED - the tool was NOT executed.\n"
                f"Preview:\n{preview}\n\n"
                f"Show this preview to the user. Only if the user explicitly approves, call "
                f"`{mcp_name(tool.name)}` again with exactly the same arguments plus "
                f"\"{CONFIRM_ARG}\": \"{result['confirm_id']}\" "
                f"(single use, expires in {result.get('expires_in', 120)} s)."
            )
            return _result(id_, _text(text, False))
        err = result.get("error") or {}
        return _result(id_, _text(f"error [{err.get('code', 'error')}]: {err.get('message', '')}", True))
