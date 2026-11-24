English · [Türkçe](tr/FAQ.md)

# FAQ

> FAQ — costs, models, privacy, search reliability, groups, unified service with mt5-trader.

**How much does it cost?** Komuta: `dz-dev` about 9 USD per month + a `Small` disk. LLM: depends on your provider and model; small models such as `deepseek/deepseek-v4.1-flash-1017` are low-cost for everyday personal use (on OpenRouter on 23 November 2026, roughly 0.03 USD per million input tokens / 0.20 USD per million output tokens; check OpenRouter for the current price). The exact price is shown by the Komuta plan and your provider's dashboard.

**Which model?** Any model that supports tool calling. Recommended: `deepseek/deepseek-v4.1-flash-1017` (OpenRouter; DeepSeek V4.1 flash): the wizard suggests it. The built-in default `openai/gpt-4o-mini` stayed the same so that working installs do not change. For always the newest flash, use `~deepseek/deepseek-flash-latest`. See [LLM-PROVIDERS.md](LLM-PROVIDERS.md).

**Can I use a local model?** Yes: for example `LLM_BASE_URL=http://localhost:11434/v1` (Ollama); the key can stay empty. There is no local model on Komuta; you need a reachable server.

**Web search sometimes returns no results.** The default DuckDuckGo is keyless and some networks reject its automated requests. Use `SEARCH_PROVIDER=tavily` (or `brave`, `searxng`): `examples/komuta-arama-saglayicili.yaml`.

**Where is my data?** `DATA_DIR/ai-bot.sqlite3` (the service's `/data` disk on Komuta): chat history (short), memories, tasks. Messages go to your LLM provider; check the privacy policy there.

**How do I delete the history?** `/sifirla` deletes the chat history, `/hafiza temizle` deletes the memories.

**Does it work in a group?** Yes; add the group's ID (with a minus sign) to `ALLOWED_CHAT_IDS` and turn off privacy mode in BotFather. Everyone in the group can use the bot and press the confirmation button; if money tools are connected, choose the group carefully.

**Can the AI open a trade for me?** Only if you press **Confirm** on Telegram. The AI cannot give the confirmation itself. See [SECURITY.md](SECURITY.md#3-confirmation-rule-money--actions).

**Does it work in a single service with mt5-trader?** Yes, mt5-trader can add ai-bot to its own process with `ai_bot.setup(stack, bot)`; details are in the mt5-trader repo. As a separate Stack, the bridge: [BRIDGES.md](BRIDGES.md).

**I want `/start` to show a different text.** `/start` and `/id` are built into the kit (because they must work even when the allow list is empty). The welcome text is in `/help`: `src/ai_bot/telegram_ui.py` → `HELP`.

**I ran two instances with the same token and got a 409 error.** Telegram allows only one polling connection per bot; shut down one instance.

**Was the Docker image tested?** It has not yet been built and tried in this repo; the Python tests and a bridge/MCP verification with a real uvicorn were done.
