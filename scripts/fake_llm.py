"""Sahte (deterministik) OpenAI uyumlu LLM sunucusu: yerel deneme ve curl doğrulaması için.
Fake OpenAI-compatible LLM for local tries (no API key, no internet).

    .venv/bin/python scripts/fake_llm.py            # http://127.0.0.1:9999/v1
    LLM_BASE_URL=http://127.0.0.1:9999/v1 ...       # ai-bot'u buna yönelt

Kurallar: mesaj "zar" içeriyorsa `ornek_zar` aracını, "hatırla:" ile başlıyorsa `memory_remember`
aracını, "bağış" içeriyorsa onaylı `ornek_bagis` aracını çağırır; araç sonucu gelince onu metin olarak döner; diğer her şeye "Sahte LLM: <mesaj>".
"""

import json
import os
import sys

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()


def _tool_call(name: str, args: dict) -> dict:
    return {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@app.get("/v1/models")
async def models() -> dict:
    prices = {"fake-large": ("0.000003", "0.000015"), "fake-mini": ("0.00000015", "0.0000006"), "other/model-a": ("0", "0")}
    return {"object": "list", "data": [{"id": m, "object": "model", **({"pricing": {"prompt": prices[m][0], "completion": prices[m][1]}} if m in prices else {})}
                                       for m in ("fake-model", "fake-large", "fake-mini", "other/model-a", "other/model-b")]}


@app.post("/v1/chat/completions")
async def chat(request: Request) -> dict:
    body = await request.json()
    last = body["messages"][-1]
    tools = {t["function"]["name"] for t in body.get("tools") or []}
    message: dict = {"role": "assistant", "content": None}
    if last["role"] == "tool":
        message["content"] = f"Araç sonucu / tool result: {last['content']}"
    else:
        text = str(last.get("content") or "")
        if "zar" in text.lower() and "ornek_zar" in tools:
            message["tool_calls"] = [_tool_call("ornek_zar", {"sides": 6, "count": 2})]
        elif "bağış" in text.lower() and "ornek_bagis" in tools:
            message["tool_calls"] = [_tool_call("ornek_bagis", {"miktar": 25})]
        elif text.lower().startswith("hatırla:") and "memory_remember" in tools:
            message["tool_calls"] = [_tool_call("memory_remember", {"content": text.split(":", 1)[1].strip()})]
        else:
            message["content"] = f"Sahte LLM: {text}"
    return {"id": "fake", "object": "chat.completion", "model": body.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("FAKE_LLM_PORT", "9999"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
