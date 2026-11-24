English · [Türkçe](tr/DEVELOPER.md)

# Developer guide

> Extend ai-bot: write plugins (`plugins/*.py` with `@tool`), use the SSRF-safe fetcher, add bridges,
> embed ai-bot into another stack with `ai_bot.setup(stack, bot)`, run the tests with fake LLM/Telegram.

## Environment

```bash
uv venv --python 3.12
uv pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The tests never reach the real network: `tests/fakes.py` contains a fake LLM (`FakeLLM`), a fake Telegram (`FakeTelegram`) and a fixed DNS resolver; `tests/conftest.py::make_harness` sets them all up.

## Writing a plugin

`plugins/*.py` is loaded at startup. Every `@tool` is exposed to the AI, to `/bridge` and to `/mcp`.

```python
from stack_kit import ToolError, tool

@tool("not.ekle", "Kısa bir not kaydeder / Saves a short note", scope="read")
def ekle(metin: str) -> dict:
    if len(metin) > 200:
        raise ToolError("not çok uzun", code="invalid_args")
    return {"kaydedildi": metin}
```

Verify and call:

```bash
.venv/bin/python -m stack_kit tools
.venv/bin/python -m stack_kit call ornek.zar '{"sides": 6, "count": 2}'
```

Rules:

- The **description** decides when the AI picks the tool; write it clearly (TR + EN works well).
- **Money / irreversible work** → `confirm=True` and `scope="trade"` (or `"admin"`). The agent does not run these tools without user confirmation (a button on Telegram, a code on other channels). A tool with `scope` trade/admin asks for confirmation even without `confirm`.
- **Outbound request** → `get_stack().app_ai_bot.fetcher.fetch(url)` (SSRF-protected). Example: `plugins/haber_ozet.py`.
- A parameter named **`chat_id`** is hidden from the AI and filled with the current chat.
- **State**: `app.db` (SQLite, `Database`), `app.settings`, `app.scheduler`, `app.memory`, `app.deliver(chat_id, text)`.
- To hide a tool from the AI: `from ai_bot.agent import HIDDEN_FROM_AI; HIDDEN_FROM_AI.add("benim.aracim")`.
- If your tool returns external/untrusted text (web, e-mail, RSS): `from ai_bot.agent import UNTRUSTED_OUTPUT_TOOLS; UNTRUSTED_OUTPUT_TOOLS.add("benim.aracim")`. This way a memory save and a schedule in the same turn ask for user confirmation. Add a tool that writes something persistent to `PERSISTENT_TOOLS`.

Test pattern: `.claude/skills/add-ai-tool`, and `tests/test_confirm_flow.py` for tools that need confirmation.

## Adding a command (on every channel)

Commands live in a single registry (`stack.commands`); the kit's bot feeds Telegram, and `src/ai_bot/commands.py` feeds the other channels (WhatsApp, Teams, Slack, e-mail). Write it once, it works everywhere:

```python
# plugins/ping.py
from ai_bot.commands import command

@command("ping", "Canlılık testi / liveness", readonly=True)
async def ping(ctx):
    return "pong"
```

- `/name` or `!name` (Slack treats a `/...` message as its own command). A name that is not registered is a normal message and goes to the AI.
- `ctx`: `chat_id`, `args`, `arg_text`, `text`, `reply()`, `confirm()`, `run_tool()`; for channel commands also `ctx.channel`.
- `readonly=True`: a command that changes nothing. On e-mail the sender cannot be verified, so only read commands run (`/help`, `/hafiza` `/gorevler` `/model` without arguments, those marked `readonly`); the others get the reply "bu kanalda kapalı" (disabled on this channel).
- For a command that changes/deletes, use `await ctx.confirm("... yapılsın mı?")` (should ... be done?): a button on Telegram, an `EVET 123456` code on other channels, always `False` on e-mail.
- For a need specific to Telegram, the kit's `from stack_kit.telegram import command` can be used (it goes into the registry; `ctx.message` is empty on channels). `/start` and `/id` are Telegram-specific.
- Skill and test pattern: `.claude/skills/add-command/SKILL.md`, `tests/test_channel_commands.py`.

## Adding a bridge

Two envs are enough to expose another stack's tools to the AI (the kit merges them):

```
BRIDGE_<AD>_URL=https://diger-stack.example.com
BRIDGE_<AD>_TOKEN=<o stack'in BRIDGE_TOKEN'ı>
```

Details: [BRIDGES.md](BRIDGES.md).

## Embedding in another stack: `ai_bot.setup`

```python
import ai_bot
from stack_kit import configure, run
from stack_kit.telegram import TelegramBot

stack = configure(name="benim-stack")
bot = TelegramBot(stack=stack)
ai_bot.setup(stack, bot)            # araçlar, komutlar, serbest metin, zamanlayıcı
stack.background(bot.run)            # bot verildiyse yaşam döngüsü çağıranındır
stack.on_shutdown(bot.aclose)
run(stack)
```

- `bot=None`: ai-bot builds and starts its own bot from `TELEGRAM_BOT_TOKEN`.
- Existing commands (such as `/help`) are kept; ai-bot adds only the missing ones.
- Tools in the same process (e.g. `mt5.*`) appear to the AI without a bridge, and the confirmation rule is the same.

## Example scenario: connect your own project with a bridge

There is a working, tested example: [`examples/ornek-stack/kargo_stack.py`](../examples/ornek-stack/kargo_stack.py), a small cargo tracking stack (a read tool, a return tool that asks for confirmation, the cargo company's webhook and a `kargo.gecikti` event). It connects to km-ai with `BRIDGE_KARGO_URL` + `BRIDGE_KARGO_TOKEN`; the tools appear to the model as `kargo_durum`, `kargo_iade_baslat`, and a return runs only with user confirmation. The step-by-step walkthrough is in the main README: **Developer area > Example scenario**. Test: `tests/test_example_stack.py` (connects the stack to km-ai with the real bridge client).

## Project map

| Path | What |
|---|---|
| `src/ai_bot/agent.py` | Tool loop, confirmation rule, prompt-injection lock, system prompt |
| `src/ai_bot/llm.py` | OpenAI-compatible client |
| `src/ai_bot/builtin_tools.py` | Built-in tools |
| `src/ai_bot/telegram_ui.py` | Commands and free text |
| `src/ai_bot/safefetch.py` | SSRF-protected fetcher |
| `src/ai_bot/search.py` | Search providers |
| `src/ai_bot/scheduler.py`, `timeparse.py` | Scheduler, cron |
| `src/ai_bot/memory.py`, `db.py` | Memory, SQLite |
| `scripts/entrypoint.sh` | Container entry: only a `/data` writability warning (does not touch ownership) |
| `stack_kit/` | Vendored kit (do not edit here) |
