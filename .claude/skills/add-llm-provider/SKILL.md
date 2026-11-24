---
name: add-llm-provider
description: Add or change an LLM provider of km-ai (14 are built in: OpenRouter, OpenAI, Anthropic, Gemini, xAI, Groq, DeepSeek, Mistral, Together, Perplexity, Ollama, LM Studio, vLLM, custom) - a card in the setup wizard and the Ayarlar page, an auto-loaded model list - or adapt the client for a provider quirk, and verify with the fake LLM server. Use when the user wants another model provider or a local model.
---

# Add / change an LLM provider

The client `src/ai_bot/llm.py` (`LLMClient`) speaks the OpenAI `/chat/completions` format; most providers need no code, only env:

| Env | Example |
|---|---|
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` (default), `https://api.openai.com/v1`, `http://localhost:11434/v1` (Ollama) |
| `LLM_API_KEY` | the provider key (may be empty on a local server) |
| `LLM_PROVIDER` | `auto` (inferred from the address) or a provider id (`openrouter`, `openai`, `anthropic`, `deepseek`, `ollama` ...) |
| `LLM_MODEL` | the provider's model name; it must support tool calling (tools). Recommended: `deepseek/deepseek-v4.1-flash-1017` (OpenRouter); the built-in default `openai/gpt-4o-mini` stayed the same so running installs do not change, do not change it |

0. **New provider card:** add `Provider(id, name, base_url, model=<recommended model>, hint=..., ...)` to the `PROVIDERS` list in `src/ai_bot/providers.py`. The wizard pre-fills this model. A cloud provider's key goes only to the host of the saved address (`panel._llm_target`), and private-network addresses follow the `netpolicy.py` rules; do not loosen them. If there is a model list endpoint, wire it with `models=`. Test: `tests/test_ui.py` (`test_llm_*`).
1. Try it end to end locally with the fake server (no key or internet needed):
   ```bash
   .venv/bin/python scripts/fake_llm.py 9999
   LLM_BASE_URL=http://127.0.0.1:9999/v1 BRIDGE_TOKEN=dev-token-change-me-0123456789 ALLOWED_CHAT_IDS=1 \
     HTTP_PORT=8091 PYTHONPATH=src:. .venv/bin/python -m ai_bot
   curl -s -H "Authorization: Bearer dev-token-change-me-0123456789" -H 'Content-Type: application/json' \
     -d '{"args":{"prompt":"zar at"}}' http://127.0.0.1:8091/bridge/call/ai.ask
   ```
2. If the provider wants a different header/field, change it in `LLMClient.__init__` (headers) or `LLMClient.chat` (body); only `choices[0].message` is read from the reply. For Anthropic (`/v1/messages`) there is a separate local adapter in `llm.py` (`kind="anthropic"`); do not apply its format to an OpenAI-compatible provider.
3. Test: `tests/test_agent.py` uses the fake LLM (`tests/fakes.py::FakeLLM`); add a test there for new behaviour:
   ```bash
   .venv/bin/python -m pytest tests/test_agent.py
   ```
4. On Komuta: in `komuta.yaml`, `LLM_BASE_URL` and `LLM_MODEL` are literals, and the key is the `llm-api-key` Secret. Docs: `docs/LLM-PROVIDERS.md` and `docs/tr/LLM-PROVIDERS.md`.
