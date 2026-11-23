"""Admin UI (/ui): auth, CSRF, write-only secrets, live settings, locks, memory/tasks, Telegram/LLM tests."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from stack_kit import BridgeConfig, Bridges, Stack, create_app
from stack_kit.telegram import TelegramBot
from stack_ui import SecretStore, UIConfig, settings_store

import ai_bot
from ai_bot import runtime_config as rc
from ai_bot.config import Settings
from ai_bot.panel import mount_panel
from tests.conftest import CHAT, make_settings
from tests.fakes import FakeLLM, FakeTelegram, message_update, reply, static_resolver

PASSWORD = "test-password-123"
# fake values, built from parts so secret scanners do not flag the test file
BOT_TOKEN = "123456789" + ":" + "AAH-test-token-not-real-0123456789ab"
NEW_TOKEN = "987654321" + ":" + "BBH-other-token-not-real-abcdefghij"
LLM_KEY = "sk" + "-ui-secret-value-0123456789"


class FakeGetMe:
    """Telegram getMe: valid only for the tokens given."""

    def __init__(self, valid: set[str]):
        self.valid = valid
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        token = request.url.path.split("/bot", 1)[1].split("/", 1)[0]
        if token not in self.valid:
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        return httpx.Response(200, json={"ok": True, "result": {"id": 42, "is_bot": True, "first_name": "Test",
                                                               "username": "test_ai_bot"}})


class SlowPollTelegram(FakeTelegram):
    """FakeTelegram whose getUpdates waits a little (a real long poll never returns instantly)."""

    async def ahandler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            self.calls.append(("getUpdates", {"token": request.url.path.split("/bot", 1)[1].split("/", 1)[0]}))
            await asyncio.sleep(0.05)
            return httpx.Response(200, json={"ok": True, "result": []})
        return self.handler(request)


class Env:
    def __init__(self, tmp_path, *, auth="password", owns_bot=False, settings_kw=None, llm_script=None,
                 from_env=False, gates=None, llm_transport=None, ui_config=None, dns=None):
        self.llm = FakeLLM(llm_script, reply("ok"))
        self.tmp = tmp_path
        self.tg = FakeTelegram()
        self.getme = FakeGetMe({BOT_TOKEN, NEW_TOKEN})
        self.stack = Stack("ai-bot", "test")
        self.store = settings_store(rc.SCHEMA, tmp_path / "ui-settings.db")
        self.secrets = SecretStore(tmp_path / "ui-secrets.json")
        def base():
            if from_env:
                s = Settings.from_env()
                s.data_dir = tmp_path
                return s
            return make_settings(tmp_path, **(settings_kw or {}))

        settings = rc.load_settings(self.store, self.secrets, base=base())
        bot = None
        if not owns_bot:
            bot = TelegramBot(BOT_TOKEN, allowed_chat_ids=settings.allowed_chat_ids, stack=self.stack,
                              transport=self.tg.transport, confirm_timeout=2.0, set_commands=False)
        self.ai = ai_bot.setup(self.stack, bot, settings=settings, bridges=Bridges([], stack=self.stack),
                               llm_transport=llm_transport or self.llm.transport, resolver=static_resolver(dns or {}))
        self.ai.llm.backoff = 0.0
        self.app = create_app(self.stack, config=BridgeConfig(token="bridge-token-0123456789abcdef"),
                              load_plugins=False, gates=gates)
        self.live = rc.LiveConfig(self.ai, self.store, self.secrets, rc.SetupState(tmp_path / "ui-setup.json"),
                                    base=base)
        cfg = ui_config or (UIConfig(auth="password", password=PASSWORD) if auth == "password" else UIConfig(auth=auth))
        cfg.auth_path = cfg.auth_path or str(tmp_path / "ui-auth.json")
        mount_panel(self.app, self.ai, self.live, config=cfg, http_transport=httpx.MockTransport(self.getme.handler))

    def client(self, login=True) -> TestClient:
        c = TestClient(self.app)
        if login:
            c.__enter__()
            r = c.post("/ui/login", json={"password": PASSWORD})
            assert r.status_code == 200, r.text
            c.headers["X-CSRF-Token"] = c.cookies.get("skui_csrf")
        return c


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e


@pytest.fixture
def client(env):
    c = env.client()
    yield c
    c.__exit__(None, None, None)


# ------------------------------------------------------------------ access


def test_api_requires_login(env):
    with env.client(login=False) as c:
        assert c.get("/ui/api/overview").status_code == 401
        assert c.put("/ui/api/config/LLM_MODEL", json={"value": "x"}).status_code in (401, 403)
        r = c.get("/ui/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/ui/login"


def test_komuta_mode_without_identity_shows_guide(tmp_path):
    e = Env(tmp_path, auth="komuta")
    with e.client(login=False) as c:
        r = c.get("/ui/api/overview")
        assert r.status_code == 403 and r.json()["error"]["code"] in ("ui_unauthorized", "ui_disabled")
        page = c.get("/ui/")
        assert page.status_code == 403
        assert "<details" in page.text and "docs/ARAYUZ.md" in page.text  # kit page + our setup_hint
        assert "Content-Security-Policy" in page.headers
        # forged convenience headers are ignored
        r = c.get("/ui/api/overview", headers={"x-komuta-user-id": "1", "x-komuta-user-email": "a@b.example"})
        assert r.status_code == 403


def test_pages_served_with_security_headers(client):
    r = client.get("/ui/")
    assert r.status_code == 200 and "app.js" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/ui/pages/setup.js").status_code == 200
    assert client.get("/ui/pages/chat.js").status_code == 200
    assert client.get("/ui/icons.svg").status_code == 200
    assert client.get("/health").status_code == 200  # machine path untouched


def test_writes_need_csrf(env, client):
    token = client.headers.pop("X-CSRF-Token")
    r = client.put("/ui/api/config/LLM_MODEL", json={"value": "evil/model"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.post("/ui/api/config/batch", json={"values": {"LLM_MODEL": "evil/model"}},
                    headers={"X-CSRF-Token": token, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert env.ai.settings.llm_model == "test/model"


# ------------------------------------------------------------------ secrets


def test_secret_is_write_only(env, client, caplog):
    caplog.set_level(logging.INFO)
    r = client.put("/ui/api/secrets/LLM_API_KEY", json={"value": LLM_KEY})
    assert r.status_code == 200
    assert r.json()["secret"] == {"name": "LLM_API_KEY", "set": True, "preview": "…6789", "source": "ui"}
    bodies = [r.text]
    for path in ("/ui/api/secrets", "/ui/api/config", "/ui/api/setup", "/ui/api/overview", "/ui/api/logs",
                 "/ui/api/config/audit"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        bodies.append(resp.text)
    assert not any(LLM_KEY in b for b in bodies)
    assert LLM_KEY not in caplog.text
    assert LLM_KEY not in client.get("/ui/").text
    # applied live: the LLM client now sends the new key
    assert env.ai.settings.llm_api_key == LLM_KEY
    asyncio.run(env.ai.agent.run(CHAT, "merhaba"))
    assert env.llm.headers[-1]["authorization"] == f"Bearer {LLM_KEY}"
    # stored with mode 600
    assert oct((env.secrets.path).stat().st_mode & 0o777) == "0o600"


def test_unknown_secret_refused(client):
    assert client.put("/ui/api/secrets/BRIDGE_TOKEN", json={"value": "x" * 20}).status_code == 404
    assert client.put("/ui/api/secrets/TELEGRAM_BOT_TOKEN", json={"value": "not-a-token"}).status_code == 422


# ------------------------------------------------------------------ settings


def test_settings_apply_live_and_precedence(env, client, monkeypatch):
    r = client.put("/ui/api/config/LLM_MODEL", json={"value": "ui/model"})
    assert r.status_code == 200 and r.json()["setting"]["source"] == "ui"
    assert "llm_model" in r.json()["applied"]["changed"]
    asyncio.run(env.ai.agent.run(CHAT, "selam"))
    assert env.llm.requests[-1]["model"] == "ui/model"

    r = client.post("/ui/api/config/batch", json={"values": {"MAX_TOOL_STEPS": 3, "TZ": "Europe/Berlin",
                                                             "CONFIRM_TIMEOUT": 60}})
    assert r.status_code == 200
    assert env.ai.settings.max_tool_steps == 3 and env.ai.settings.tz == "Europe/Berlin"
    assert env.ai.bot.confirm_timeout == 60

    # UI value > env > default
    monkeypatch.setenv("LLM_MODEL", "env/model")
    assert rc.load_settings(env.store, env.secrets).llm_model == "ui/model"
    assert client.delete("/ui/api/config/LLM_MODEL").json()["setting"]["source"] == "env"
    assert env.ai.settings.llm_model == "test/model"  # back to this harness's environment layer
    audit = client.get("/ui/api/config/audit").json()["audit"]
    assert audit[0]["name"] == "LLM_MODEL" and audit[0]["actor"] == "admin"


def test_invalid_values_rejected(env, client):
    assert client.put("/ui/api/config/TZ", json={"value": "Mars/Base"}).status_code == 422
    assert client.put("/ui/api/config/MAX_TOOL_STEPS", json={"value": 0}).status_code == 422
    assert client.put("/ui/api/config/LLM_BASE_URL", json={"value": "file:///etc/passwd"}).status_code == 422
    assert client.put("/ui/api/config/ALLOWED_CHAT_IDS", json={"value": "abc"}).status_code == 422
    # batch is all-or-nothing
    r = client.post("/ui/api/config/batch", json={"values": {"LLM_MODEL": "ok/model", "TZ": "Nope/Zone"}})
    assert r.status_code == 422 and env.ai.settings.llm_model == "test/model"


@pytest.mark.parametrize("name", ["WEB_READ_PORTS", "WEB_READ_MAX_REDIRECTS", "WEB_READ_MAX_BYTES", "HUMAN_SCOPES",
                                  "BRIDGE_REQUIRE_CONFIRM_ROUNDTRIP", "UI_AUTH", "BRIDGE_TOKEN"])
def test_security_locks_cannot_be_changed(env, client, name):
    assert client.put(f"/ui/api/config/{name}", json={"value": "1"}).status_code == 404
    assert client.post("/ui/api/config/batch", json={"values": {name: "1"}}).status_code == 404
    assert client.delete(f"/ui/api/config/{name}").status_code == 404
    assert env.ai.settings.web_read_ports == [80, 443, 8080, 8443]


def test_lower_limits_hold(env, client):
    # the schedule spam guard cannot go below the default
    assert client.put("/ui/api/config/SCHEDULE_MIN_INTERVAL_MINUTES", json={"value": 1}).status_code == 422
    locks = client.get("/ui/api/config").json()["locks"]
    assert {lock["id"] for lock in locks} >= {"confirm", "ssrf", "allowlist", "injection"}


def test_empty_allowlist_still_disables_chat_tools(env, client):
    r = client.delete(f"/ui/api/telegram/chats/{CHAT}")
    assert r.status_code == 200 and r.json()["allowed"] == []
    assert env.ai.bot.allowed_chat_ids == set()
    res = asyncio.run(env.stack.call("memory.list", {}))
    assert res["ok"] is False and res["error"]["code"] == "forbidden"


# ------------------------------------------------------------------ telegram


def test_telegram_test_uses_getme_and_never_leaks_token(env, client, caplog):
    caplog.set_level(logging.DEBUG)
    r = client.post("/ui/api/telegram/test", json={"token": BOT_TOKEN})
    assert r.status_code == 200 and r.json()["bot"]["username"] == "test_ai_bot"
    bad = "111111111" + ":" + "ZZZ-rejected-token-not-real-0000000"
    r = client.post("/ui/api/telegram/test", json={"token": bad})
    assert r.status_code == 422 and r.json()["error"]["code"] == "telegram_rejected"
    assert bad not in r.text and BOT_TOKEN not in r.text
    assert client.post("/ui/api/telegram/test", json={"token": "nope"}).status_code == 422
    assert BOT_TOKEN not in caplog.text and bad not in caplog.text


def test_chat_capture_and_allow(env, client):
    asyncio.run(env.ai.bot.process_update(message_update(555777, "/id")))
    data = client.get("/ui/api/telegram/chats").json()
    seen = {c["chat_id"]: c for c in data["seen"]}
    assert 555777 in seen and seen[555777]["allowed"] is False
    assert "text" not in seen[555777]  # message text is never kept
    r = client.post("/ui/api/telegram/chats", json={"chat_id": "555777"})
    assert r.status_code == 200 and 555777 in r.json()["allowed"]
    assert 555777 in env.ai.bot.allowed_chat_ids  # live
    assert env.store.resolve("ALLOWED_CHAT_IDS") == {"name": "ALLOWED_CHAT_IDS", "value": f"{CHAT},555777", "source": "ui"}
    assert client.post("/ui/api/telegram/chats", json={"chat_id": "12a"}).status_code == 422


def test_token_change_restarts_polling_live(tmp_path):
    e = Env(tmp_path, owns_bot=True)
    fake = SlowPollTelegram()
    e.ai.bot._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.ahandler))
    e.ai.bot.set_commands = False
    c = e.client()
    try:
        assert e.ai.telegram is not None and not e.ai.telegram.running  # no token yet
        r = c.put("/ui/api/secrets/TELEGRAM_BOT_TOKEN", json={"value": NEW_TOKEN})
        assert r.status_code == 200 and r.json()["applied"]["restart_required"] is False
        assert NEW_TOKEN not in r.text
        for _ in range(100):
            if any(m == "getUpdates" and b["token"] == NEW_TOKEN for m, b in fake.calls):
                break
            c.get("/health")
            time.sleep(0.02)
        assert any(m == "getUpdates" and b["token"] == NEW_TOKEN for m, b in fake.calls)
        assert e.ai.telegram.running
        assert c.get("/ui/api/overview").json()["telegram"]["running"] is True
    finally:
        c.__exit__(None, None, None)


def test_telegram_switch_stops_and_restarts_polling(tmp_path):
    e = Env(tmp_path, owns_bot=True)
    fake = SlowPollTelegram()
    e.ai.bot._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.ahandler))
    e.ai.bot.set_commands = False
    c = e.client()
    try:
        c.put("/ui/api/secrets/TELEGRAM_BOT_TOKEN", json={"value": NEW_TOKEN})
        for _ in range(100):
            if e.ai.telegram.running:
                break
            c.get("/health")
            time.sleep(0.02)
        assert e.ai.telegram.running
        assert c.get("/ui/api/telegram").json()["enabled"] is True
        r = c.post("/ui/api/config/batch", json={"values": {"TELEGRAM_ENABLED": False}})
        assert r.status_code == 200, r.text
        assert not e.ai.telegram.running and not e.ai.has_telegram
        tg = c.get("/ui/api/telegram").json()
        assert tg["enabled"] is False and tg["running"] is False and tg["configured"] is True
        assert c.post("/ui/api/telegram/restart").status_code == 409  # stays off until switched on
        assert asyncio.run(e.ai.deliver(CHAT, "hello")) is False  # reminders do not go out while the channel is off
        r = c.post("/ui/api/config/batch", json={"values": {"TELEGRAM_ENABLED": True}})
        assert r.status_code == 200
        for _ in range(100):
            if e.ai.telegram.running:
                break
            c.get("/health")
            time.sleep(0.02)
        assert e.ai.telegram.running and e.ai.has_telegram
    finally:
        c.__exit__(None, None, None)


def test_telegram_runner_never_runs_two_pollers():
    from ai_bot.core import TelegramRunner

    class Bot:
        token = "t"
        running = False
        active = 0
        peak = 0

        async def run(self):
            self.running = True
            Bot.active += 1
            Bot.peak = max(Bot.peak, Bot.active)
            try:
                await asyncio.sleep(30)
            finally:
                Bot.active -= 1

    async def go():
        runner = TelegramRunner(Bot())
        await runner.start()
        await asyncio.sleep(0)
        await asyncio.gather(runner.restart("a1"), runner.restart("b2"), runner.restart("c3"))
        await asyncio.sleep(0.05)
        peak = Bot.peak
        await runner.stop()
        return peak, Bot.active

    assert asyncio.run(go()) == (1, 0)


# ------------------------------------------------------------------ llm / search


def test_llm_test_endpoint(env, client):
    # a local server: needs the "private network" opt-in for the custom provider (network policy)
    assert client.post("/ui/api/llm/test", json={"provider": "custom", "base_url": "http://127.0.0.1:9/v1",
                                                "model": "m/one", "api_key": LLM_KEY}).status_code == 422
    r = client.post("/ui/api/llm/test", json={"provider": "custom", "base_url": "http://127.0.0.1:9/v1", "model": "m/one",
                                              "api_key": LLM_KEY, "allow_private": True})
    assert r.status_code == 200 and r.json()["reply"] == "ok" and r.json()["saved"] is False
    assert env.llm.requests[-1]["model"] == "m/one"
    assert env.llm.headers[-1]["authorization"] == f"Bearer {LLM_KEY}"
    assert LLM_KEY not in r.text
    env.llm.script = [401]
    r = client.post("/ui/api/llm/test", json={"api_key": LLM_KEY})
    assert r.status_code == 424 and r.json()["error"]["code"] == "llm_failed" and LLM_KEY not in r.text
    assert client.post("/ui/api/llm/test", json={"base_url": "ftp://x"}).status_code == 422


def test_search_setup_requires_key(env, client):
    r = client.post("/ui/api/setup/search", json={"provider": "tavily"})
    assert r.status_code == 422
    assert env.ai.settings.search_provider == "auto"  # nothing saved on a refused request
    assert client.post("/ui/api/setup/search", json={"provider": "searxng", "searxng_url": ""}).status_code == 422
    r = client.post("/ui/api/setup/search", json={"provider": "tavily", "api_key": "tvly-0123456789abcdef"})
    assert r.status_code == 200 and r.json()["search"]["ready"] is True
    assert env.ai.settings.search_provider == "tavily" and env.ai.settings.tavily_api_key == "tvly-0123456789abcdef"
    assert "tvly-0123456789abcdef" not in r.text
    r = client.post("/ui/api/setup/search", json={"provider": "duckduckgo"})
    assert r.status_code == 200 and "güvenilmez" in r.json()["search"]["warning"]


# ------------------------------------------------------------------ wizard


def test_setup_wizard_flow(env, client):
    s = client.get("/ui/api/setup").json()
    assert [x["id"] for x in s["steps"]] == ["panel", "channel", "telegram", "chats", "llm", "search", "summary"]
    assert s["steps"][0]["state"] == "done"  # UI_PASSWORD given
    assert client.post("/ui/api/setup/complete").status_code == 409
    client.put("/ui/api/secrets/TELEGRAM_BOT_TOKEN", json={"value": BOT_TOKEN})
    assert client.post("/ui/api/telegram/test", json={}).json()["saved"] is True
    client.post("/ui/api/config/batch", json={"values": {"LLM_MODEL": "w/model"}, "secrets": {"LLM_API_KEY": LLM_KEY}})
    assert client.post("/ui/api/llm/test", json={}).status_code == 200
    assert client.post("/ui/api/setup/search", json={"provider": "duckduckgo"}).status_code == 200
    s = client.get("/ui/api/setup").json()
    chats = {x["id"]: x for x in s["steps"]}["chats"]
    assert chats["state"] == "ready" and not s["all_ready"]  # a default list is never trusted until confirmed
    assert client.post("/ui/api/setup/confirm", json={"step": "chats", "fp": chats["fp"]}).status_code == 200
    s = client.get("/ui/api/setup").json()
    assert s["done"] == 6 and s["all_ready"], s["steps"]
    r = client.post("/ui/api/setup/complete")
    assert r.status_code == 200 and r.json()["completed"] is True
    # a changed key invalidates the LLM step until it is tested again
    client.put("/ui/api/secrets/LLM_API_KEY", json={"value": LLM_KEY + "x"})
    s = client.get("/ui/api/setup").json()
    assert {x["id"]: x for x in s["steps"]}["llm"]["done"] is False and s["completed"] is False
    state = json.loads((env.live.setup.path).read_text())
    assert LLM_KEY not in json.dumps(state) and BOT_TOKEN not in json.dumps(state)


def test_channel_picked_after_completion_does_not_reopen_the_setup(env, client):
    client.put("/ui/api/secrets/TELEGRAM_BOT_TOKEN", json={"value": BOT_TOKEN})
    assert client.post("/ui/api/telegram/test", json={}).json()["saved"] is True
    client.post("/ui/api/config/batch", json={"values": {"LLM_MODEL": "w/model"}, "secrets": {"LLM_API_KEY": LLM_KEY}})
    assert client.post("/ui/api/llm/test", json={}).status_code == 200
    assert client.post("/ui/api/setup/search", json={"provider": "duckduckgo"}).status_code == 200
    chats = {x["id"]: x for x in client.get("/ui/api/setup").json()["steps"]}["chats"]
    assert client.post("/ui/api/setup/confirm", json={"step": "chats", "fp": chats["fp"]}).status_code == 200
    assert client.post("/ui/api/setup/complete").json()["completed"] is True
    st = client.post("/ui/api/setup/channel", json={"channel": "slack"}).json()
    by = {x["id"]: x for x in st["steps"]}
    assert by["channel_setup"]["late"] is True and by["channel_setup"]["state"] == "todo"
    assert st["completed"] is True and st["all_ready"] is True  # the new channel is set up at its own pace
    assert "telegram" in by and "chats" in by  # Telegram is still in use next to the new pick


# ------------------------------------------------------------------ memory / tasks / tools / logs


def test_memory_endpoints(env, client):
    a = env.ai.memory.remember(CHAT, "Kullanıcının adı Deniz.")["id"]
    env.ai.memory.remember(CHAT, "Kahveyi sütsüz içer.")
    env.ai.memory.remember(999, "Başka sohbetin anısı.")
    chats = {c["chat_id"]: c for c in client.get("/ui/api/memory/chats").json()["chats"]}
    assert chats[CHAT]["count"] == 2 and chats[999]["count"] == 1
    r = client.get(f"/ui/api/memory?chat_id={CHAT}").json()
    assert r["total"] == 2 and all("Başka" not in m["content"] for m in r["items"])
    r = client.get(f"/ui/api/memory?chat_id={CHAT}&q=Deniz").json()
    assert [m["id"] for m in r["items"]] == [a]
    assert client.delete(f"/ui/api/memory/999/{a}").status_code == 404  # other chat's id
    assert client.delete(f"/ui/api/memory/{CHAT}/{a}").status_code == 200
    assert client.delete(f"/ui/api/memory/{CHAT}").json()["deleted"] == 1
    assert env.ai.db.count_memories(999) == 1


def test_task_endpoints(env, client):
    t = env.ai.scheduler.add(CHAT, "0 9 * * *", "Günaydın", "message", "sabah")
    data = client.get("/ui/api/tasks").json()
    assert data["tasks"][0]["id"] == t["id"] and data["tasks"][0]["cron"] == "0 9 * * *"
    assert data["tasks"][0]["next_run"] > 0
    assert client.delete(f"/ui/api/tasks/{t['id']}").status_code == 200
    assert client.get("/ui/api/tasks").json()["tasks"] == []
    assert client.get("/ui/api/tasks?include_inactive=true").json()["tasks"][0]["active"] is False
    assert client.delete(f"/ui/api/tasks/{t['id']}").status_code == 404


def test_tools_marks_confirmation(env, client):
    @env.stack.tool("demo.pay", "Pay", confirm=True, scope="trade")
    def pay(amount: float) -> dict:
        return {}

    tools = {t["name"]: t for t in client.get("/ui/api/tools").json()["local"]}
    assert tools["demo.pay"]["needs_confirmation"] is True and tools["demo.pay"]["source"] == "plugin"
    assert tools["web.read"]["needs_confirmation"] is False and tools["web.read"]["source"] == "builtin"
    assert tools["ai.ask"]["hidden_from_ai"] is True
    assert client.post("/ui/api/bridges/test").json() == {"ok": True, "bridges": []}


def test_logs_are_redacted(env, client):
    client.put("/ui/api/secrets/LLM_API_KEY", json={"value": LLM_KEY})
    logging.getLogger("ai_bot.test").warning("leak attempt %s", LLM_KEY)
    r = client.get("/ui/api/logs?level=warning")
    assert r.status_code == 200 and LLM_KEY not in r.text
    assert any("leak attempt ***" in x["message"] for x in r.json()["logs"])
    ov = client.get("/ui/api/overview").json()
    assert any("leak attempt" in x["message"] for x in ov["errors"])


def test_build_app_boots_with_ui_layer(tmp_path, monkeypatch):
    """python -m ai_bot path: a value saved in /ui wins over the env var at start-up."""
    from ai_bot.main import build_app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODEL", "env/model")
    monkeypatch.setenv("UI_AUTH", "password")
    monkeypatch.setenv("UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("PLUGINS_DIR", str(tmp_path / "no-plugins"))
    settings_store(rc.SCHEMA, tmp_path / "ui-settings.db").set("LLM_MODEL", "ui/model", actor="test")
    SecretStore(tmp_path / "ui-secrets.json").set("LLM_API_KEY", LLM_KEY)
    app = build_app()
    ai = app.state.ai_panel.ai
    assert ai.settings.llm_model == "ui/model" and ai.settings.llm_api_key == LLM_KEY
    with TestClient(app) as c:
        assert c.post("/ui/login", json={"password": PASSWORD}).status_code == 200
        body = c.get("/ui/api/config").json()
        model = next(s for s in body["settings"] if s["name"] == "LLM_MODEL")
        assert model["value"] == "ui/model" and model["source"] == "ui"
        assert LLM_KEY not in json.dumps(body)
