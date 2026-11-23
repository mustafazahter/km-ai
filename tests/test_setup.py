"""ai_bot.setup(stack, bot) contract used by the unified service (mt5-trader)."""

import asyncio

from stack_kit import Bridges, Stack
from stack_kit.telegram import TelegramBot

import ai_bot
from tests.conftest import CHAT, make_settings


def test_setup_with_given_bot_does_not_start_it(tmp_path):
    stack = Stack("mt5-trader", "test")

    @stack.command("help", "mt5 help")
    async def mt5_help(ctx):
        return "mt5"

    bot = TelegramBot("123456:TEST-token-not-real", allowed_chat_ids=[CHAT], stack=stack, set_commands=False)
    app = ai_bot.setup(stack, bot, settings=make_settings(tmp_path), bridges=Bridges([], stack=stack))
    assert isinstance(app, ai_bot.AiBot)
    assert {"hafiza", "gorevler", "model", "sifirla"} <= set(stack.commands)
    assert stack.commands["help"].func is mt5_help  # existing command kept
    assert bot._message_handler is not None
    assert {"web.search", "memory.remember", "schedule.add"} <= set(stack.tools)
    bg = [f.__qualname__ for f in stack._background]
    assert not any("TelegramBot.run" in q for q in bg)  # caller owns the bot lifecycle
    assert any("scheduler" in q for q in bg)
    assert stack.app_ai_bot is app


def test_setup_without_bot_creates_one(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST-token-not-real")
    stack = Stack("ai-bot", "test")
    app = ai_bot.setup(stack, settings=make_settings(tmp_path), bridges=Bridges([], stack=stack))
    assert app.bot is not None and app.bot.allowed_chat_ids == {CHAT}
    # polling is owned by the TelegramRunner (started on startup, restartable from /ui)
    assert app.telegram is not None and app.telegram.bot is app.bot
    assert any("telegram_start" in f.__qualname__ for f in stack._startup)


def test_unified_service_ai_sees_local_trade_tools(tmp_path):
    from tests.fakes import FakeLLM, reply
    stack = Stack("mt5-trader", "test")
    done = []

    @stack.tool("mt5.buy", "Buy", confirm=True, scope="trade")
    def buy(symbol: str) -> dict:
        done.append(symbol)
        return {"ok": 1}

    llm = FakeLLM([reply("hazır")])
    app = ai_bot.setup(stack, None, settings=make_settings(tmp_path), bridges=Bridges([], stack=stack),
                       llm_transport=llm.transport)
    asyncio.run(app.agent.run(CHAT, "x", confirmer=None))
    names = {t["function"]["name"] for t in llm.requests[0]["tools"]}
    assert "mt5_buy" in names and done == []
