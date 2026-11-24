---
name: add-command
description: Add a slash command (/ping, /rapor ...) that works on EVERY channel of km-ai - Telegram, WhatsApp, Microsoft Teams, Slack and e-mail - from one handler, with the right read-only/confirmation behaviour and a test. Use when the user wants a new command people can type in chat.
---

# Add a command (works on every channel)

A command = an action that runs, without going to the AI, when the user types `/name` (or `!name`) in the chat. One registry (`stack.commands`) feeds Telegram and the other channels (WhatsApp, Teams, Slack, e-mail) alike; you write it once and it works on all of them. Ready-made commands: `/help`, `/hafiza`, `/gorevler`, `/model`, `/sifirla` (`src/ai_bot/telegram_ui.py`). `/start` and `/id` exist only on Telegram.

1. Write `plugins/<name>.py` (loaded at startup):
   ```python
   from ai_bot.commands import command

   @command("ping", "Canlılık testi / liveness", readonly=True)
   async def ping(ctx):
       kanal = ctx.channel.name if getattr(ctx, "channel", None) else "telegram"
       return f"pong ({kanal}) {ctx.arg_text}".strip()
   ```
   - A command that uses `ctx.reply`/`confirm`/`run_tool` must be `async def`; a plain `def` command runs in a separate thread (so it does not stall the other channels) and can only compute and return text. For bridge tools the approval prompt shows the arguments, not the remote tool's own preview.
   - The return value is the reply text (`None` = no reply). The text may be in the `"Türkçe / English"` form.
   - `ctx`: `chat_id` (a stable negative number for a channel user), `args` (list), `arg_text` (what follows the command), `text`, `reply(text)`, `confirm(text)` (an approval code or button; `False` if not on a channel), `run_tool(name, args)` (the same approval rule as the AI's tool loop: a tool with `confirm=True` or a `trade`/`admin` scope, and bridge tools, do not run without the same sender confirming with a code; refused on e-mail). Channel commands also have `ctx.channel`; Telegram does not.
   - Name: lowercase, digits, `_`, at most 32 characters. Registering the same name twice raises an error.
2. The **`readonly`** rule (security): `readonly=True` only for a command that changes nothing. On e-mail the sender cannot be verified (no `can_confirm`), so only read commands run; a command not marked `readonly` gets "disabled on this channel" there. `readonly` applies only to the command's own behaviour; `ctx.run_tool` still enforces the approval rule separately. The registration belongs to the stack the command is written in. For a command that deletes, changes, or triggers money/work, do not set `readonly` and ask the user with `await ctx.confirm("... go ahead?")` (a button in Telegram, an `EVET 123456` code on other channels, always `False` on e-mail).
3. Entry rules (do not change): a command is accepted only from allow-listed senders (the channel pipeline checks first); both `/name` and `!name` work (Slack treats a `/...` message as its own command, so type `!name` there); an unregistered name is a normal message and goes to the AI; if a command raises `ToolError` its message (secrets masked) goes to the user; any other error goes to the user as the generic "Komut çalışırken hata oluştu" (an error occurred while running the command) and the detail is written only to the log (no secrets or bodies).
4. Test (`tests/test_<name>.py`) with the channel fixture (the `tests/test_channel_commands.py` pattern):
   ```python
   import asyncio
   from stack_kit import use_stack
   from ai_bot import commands
   from ai_bot.channels.base import Inbound
   from tests.test_channels_env import ALICE, chenv  # noqa: F401

   def test_ping_on_whatsapp(chenv):
       chenv.whatsapp()
       with use_stack(chenv.stack):
           @commands.command("ping", "test", readonly=True)
           def ping(ctx):
               return "pong"
       ch = chenv.mgr.channels["whatsapp"]
       sent = []
       async def send(ctx, text): sent.append(text)
       ch.send = send
       asyncio.run(ch.process(Inbound(sender=ALICE, text="/ping", msg_id=None, ctx={})))
       assert sent == ["pong"] and chenv.llm.requests == []   # did not go to the AI
   ```
   For e-mail behaviour use `chenv.set(EMAIL_ENABLED=True)` and `chenv.mgr.channels["email"]`.
   ```bash
   .venv/bin/python -m pytest -q tests/test_channel_commands.py tests/test_<name>.py
   ```
5. Document it: the "Commands" table in `README.md` and `README.tr.md`, `docs/DEVELOPER.md` and `docs/tr/DEVELOPER.md`. The Telegram command menu (`setMyCommands`) is published from the registry when the bot starts; a new command shows up in the menu after a restart.

If you need something specific to Telegram (a button, the `message` field), use the kit's `@command` (`from stack_kit.telegram import command`); that command also enters the registry, and on the other channels `ctx.message`/`ctx.user_id` come empty, so write it accordingly.
