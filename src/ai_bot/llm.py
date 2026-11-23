"""Minimal OpenAI-compatible ``/chat/completions`` client (httpx, no SDK).

Works with OpenRouter (default), OpenAI, Groq, Together, DeepSeek, a local Ollama / LM Studio /
vLLM server — anything that speaks the OpenAI chat format with ``tools``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from stack_kit import redact

log = logging.getLogger("ai_bot.llm")

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class LLMError(Exception):
    """The model endpoint failed. ``message`` is safe to show (no secrets, no raw body)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


class LLMClient:
    def __init__(self, base_url: str, api_key: str | None, *, timeout: float = 60.0, retries: int = 2,
                 transport: httpx.AsyncBaseTransport | None = None, backoff: float = 1.0,
                 app_name: str = "km-ai"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.retries = max(0, retries)
        self.backoff = backoff
        headers = {"Content-Type": "application/json", "X-Title": app_name}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(self, *, model: str, messages: list[dict], tools: list[dict] | None = None,
                   max_tokens: int | None = None, temperature: float | None = None) -> dict:
        """Return the assistant message dict (``content`` and optional ``tool_calls``) plus ``usage``."""
        body: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if max_tokens:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        url = f"{self.base_url}/chat/completions"
        last: LLMError | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                await asyncio.sleep(min(self.backoff * 2 ** (attempt - 1), 10))
            try:
                resp = await self._client.post(url, json=body)
            except httpx.TimeoutException:
                last = LLMError("model request timed out")
                continue
            except httpx.HTTPError as exc:
                last = LLMError(f"model endpoint unreachable ({type(exc).__name__})")
                continue
            if resp.status_code in RETRY_STATUS:
                last = LLMError(f"model endpoint returned HTTP {resp.status_code}", resp.status_code)
                continue
            if resp.status_code >= 400:
                raise LLMError(f"model endpoint returned HTTP {resp.status_code}: {_error_text(resp)}",
                               resp.status_code)
            try:
                data = resp.json()
            except ValueError:
                last = LLMError("model endpoint returned invalid JSON")
                continue
            if isinstance(data, dict) and data.get("error") and not data.get("choices"):
                err = data["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                last = LLMError(f"model error: {redact(str(msg))[:200]}")
                continue
            try:
                message = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError):
                last = LLMError("model response has no choices")
                continue
            if not isinstance(message, dict):
                last = LLMError("model response message is not an object")
                continue
            message = dict(message)
            message["_usage"] = data.get("usage") or {}
            return message
        assert last is not None
        log.warning("LLM call failed after %d attempt(s): %s", self.retries + 1, last.message)
        raise last


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error") if isinstance(data, dict) else None
        msg = err.get("message") if isinstance(err, dict) else err
        return redact(str(msg or ""))[:200]
    except ValueError:
        return ""


# --------------------------------------------------------------------------- Anthropic (native)

ANTHROPIC_VERSION = "2023-06-01"


def to_anthropic(messages: list[dict], tools: list[dict] | None = None) -> tuple[str, list[dict], list[dict]]:
    """Map OpenAI chat messages (+ tools) to Anthropic Messages API ``(system, messages, tools)``.

    * every ``system`` message goes to the separate ``system`` field (first ones) or, when it appears
      after the conversation started, into a user turn marked ``[system]`` (Anthropic has no mid-chat
      system role);
    * assistant ``tool_calls`` become ``tool_use`` blocks; ``tool`` messages become ``tool_result``
      blocks inside a user turn (consecutive results share one turn);
    * consecutive turns of the same role are merged (the API needs user/assistant alternation) and the
      conversation always starts with a user turn.
    """
    system_parts: list[str] = []
    out: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        blocks = [b for b in blocks if not (b.get("type") == "text" and not b.get("text"))]
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        role = m.get("role")
        content = m.get("content")
        text = content if isinstance(content, str) else ("" if content is None else json.dumps(content, ensure_ascii=False))
        if role == "system":
            if not out:
                system_parts.append(text)
            else:
                push("user", [{"type": "text", "text": f"[system] {text}"}])
        elif role == "assistant":
            blocks: list[dict] = [{"type": "text", "text": text}] if text else []
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                raw = fn.get("arguments")
                try:
                    args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
                except (ValueError, TypeError):
                    args = {}
                blocks.append({"type": "tool_use", "id": str(call.get("id") or "call"), "name": str(fn.get("name") or ""),
                               "input": args if isinstance(args, dict) else {}})
            push("assistant", blocks)
        elif role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": str(m.get("tool_call_id") or ""),
                           "content": text}])
        else:
            push("user", [{"type": "text", "text": text}])
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(start)"}]})
    a_tools = []
    for t in tools or []:
        fn = t.get("function") or {}
        a_tools.append({"name": fn.get("name"), "description": fn.get("description") or "",
                        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return "\n\n".join(p for p in system_parts if p), out, a_tools


def from_anthropic(data: dict) -> dict:
    """Map an Anthropic response to the OpenAI assistant message shape used by the agent."""
    texts: list[str] = []
    calls: list[dict] = []
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            calls.append({"id": str(block.get("id") or f"call_{len(calls)}"), "type": "function",
                          "function": {"name": str(block.get("name") or ""),
                                       "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)}})
    message: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if calls:
        message["tool_calls"] = calls
    usage = data.get("usage") or {}
    message["_usage"] = {"prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens")}
    return message


class AnthropicClient(LLMClient):
    """Anthropic Messages API (``POST {base}/v1/messages``), non-streaming, with tool use.
    Same ``chat()`` contract as :class:`LLMClient` (OpenAI shaped in and out)."""

    def __init__(self, base_url: str, api_key: str | None, *, timeout: float = 60.0, retries: int = 2,
                 transport: httpx.AsyncBaseTransport | None = None, backoff: float = 1.0,
                 app_name: str = "km-ai"):
        base = (base_url or "https://api.anthropic.com").rstrip("/")
        self.base_url = base[:-3] if base.endswith("/v1") else base
        self.api_key = api_key
        self.retries = max(0, retries)
        self.backoff = backoff
        headers = {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
        if api_key:
            headers["x-api-key"] = api_key
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers)

    async def chat(self, *, model: str, messages: list[dict], tools: list[dict] | None = None,
                   max_tokens: int | None = None, temperature: float | None = None) -> dict:
        system, a_messages, a_tools = to_anthropic(messages, tools)
        body: dict[str, Any] = {"model": model, "messages": a_messages, "max_tokens": max_tokens or 1024}
        if system:
            body["system"] = system
        if a_tools:
            body["tools"] = a_tools
            body["tool_choice"] = {"type": "auto"}
        if temperature is not None:
            body["temperature"] = max(0.0, min(float(temperature), 1.0))
        url = f"{self.base_url}/v1/messages"
        last: LLMError | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                await asyncio.sleep(min(self.backoff * 2 ** (attempt - 1), 10))
            try:
                resp = await self._client.post(url, json=body)
            except httpx.TimeoutException:
                last = LLMError("model request timed out")
                continue
            except httpx.HTTPError as exc:
                last = LLMError(f"model endpoint unreachable ({type(exc).__name__})")
                continue
            if resp.status_code in RETRY_STATUS or resp.status_code == 529:  # 529 = overloaded
                last = LLMError(f"model endpoint returned HTTP {resp.status_code}", resp.status_code)
                continue
            if resp.status_code >= 400:
                raise LLMError(f"model endpoint returned HTTP {resp.status_code}: {_error_text(resp)}",
                               resp.status_code)
            try:
                data = resp.json()
            except ValueError:
                last = LLMError("model endpoint returned invalid JSON")
                continue
            if not isinstance(data, dict) or data.get("type") == "error":
                err = data.get("error") if isinstance(data, dict) else None
                msg = err.get("message") if isinstance(err, dict) else "unknown error"
                last = LLMError(f"model error: {redact(str(msg))[:200]}")
                continue
            return from_anthropic(data)
        assert last is not None
        log.warning("Anthropic call failed after %d attempt(s): %s", self.retries + 1, last.message)
        raise last
