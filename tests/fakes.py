"""Test doubles: scripted LLM, fake Telegram API, helpers. No real network is ever used."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx


def tool_call(name: str, args: dict | str, call_id: str | None = None) -> dict:
    return {"id": call_id or f"c_{name}", "type": "function",
            "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}


def reply(content: str | None = None, calls: list[dict] | None = None) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return {"choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class FakeLLM:
    """OpenAI-compatible /chat/completions. ``script`` items: a response dict, an int status,
    or a callable(request_json) -> response dict. When the script runs out, ``default`` is used."""

    def __init__(self, script: list[Any] | None = None, default: Any = None):
        self.script = list(script or [])
        self.default = default if default is not None else reply("tamam")
        self.requests: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions"), request.url
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        item = self.script.pop(0) if self.script else self.default
        if callable(item):
            item = item(body)
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": "boom"}})
        return httpx.Response(200, json=item)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class FakeTelegram:
    """Records Bot API calls; every call succeeds."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._mid = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": []})
        self._mid += 1
        return httpx.Response(200, json={"ok": True, "result": {"message_id": self._mid, "chat": {"id": body.get("chat_id")}}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def sent(self, chat_id: int | None = None) -> list[dict]:
        return [b for m, b in self.calls if m == "sendMessage" and (chat_id is None or b.get("chat_id") == chat_id)]

    def texts(self, chat_id: int | None = None) -> list[str]:
        return [b["text"] for b in self.sent(chat_id)]

    async def wait_for(self, pred: Callable[[dict], bool], timeout: float = 3.0) -> dict:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            for b in self.sent():
                if pred(b):
                    return b
            await asyncio.sleep(0.01)
        raise AssertionError(f"no matching message; sent: {self.texts()}")


def message_update(chat_id: int, text: str, update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {"message_id": update_id, "chat": {"id": chat_id, "type": "private"},
                                                "from": {"id": chat_id}, "text": text}}


def callback_update(chat_id: int, data: str, message_id: int = 999) -> dict:
    return {"update_id": 9999, "callback_query": {"id": "cq1", "data": data, "from": {"id": chat_id},
                                                  "message": {"message_id": message_id, "chat": {"id": chat_id}}}}


def static_resolver(mapping: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise OSError("no such host")
        return mapping[host]
    return resolve
