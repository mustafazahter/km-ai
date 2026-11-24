English · [Türkçe](tr/LLM-PROVIDERS.md)

# LLM providers

> Pick a provider in the panel (Kurulum > Yapay zekâ modeli) or with `LLM_PROVIDER`. Every provider except
> Anthropic is reached through its OpenAI compatible `/chat/completions`; Anthropic uses a native Messages API
> adapter (tool_use/tool_result). "Bağlan" fetches the provider's model list. Base URLs are from the providers'
> public docs; none was called live from this repo (fake servers in tests).

Choice: panel → **Kurulum → Yapay zekâ modeli** (UI label: "Setup → AI model"; cards) or `LLM_PROVIDER` (default `auto`: inferred from the address). When you enter the key and press **Bağlan** ("Connect"), the model list arrives automatically (searchable list, last used on top, a badge if tool support is known); if the list does not arrive, type the model name by hand. Keys are write-only and are never shown back. The saved key is sent only to the host of the saved address. The address of cloud presets is **fixed** (cannot be changed); local providers may go to localhost/private network; the "Özel" ("Custom") provider by default goes only to public addresses, for local/private network the box is checked (`LLM_ALLOW_PRIVATE_NETWORK`). Link-local, cloud metadata, `0.0.0.0` and this service's own port are always blocked: [SECURITY.md §6b](SECURITY.md#6b-network-policy-addresses-entered-in-the-panel).

| Provider (`LLM_PROVIDER`) | Base URL preset | Model list | Tool calling | Status |
|---|---|---|---|---|
| OpenRouter (`openrouter`) | `https://openrouter.ai/api/v1` | `GET /models` (`supported_parameters` per model → badge) | depends on model | from docs; not tried live |
| OpenAI (`openai`) | `https://api.openai.com/v1` | `GET /models` | yes | from docs; not tried live |
| Anthropic (`anthropic`) | `https://api.anthropic.com` | `GET /v1/models` + `x-api-key`, `anthropic-version: 2023-06-01` | yes (native adapter) | from docs; not tried live |
| Google Gemini (`gemini`) | `https://generativelanguage.googleapis.com/v1beta/openai` | `GET /models` (the `models/` prefix is stripped) | yes | from docs; not tried live; stripping the `models/` prefix was not verified |
| xAI Grok (`xai`) | `https://api.x.ai/v1` | `GET /models` | yes | from docs; not tried live |
| Groq (`groq`) | `https://api.groq.com/openai/v1` | `GET /models` | depends on model | from docs; not tried live |
| DeepSeek (`deepseek`) | `https://api.deepseek.com/v1` | `GET /models` | yes | from docs; not tried live |
| Mistral (`mistral`) | `https://api.mistral.ai/v1` | `GET /models` | yes | from docs; not tried live |
| Together (`together`) | `https://api.together.xyz/v1` | `GET /models` | depends on model | from docs; not tried live |
| Perplexity (`perplexity`) | `https://api.perplexity.ai` | none: type the model name by hand (`sonar`, `sonar-pro`) | none | from docs; **not verified** |
| Ollama (`ollama`) | `http://127.0.0.1:11434/v1` | `GET /api/tags` (otherwise `/v1/models`) | depends on model | tried in the browser with a fake server |
| LM Studio (`lmstudio`) | `http://127.0.0.1:1234/v1` | `GET /models` | depends on model | with a fake server |
| vLLM (`vllm`) | `http://127.0.0.1:8000/v1` | `GET /models` | depends on model | tried in the browser with a fake server |
| Custom (`custom`) | you type it yourself | `GET /models` | — | — |

A local server on the host machine, reached from inside Docker: `http://host.docker.internal:11434/v1`.

**Anthropic adapter** (`src/ai_bot/llm.py` → `AnthropicClient`, `to_anthropic`, `from_anthropic`): OpenAI-format messages are converted to `POST /v1/messages`. The system prompt goes into the separate `system` field (a system message in the middle of the conversation goes into a user turn with a `[system]` prefix); the assistant's `tool_calls` are converted to `tool_use` blocks, and `tool` messages to `tool_result` blocks in a single user turn; consecutive messages with the same role are merged; `max_tokens` is always sent; it is non-streaming. The `tool_use` blocks in the response are converted back to the OpenAI `tool_calls` format. The OpenAI-compatible Anthropic endpoint is not used because it is not recommended for production.

**Error messages** are in Turkish, with the provider name and chosen by status code: 401/403 key rejected, 402 balance, 429 rate/quota limit, 404 model or address not found, 5xx provider error. The key does not appear in any message.

> Verification: each provider's model list, chat, tool call and error paths were tested with a fake server in `tests/test_ui_v2.py`; the Anthropic mapping was tried with unit tests and in the agent loop (tool → tool_result → answer). No live requests were sent to real providers from this repo.

## Settings

| Env | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `auto` | Provider (table above); `auto` is inferred from the address |
| `LLM_BASE_URL` | OpenRouter | Endpoint address (`/chat/completions` is appended, or `/v1/messages` for Anthropic) |
| `LLM_API_KEY` | — | `Authorization: Bearer` (Anthropic: `x-api-key`); write-only from the panel |
| `LLM_MODEL` | `openai/gpt-4o-mini` | Model. Recommended: `deepseek/deepseek-v4.1-flash-1017` (DeepSeek V4.1 flash: cheap, calls tools, 1M context); the wizard suggests this one |
| `LLM_MODEL_CHOICES` | empty | Models selectable with `/model` (empty = free) |
| `LLM_MAX_TOKENS` | 1024 | Token limit per response |
| `LLM_TEMPERATURE` | 0.3 | |
| `LLM_TIMEOUT` | 60 | Request time limit (s) |
| `LLM_RETRIES` | 2 | Retries on 408/409/425/429/5xx and network errors (exponential backoff) |
| `MAX_TOOL_STEPS` | 6 | Maximum tool rounds in one message; when reached, a final answer without tools is requested |
| `TURN_TIMEOUT` | 180 | Total time of one message; once exceeded, no new round is started |
| `TOOL_RESULT_MAX_CHARS` | 6000 | Truncation limit for a tool result returned to the model |
| `SYSTEM_PROMPT_EXTRA` | empty | General system prompt: added on all channels and in the panel chat |
| `SYSTEM_PROMPT_TELEGRAM`, `_WHATSAPP`, `_TEAMS`, `_SLACK`, `_EMAIL` | empty | Added only in that channel's turns, after the general prompt (not added in the panel chat) |

`/model <name>` changes the model for that chat, `/model varsayilan` ("default") reverts it.

Provider-specific fix (header, field): `.claude/skills/add-llm-provider`.
