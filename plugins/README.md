# plugins/

> Drop `*.py` files here. Every `@tool` becomes callable by the AI, over `/bridge` and over MCP.
> Money-moving tools must be `confirm=True` (+ `scope="trade"`): the AI cannot run them without a Telegram button press.

`*.py` files in this folder are loaded at startup (files starting with `_` are skipped). Every `@tool`:

- is visible to the AI (`ornek.zar` → `ornek_zar`),
- can be called with `POST /bridge/call/ornek.zar` and as the MCP tool `ornek_zar`.

| File | What |
|---|---|
| `ornek_arac.py` | The smallest example (`ornek.zar`) and a confirmed-tool example (`ornek.bagis`, `confirm=True`, `scope="trade"`) |
| `haber_ozet.py` | A real example: RSS headlines (`haber.basliklar`), through the SSRF-safe `app.fetcher` |

Rules: money movement → `confirm=True`; outbound request → `get_stack().app_ai_bot.fetcher`; error → `ToolError`.
Step by step: `../docs/DEVELOPER.md` and `.claude/skills/add-ai-tool`.
