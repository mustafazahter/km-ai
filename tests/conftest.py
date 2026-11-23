from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from stack_kit import Bridges, Stack
from stack_kit.telegram import TelegramBot

import ai_bot
from ai_bot.config import Settings
from ai_bot.core import AiBot
from tests.fakes import FakeLLM, FakeTelegram, static_resolver

CHAT = 111111
OTHER_CHAT = 222222


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("ALLOWED_CHAT_IDS", "TELEGRAM_BOT_TOKEN", "BRIDGES", "LLM_API_KEY", "BRIDGE_TOKEN",
                "BRIDGE_MT5_URL", "BRIDGE_MT5_TOKEN", "PLUGINS_DIR"):
        monkeypatch.delenv(key, raising=False)
    logging.getLogger("ai_bot").setLevel(logging.INFO)


def make_settings(tmp_path: Path, **kw) -> Settings:
    base = dict(data_dir=tmp_path, allowed_chat_ids=[CHAT], llm_api_key="test-key-123456", llm_retries=1,
                tz="Europe/Istanbul", llm_model="test/model", schedule_min_interval_minutes=5)
    base.update(kw)
    return Settings(**base)


@dataclass
class Harness:
    app: AiBot
    stack: Stack
    bot: TelegramBot
    llm: FakeLLM
    tg: FakeTelegram


@pytest.fixture
def make_harness(tmp_path):
    created: list[Harness] = []

    def factory(script=None, *, default=None, settings: Settings | None = None, bridges: Bridges | None = None,
                web_transport=None, resolver=None, stack: Stack | None = None, data_dir: Path | None = None,
                **settings_kw) -> Harness:
        llm = FakeLLM(script, default)
        tg = FakeTelegram()
        stack = stack or Stack("ai-bot", "test")
        s = settings or make_settings(data_dir or tmp_path, **settings_kw)
        bot = TelegramBot("123456:TEST-token-not-real", allowed_chat_ids=s.allowed_chat_ids, stack=stack,
                          transport=tg.transport, confirm_timeout=2.0, set_commands=False)
        app = ai_bot.setup(stack, bot, settings=s, bridges=bridges or Bridges([], stack=stack),
                           llm_transport=llm.transport, web_transport=web_transport,
                           resolver=resolver or static_resolver({}))
        app.llm.backoff = 0.0
        h = Harness(app, stack, bot, llm, tg)
        created.append(h)
        return h

    return factory
