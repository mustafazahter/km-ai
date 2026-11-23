"""Shared helpers for tests/test_channels_*.py: a bot with the channel manager, a fake provider network
(httpx.MockTransport), stores that behave like the panel's. No real network is ever used."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from stack_kit import BridgeConfig, Bridges, Stack, create_app
from stack_kit.telegram import TelegramBot
from stack_ui import SecretStore, UIConfig, settings_store

import ai_bot
from ai_bot import runtime_config as rc
from ai_bot.channels.base import chat_id_for
from ai_bot.panel import mount_panel
from tests.conftest import make_settings
from tests.fakes import FakeLLM, FakeTelegram, reply, static_resolver

PASSWORD = "test-password-123"
WA_TOKEN = "EAAG" + "wa-access-token-not-real-0123456789"
WA_APP_SECRET = "wa-app-secret-not-real-0123456789"
WA_VERIFY = "wa-verify-token-not-real-0123"
SLACK_TOKEN = "xoxb" + "-slack-bot-token-not-real-0123456789"
SLACK_SIGNING = "slack-signing-secret-not-real-0123"
TEAMS_PASSWORD = "teams-client-secret-not-real-0123"
EMAIL_PASSWORD = "mail-app-password-not-real-0123"
PHONE_ID = "109876543210987"
ALICE = "905551112233"
BOB = "905559998877"


class Provider:
    """Fake provider network. ``routes``: host -> callable(request) -> httpx.Response."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        fn = self.routes.get(request.url.host)
        if fn is None:
            return httpx.Response(404, json={"error": "unrouted"})
        return fn(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def to(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]

    def bodies(self, host: str) -> list[dict]:
        out = []
        for r in self.to(host):
            try:
                out.append(json.loads(r.content))
            except ValueError:
                out.append({})
        return out


class ChEnv:
    def __init__(self, tmp_path, *, script=None, with_panel=False, stack: Stack | None = None, **settings_kw):
        self.tmp = tmp_path
        self.llm = FakeLLM(script, reply("tamam"))
        self.tg = FakeTelegram()
        self.net = Provider()
        self.stack = stack or Stack("ai-bot", "test")
        self.store = settings_store(rc.SCHEMA, tmp_path / "ui-settings.db")
        self.secrets = SecretStore(tmp_path / "ui-secrets.json")
        settings = rc.load_settings(self.store, self.secrets, base=make_settings(tmp_path, **settings_kw))
        bot = TelegramBot("123456:TEST-token-not-real", allowed_chat_ids=settings.allowed_chat_ids, stack=self.stack,
                          transport=self.tg.transport, confirm_timeout=2.0, set_commands=False)
        self.ai = ai_bot.setup(self.stack, bot, settings=settings, bridges=Bridges([], stack=self.stack),
                               llm_transport=self.llm.transport, resolver=static_resolver({}),
                               channel_transport=self.net.transport)
        self.ai.llm.backoff = 0.0
        self.mgr = self.ai.channels
        self.mgr.bind(self.store, self.secrets)
        self.mgr.config.ttl = 0  # tests change settings and expect them at once
        self.app = create_app(self.stack, config=BridgeConfig(token="bridge-token-0123456789abcdef"), load_plugins=False)
        self.mgr.mount(self.app)
        self.live = None
        if with_panel:
            self.live = rc.LiveConfig(self.ai, self.store, self.secrets, rc.SetupState(tmp_path / "ui-setup.json"),
                                      base=lambda: make_settings(tmp_path, **settings_kw))
            cfg = UIConfig(auth="password", password=PASSWORD)
            cfg.auth_path = str(tmp_path / "ui-auth.json")
            mount_panel(self.app, self.ai, self.live, config=cfg)

    # -- settings like the panel stores them
    def set(self, **values) -> None:
        for name, value in values.items():
            if name in rc.SECRET_NAMES:
                self.secrets.set(name, value)
            else:
                self.store.set(name, value, actor="test")
        self.mgr.config.invalidate()

    def whatsapp(self, allowed=(ALICE,), enabled=True) -> None:
        self.set(WHATSAPP_ENABLED=enabled, WHATSAPP_PHONE_NUMBER_ID=PHONE_ID, WHATSAPP_ALLOWED_NUMBERS=", ".join(allowed),
                 WHATSAPP_TOKEN=WA_TOKEN, WHATSAPP_APP_SECRET=WA_APP_SECRET, WHATSAPP_VERIFY_TOKEN=WA_VERIFY)

    def slack(self, allowed=("U0ALICE01",), enabled=True) -> None:
        self.set(SLACK_ENABLED=enabled, SLACK_ALLOWED_USERS=", ".join(allowed), SLACK_BOT_TOKEN=SLACK_TOKEN,
                 SLACK_SIGNING_SECRET=SLACK_SIGNING)

    def client(self) -> TestClient:
        c = TestClient(self.app)
        c.__enter__()  # one event loop for the whole test: background tasks keep running between requests
        return c

    def panel_client(self) -> TestClient:
        c = self.client()
        r = c.post("/ui/login", json={"password": PASSWORD})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = c.cookies.get("skui_csrf")
        return c

    def settle(self, c: TestClient) -> None:
        c.portal.call(self.mgr.idle)

    def llm_texts(self) -> list[str]:
        return [m["content"] for body in self.llm.requests for m in body["messages"] if m["role"] == "user"]


@pytest.fixture
def chenv(tmp_path):
    clients: list[TestClient] = []
    e = ChEnv(tmp_path)
    orig = e.client

    def tracked():
        c = orig()
        clients.append(c)
        return c

    e.client = tracked  # type: ignore[method-assign]
    yield e
    for c in clients:
        c.__exit__(None, None, None)


def wa_sign(body: bytes, secret: str = WA_APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def slack_sign(body: bytes, ts: int | None = None, secret: str = SLACK_SIGNING) -> dict:
    ts = int(time.time()) if ts is None else ts
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + str(ts).encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": sig, "Content-Type": "application/json"}


def test_helpers_load(chenv):
    assert chat_id_for("whatsapp", ALICE) < 0
