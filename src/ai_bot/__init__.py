"""komuta ai-bot: Telegram AI assistant on top of stack_kit.

Public API (other stacks rely on it, keep it stable)::

    import ai_bot
    app = ai_bot.setup(stack, bot=None)

``setup`` registers every built-in tool (web.*, schedule.*, memory.*, ai.ask), the Telegram
commands (/help /hafiza /gorevler /model /sifirla; /start and /id come from the kit) and the
free-text handler onto the given ``Stack`` / ``TelegramBot``, plus the scheduler background task
and clean shutdown. It returns the :class:`~ai_bot.core.AiBot` container.

* ``bot=None``: a ``TelegramBot`` is created from ``TELEGRAM_BOT_TOKEN`` / ``ALLOWED_CHAT_IDS``
  and started as a background task of ``stack`` (if the token is set).
* ``bot`` given (unified service, e.g. mt5-trader): handlers are registered on it, but the caller
  owns its lifecycle (``stack.background(bot.run)`` / ``stack.on_shutdown(bot.aclose)``), so the
  bot is never polled twice.
"""

from __future__ import annotations

import logging
from typing import Any

from stack_kit import Bridges, Stack
from stack_kit.telegram import TelegramBot

from . import builtin_tools, telegram_ui
from .config import Settings
from .channels.manager import ChannelManager
from .core import AiBot, TelegramRunner

__version__ = "1.0.0"
__all__ = ["setup", "AiBot", "Settings", "EVENTS", "__version__"]

# Events emitted on the kit event log (GET /bridge/events) and to WEBHOOKS_OUT (n8n...).
EVENTS = ["ai.reply", "schedule.fired", "memory.remembered", "memory.forgotten"]

log = logging.getLogger("ai_bot")


def setup(stack: Stack, bot: TelegramBot | None = None, *, settings: Settings | None = None,
          bridges: Bridges | None = None, **overrides: Any) -> AiBot:
    """Register ai-bot onto ``stack`` (and ``bot``). See the module docstring."""
    settings = settings or Settings.from_env()
    owns_bot = bot is None
    if bot is None:
        bot = TelegramBot(settings.telegram_bot_token, stack=stack, allowed_chat_ids=settings.allowed_chat_ids,
                          confirm_timeout=settings.confirm_timeout, api_base=settings.telegram_api_base)
    if not settings.allowed_chat_ids and bot.allowed_chat_ids:
        settings.allowed_chat_ids = sorted(bot.allowed_chat_ids)
    if bridges is None:
        bridges = Bridges.from_env(stack=stack)
    channel_transport = overrides.pop("channel_transport", None)  # tests: httpx.MockTransport for the channels
    app = AiBot(stack, settings, bot=bot, bridges=bridges, **overrides)
    app.channels = ChannelManager(app, transport=channel_transport)  # WhatsApp/Teams/Slack/e-mail (off by default)
    for event in EVENTS:
        stack.declare_event(event)
    app.memory.on_change = stack.emit
    builtin_tools.register(stack, app)
    telegram_ui.register(bot, app)

    # chats that write to the bot are remembered (id/name only) so the admin UI can offer them
    _process = bot.process_update

    async def _process_and_note(update: dict) -> None:
        app.note_update(update)
        await _process(update)

    bot.process_update = _process_and_note  # type: ignore[method-assign]

    @stack.background
    async def _ai_bot_scheduler() -> None:
        await app.scheduler.run_forever()

    if owns_bot:
        # the runner owns polling so a token saved in the admin UI applies without a restart
        app.telegram = TelegramRunner(bot)

        @stack.on_startup
        async def _ai_bot_telegram_start() -> None:
            if settings.telegram_enabled:
                await app.telegram.start()

        @stack.on_shutdown
        async def _ai_bot_telegram_stop() -> None:
            await app.telegram.stop()
            await bot.aclose()

        if not settings.telegram_enabled:
            log.info("Telegram is switched off (TELEGRAM_ENABLED=false)")
        elif not bot.token:
            log.warning("TELEGRAM_BOT_TOKEN is not set: Telegram is off (bridge/MCP still work; set it in /ui)")

    @stack.on_startup
    async def _ai_bot_channels_start() -> None:
        await app.channels.start()  # e-mail poller idles until the channel is enabled and configured

    @stack.on_shutdown
    async def _ai_bot_close() -> None:
        await app.aclose()

    if not settings.llm_api_key:
        log.warning("LLM_API_KEY is not set; fine for local servers, OpenRouter/OpenAI will refuse requests")
    stack.app_ai_bot = app  # type: ignore[attr-defined]  # handy for plugins: get_stack().app_ai_bot
    return app
