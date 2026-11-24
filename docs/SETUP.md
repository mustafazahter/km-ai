English · [Türkçe](tr/SETUP.md)

# Setup

> Step-by-step install: pick a chat channel in the wizard (Telegram needs a bot token and your chat id from `/id`; WhatsApp, Teams, Slack and e-mail have their own guides), get an OpenRouter (or other) key,
> then either paste `komuta.yaml` into Komuta or run locally with docker compose / Python.

Contents: [1. Telegram bot token](#1-telegram-bot-token) · [2. Chat id](#2-chat-id-id) · [3. LLM key](#3-llm-key) · [4. Komuta](#4-setup-on-komuta) · [5. Docker compose](#5-local-with-docker-compose) · [6. Local with Python](#6-local-with-python-development) · [Troubleshooting](#troubleshooting)

## 1. Telegram bot token

1. In Telegram, open **@BotFather** and type `/newbot`.
2. Give the bot a display name and a username ending in `_bot`.
3. The text BotFather gives you in the form `123456789:AA...` is the bot token. Do not share it with anyone; do not write it into the repo.
4. (Optional) If you will use it in a group, go to BotFather → `/setprivacy` → Disable, otherwise the bot does not see plain messages in the group.

## 2. Chat id (`/id`)

The bot only answers chats in `ALLOWED_CHAT_IDS`. While the list is empty, **only `/id` and `/start`** work; this way you can learn your id safely:

1. Start the bot with `ALLOWED_CHAT_IDS` empty (on Komuta you can temporarily enter `0` in the Secret).
2. Type `/id` to the bot → a reply like `Chat ID: 12345678` comes back. In groups the id starts with a minus sign (`-100...`).
3. Write this value into `ALLOWED_CHAT_IDS` (comma-separated if there are several) and restart the bot.

To change the Secret value on Komuta, use the service → environment variables screen; then redeploy.

## 3. LLM key

The default provider is **OpenRouter** (many models with one key):

1. openrouter.ai → create an account → Keys → **Create Key**.
2. Add some credit or try the free models (take the `LLM_MODEL` name from the OpenRouter model list).
3. Pick a model that supports tool calling; recommended: `deepseek/deepseek-v4.1-flash-1017` (cheap, calls tools, 1M context; the wizard suggests this one, the built-in default is `openai/gpt-4o-mini`).

For another provider (OpenAI, Groq, DeepSeek, local Ollama...): [LLM-PROVIDERS.md](LLM-PROVIDERS.md).

## 4. Setup on Komuta

1. **Fork** this repo on GitHub (Komuta builds from source).
2. In the Komuta panel, create a **project** (a Stack does not create a project) and connect your GitHub account (Git connection).
3. In `komuta.yaml`, fill in the three placeholders: `<proje-adi>` (project name), `<git-baglanti-adi>` (Git connection name), `<kullanici>` (user; the owner of your fork).
4. (Optional) Validate locally: `.venv/bin/python ../stack-spec/tools/validate_stack.py komuta.yaml`.
5. `console.komuta.io/stacks/new` → **Source** (UI label: "Kaynak") → paste the file into the **komuta.yaml** box → **Review the plan** (UI label: "Planı incele").
6. **Publish** (UI label: "Yayınla"; there is no required Secret). When the build finishes, the service is ready at `/health`.
7. Service → **Logs** (UI label: "Loglar"): find the `Kurulum kodu` ("Setup code") line. Open `https://<servis-adresi>/ui/` (service address; Komuta sign-in first), set the panel password with the code and follow the wizard: channel choice, setup of the chosen channel (for Telegram the token and chat id), AI provider, search ([ADMIN-UI.md](ADMIN-UI.md)). If you want to supply values as Secrets, use `examples/komuta-env-ile.yaml`.

Package: `dz-dev` (0.5 CPU / 1 GiB, about 9 USD per month), disk `Small` (`/data`: SQLite). Optional manifests are under `examples/`:

| File | What it adds |
|---|---|
| `examples/komuta-mt5-ile.yaml` | Exposes the tools of the mt5-trader Stack to the AI (bridge) |
| `examples/komuta-n8n-ile.yaml` | Sends events to n8n; n8n asks the AI questions with `ai.ask` (bridge open from the manifest, no Komuta sign-in) |
| `examples/komuta-env-ile.yaml` | All settings from Secrets/environment |
| `examples/komuta-giris-kapali.yaml` | No Komuta sign-in, only the panel password |
| `examples/komuta-arama-saglayicili.yaml` | Reliable web search with Tavily |

## 5. Local with Docker compose

```bash
scripts/setup.sh            # asks questions, generates .env (BRIDGE_TOKEN is generated locally)
docker compose up --build
```

The image runs as uid 1000 and `/data` is owned by 1000 in the image; the default named volume needs no extra setup. If you use a bind mount (`./data:/data`), first run `chown 1000:1000 ./data` ([SECURITY.md](SECURITY.md#5-container-user-and-disk-ownership)).

> Note: the Docker image has not yet been built and tried in this repo (the Docker service was down at the time of writing). If you see a problem, open an issue.

## 6. Local with Python (development)

```bash
uv venv --python 3.12
uv pip install -r requirements-dev.txt
scripts/setup.sh
PYTHONPATH=src:. .venv/bin/python -m ai_bot
```

A fake LLM for trying it without a key:

```bash
.venv/bin/python scripts/fake_llm.py 9999
LLM_BASE_URL=http://127.0.0.1:9999/v1 BRIDGE_TOKEN=dev-token-change-me-0123456789 ALLOWED_CHAT_IDS=1 \
  HTTP_PORT=8091 PYTHONPATH=src:. .venv/bin/python -m ai_bot
curl -s -H "Authorization: Bearer dev-token-change-me-0123456789" -H 'Content-Type: application/json' \
  -d '{"args":{"prompt":"zar at"}}' http://127.0.0.1:8091/bridge/call/ai.ask
```

(`"zar at"` means "roll a die".)

## Troubleshooting

| Symptom | Fix |
|---|---|
| The bot never answers | Is `TELEGRAM_BOT_TOKEN` correct? Does the log say "token reddedildi" ("token rejected")? If the same token is polling somewhere else you will see 409; run a single instance. |
| Only `/id` answers | Your chat id is not in `ALLOWED_CHAT_IDS`. |
| "AI servisine ulaşılamadı" ("AI service unreachable") | Are `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` correct; does the model have credit? |
| `entrypoint: WARNING ... not writable` in the log | `/data` is not writable by the app user (uid 1000). If it is a local bind mount, run `chown 1000:1000 ./data`; the app does not change the disk ownership itself. |
| web.search "bot protection" | DuckDuckGo rejects automated requests from some networks; use `SEARCH_PROVIDER=tavily` or `brave`. |
| `/bridge` 401 | The `Authorization: Bearer <BRIDGE_TOKEN>` header is missing/wrong. |
