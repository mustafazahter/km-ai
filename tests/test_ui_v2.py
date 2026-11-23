"""Admin UI v2: first-time setup, env-filled wizard, protection switch, bridge gate, Telegram page,
multi-provider model lists, Anthropic adapter, short closed page."""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx
import pytest

from stack_kit import BridgeGate
from stack_ui import UIConfig

from ai_bot.llm import AnthropicClient, from_anthropic, to_anthropic
from ai_bot.providers import BY_ID, PROVIDERS, infer_provider, list_models, resolve_provider
from tests.conftest import CHAT
from tests.fakes import tool_call
from tests.test_ui import BOT_TOKEN, LLM_KEY, PASSWORD, Env, client, env  # noqa: F401

SETUP_CODE = "ABCD-EFGH-JKMN"
NEW_PASSWORD = "uzun-ve-guclu-bir-parola-42"


def login(c):
    r = c.post("/ui/login", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = c.cookies.get("skui_csrf")


# ------------------------------------------------------------------ fake multi-provider server


class ProviderServer:
    """OpenAI style /models + /chat/completions, Anthropic /v1/models + /v1/messages, Ollama /api/tags."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.fail_status: int | None = None
        self.anthropic_script: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.fail_status:
            return httpx.Response(self.fail_status, json={"error": {"message": "nope"}})
        if path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}, {"name": "qwen2.5:7b"}]})
        if path == "/v1/models" and request.headers.get("anthropic-version"):
            return httpx.Response(200, json={"data": [{"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"},
                                                      {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5"}],
                                             "has_more": False})
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "models/gemini-2.5-flash" if "googleapis" in request.url.host else "model-a",
                 "supported_parameters": ["tools", "temperature"]},
                {"id": "model-b"}]})
        if path.endswith("/v1/messages"):
            body = json.loads(request.content)
            item = self.anthropic_script.pop(0) if self.anthropic_script else {
                "type": "message", "content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 3, "output_tokens": 1}}
            item.setdefault("_echo", body)
            return httpx.Response(200, json={k: v for k, v in item.items() if k != "_echo"})
        if path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
        return httpx.Response(404, json={"error": {"message": "unknown path"}})

    @property
    def transport(self):
        return httpx.MockTransport(self.handler)


# ------------------------------------------------------------------ first-time setup


def test_first_time_setup_with_code_from_env_config(tmp_path):
    e = Env(tmp_path, ui_config=UIConfig(setup_code=SETUP_CODE))  # auth not set -> default_auth="password"
    with e.client(login=False) as c:
        r = c.get("/ui/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/ui/setup")
        page = c.get("/ui/setup")
        assert page.status_code == 200 and "setup-form" in page.text and "auth-vars.css" in page.text
        css = c.get("/ui/_kit/auth-vars.css").text
        assert "460px" in css  # compact card width from mount_ui(setup_card_width=460)
        assert c.get("/ui/api/overview").status_code == 401
        bad = c.post("/ui/setup", json={"code": "WRONG-CODE-1234", "password": NEW_PASSWORD, "password2": NEW_PASSWORD})
        assert bad.status_code == 401
        weak = c.post("/ui/setup", json={"code": SETUP_CODE, "password": "kisa", "password2": "kisa"})
        assert weak.status_code == 422
        ok = c.post("/ui/setup", json={"code": SETUP_CODE, "password": NEW_PASSWORD, "password2": NEW_PASSWORD})
        assert ok.status_code == 200, ok.text
        c.headers["X-CSRF-Token"] = c.cookies.get("skui_csrf")
        st = c.get("/ui/api/setup").json()
        assert st["steps"][0]["id"] == "panel" and st["steps"][0]["state"] == "done"
        assert st["auth"]["password"] == "stored"
        assert NEW_PASSWORD not in json.dumps(st)
        # the code is single use
        assert c.post("/ui/setup", json={"code": SETUP_CODE, "password": NEW_PASSWORD}).status_code == 409


def test_setup_code_is_logged_once_when_not_given(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    e = Env(tmp_path, ui_config=UIConfig())
    code = e.app.state.ui.setup_code
    assert code and code in caplog.text  # Komuta console > Logs
    with e.client(login=False) as c:
        assert c.post("/ui/setup", json={"code": code.lower(), "password": NEW_PASSWORD, "password2": NEW_PASSWORD}).status_code == 200
        assert code not in c.get("/ui/api/setup").text


# ------------------------------------------------------------------ env values shown filled


@pytest.fixture
def env_vars(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ALLOWED_CHAT_IDS", str(CHAT))
    monkeypatch.setenv("LLM_API_KEY", LLM_KEY)
    monkeypatch.setenv("LLM_MODEL", "env/model")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("SEARCH_PROVIDER", "duckduckgo")


def test_env_values_are_shown_but_not_trusted(tmp_path, env_vars):
    """A value coming from the environment is filled in, but the step stays open until it is verified."""
    e = Env(tmp_path, from_env=True)
    with e.client(login=False) as c:
        login(c)
        st = c.get("/ui/api/setup").json()
        by = {s["id"]: s for s in st["steps"]}
        assert by["telegram"]["state"] == "ready" and by["chats"]["state"] == "ready" and by["llm"]["state"] == "ready"
        assert by["search"]["state"] == "ready"
        assert not any(by[k]["done"] for k in ("telegram", "chats", "llm", "search"))  # nothing verified yet
        tok = by["telegram"]["fields"][0]
        assert tok["source"] == "env" and tok["preview"] == "…89ab" and tok["env_set"] is True
        model = next(f for f in by["llm"]["fields"] if f["name"] == "LLM_MODEL")
        assert model["value"] == "env/model" and model["source"] == "env"
        assert st["all_ready"] is False and st["done"] == 2  # password + the channel inferred from the env token
        assert c.post("/ui/api/setup/complete").status_code == 409
        assert BOT_TOKEN not in json.dumps(st) and LLM_KEY not in json.dumps(st)
        # chats + search: the user confirms what the environment gave
        assert c.post("/ui/api/setup/confirm", json={"step": "chats"}).status_code == 409  # no fingerprint = blind
        assert c.post("/ui/api/setup/confirm", json={"step": "chats", "fp": "yanlis"}).status_code == 409
        st = c.post("/ui/api/setup/confirm", json={"step": "chats", "fp": by["chats"]["fp"]}).json()
        assert {s["id"]: s for s in st["steps"]}["chats"]["state"] == "done"
        st = c.post("/ui/api/setup/confirm", json={"step": "search", "fp": by["search"]["fp"]}).json()
        assert {s["id"]: s for s in st["steps"]}["search"]["state"] == "done"
        assert c.post("/ui/api/setup/confirm", json={"step": "llm"}).status_code == 422  # tested, never confirmed
        # a value typed in the panel overrides env ...
        c.put("/ui/api/secrets/LLM_API_KEY", json={"value": LLM_KEY + "-panel"})
        st = c.get("/ui/api/setup").json()
        key = next(f for f in {s["id"]: s for s in st["steps"]}["llm"]["fields"] if f["name"] == "LLM_API_KEY")
        assert key["source"] == "ui" and key["env_set"] is True
        assert e.ai.settings.llm_api_key == LLM_KEY + "-panel"
        # ... and "back to env" removes the panel value
        assert c.delete("/ui/api/secrets/LLM_API_KEY").status_code == 200
        assert e.ai.settings.llm_api_key == LLM_KEY


def test_confirmed_chats_reset_when_the_list_changes(tmp_path, env_vars):
    e = Env(tmp_path, from_env=True)
    with e.client(login=False) as c:
        login(c)
        fp = {s["id"]: s for s in c.get("/ui/api/setup").json()["steps"]}["chats"]["fp"]
        assert c.post("/ui/api/setup/confirm", json={"step": "chats", "fp": fp}).status_code == 200
        c.put("/ui/api/config/ALLOWED_CHAT_IDS", json={"value": f"{CHAT},{CHAT + 1}"})
        st = c.get("/ui/api/setup").json()
        assert {s["id"]: s for s in st["steps"]}["chats"]["state"] == "done"  # typed in the panel = deliberate
        assert c.delete("/ui/api/config/ALLOWED_CHAT_IDS").status_code == 200
        assert {s["id"]: s for s in c.get("/ui/api/setup").json()["steps"]}["chats"]["state"] == "done"  # same ids as confirmed


def test_chat_id_zero_is_a_placeholder(tmp_path, monkeypatch, env_vars):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "0")
    e = Env(tmp_path, from_env=True)
    with e.client(login=False) as c:
        login(c)
        st = c.get("/ui/api/setup").json()
        chats = {s["id"]: s for s in st["steps"]}["chats"]
        assert chats["state"] == "todo" and chats["detail"] == "İzin listesi boş"
        assert c.post("/ui/api/setup/confirm", json={"step": "chats"}).status_code == 422


def test_search_confirmation_is_bound_to_the_exact_config(tmp_path, env_vars, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("SEARXNG_URL", "https://searx.example.org")
    e = Env(tmp_path, from_env=True)
    with e.client(login=False) as c:
        login(c)
        by = {s["id"]: s for s in c.get("/ui/api/setup").json()["steps"]}
        assert c.post("/ui/api/setup/confirm", json={"step": "search", "fp": by["search"]["fp"]}).status_code == 200
        assert {s["id"]: s for s in c.get("/ui/api/setup").json()["steps"]}["search"]["state"] == "done"
        c.put("/ui/api/config/SEARXNG_URL", json={"value": "https://other.example.org"})  # same provider, new server
        st = {s["id"]: s for s in c.get("/ui/api/setup").json()["steps"]}["search"]
        assert st["fp"] != by["search"]["fp"] and st["state"] == "ready"  # the confirmed server is no longer the configured one


def test_empty_env_steps_are_todo(tmp_path, monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "ALLOWED_CHAT_IDS", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    e = Env(tmp_path, from_env=True)
    with e.client(login=False) as c:
        login(c)
        st = c.get("/ui/api/setup").json()
        by = {s["id"]: s for s in st["steps"]}
        # nothing configured yet: the wizard first asks which channel to use, and shows no channel-specific step
        assert by["channel"]["state"] == "todo" and by["channel"]["choice"] is None and by["llm"]["state"] == "todo"
        assert "telegram" not in by and "chats" not in by and "channel_setup" not in by
        assert st["all_ready"] is False and st["channel"] is None
        # the wizard recommends the cheap DeepSeek model while nothing is set; the built-in default stays untouched
        assert c.get("/ui/api/setup").json()["values"]["LLM_MODEL"] == "deepseek/deepseek-v4.1-flash-1017"
        assert e.ai.settings.llm_model == "openai/gpt-4o-mini"
        # picking Telegram brings its two steps back
        st = c.post("/ui/api/setup/channel", json={"channel": "telegram"}).json()
        by = {s["id"]: s for s in st["steps"]}
        assert by["channel"]["state"] == "done" and by["telegram"]["state"] == "todo" and by["chats"]["state"] == "todo"
        assert [s["id"] for s in st["steps"]] == ["panel", "channel", "telegram", "chats", "llm", "search", "summary"]


def test_wizard_continues_with_the_chosen_channel(tmp_path, monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "ALLOWED_CHAT_IDS", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    e = Env(tmp_path, from_env=True)
    with e.client(login=False) as c:
        login(c)
        assert c.post("/ui/api/setup/channel", json={"channel": "nope"}).status_code == 422
        st = c.post("/ui/api/setup/channel", json={"channel": "slack"}).json()
        ids = [s["id"] for s in st["steps"]]
        assert ids == ["panel", "channel", "channel_setup", "llm", "search", "summary"]  # no Telegram step at all
        by = {s["id"]: s for s in st["steps"]}
        assert by["channel_setup"]["channel"] == "slack" and by["channel_setup"]["state"] == "todo"
        # panel-only is a valid choice: no channel step, the rest still completes
        st = c.post("/ui/api/setup/channel", json={"channel": "none"}).json()
        assert [s["id"] for s in st["steps"]] == ["panel", "channel", "llm", "search", "summary"]
        assert st["channel"] == "none"


# ------------------------------------------------------------------ protection switch


def test_protection_off_flow_and_back(tmp_path):
    e = Env(tmp_path)
    with e.client(login=False) as c:
        login(c)
        assert c.post("/ui/api/auth/protection", json={"enabled": False, "password": PASSWORD}).status_code == 422
        assert c.post("/ui/api/auth/protection",
                      json={"enabled": False, "password": "wrong-password", "confirm": "KORUMAYI KAPAT"}).status_code == 401
        r = c.post("/ui/api/auth/protection", json={"enabled": False, "password": PASSWORD, "confirm": "KORUMAYI KAPAT"})
        assert r.status_code == 200 and r.json()["protection"] is False
        assert c.get("/ui/api/overview").json()["protection"] is False
    with e.client(login=False) as anon:
        assert anon.get("/ui/api/overview").status_code == 200  # open to anyone with the link
        st = anon.get("/ui/api/setup").json()
        assert st["steps"][0]["state"] == "todo" and st["steps"][0]["detail"] == "Koruma KAPALI"
        assert anon.put("/ui/api/config/LLM_MODEL", json={"value": "x/y"}).status_code == 403  # CSRF still on
        anon.get("/ui/")  # sets the CSRF cookie
        anon.headers["X-CSRF-Token"] = anon.cookies.get("skui_csrf")
        assert anon.post("/ui/api/auth/password", json={"new": NEW_PASSWORD}).status_code == 403  # credentials locked
        assert anon.post("/ui/api/auth/protection", json={"enabled": True}).json()["protection"] is True
        assert anon.get("/ui/api/overview").status_code == 401


# ------------------------------------------------------------------ bridge gate from the panel


def test_bridge_mcp_hook_closed_by_default_and_opened_from_panel(tmp_path):
    gate = BridgeGate(default={"bridge": False, "mcp": False, "hook": False}, path=tmp_path / "gate.json", use_env=False)
    e = Env(tmp_path, gates=gate)
    with e.client(login=False) as c:
        login(c)
        env_token = "bridge-token-0123456789abcdef"
        auth = {"Authorization": f"Bearer {env_token}"}
        assert c.get("/bridge/manifest", headers=auth).status_code == 404
        assert c.post("/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404
        assert c.post("/hook/notify", json={"text": "x"}).status_code == 404
        g = c.get("/ui/api/bridge").json()
        assert g["surfaces"] == {"bridge": False, "mcp": False, "hook": False}
        r = c.post("/ui/api/bridge", json={"action": "set", "surface": "bridge", "enabled": True}).json()
        assert r["surfaces"]["bridge"] is True
        token = c.post("/ui/api/bridge", json={"action": "rotate_token"}).json()["token"]
        assert len(token) >= 40
        assert token not in c.get("/ui/api/bridge").text  # shown once only
        assert c.get("/bridge/manifest", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        # webhook with the secret inside the JSON body (TradingView style)
        c.post("/ui/api/bridge", json={"action": "set", "surface": "hook", "enabled": True})
        secret = c.post("/ui/api/bridge", json={"action": "rotate_hook_secret"}).json()["hook_secret"]
        assert c.post("/hook/notify", json={"secret": "wrong", "text": "BTC 100k"}).status_code == 401
        r = c.post("/hook/notify", json={"secret": secret, "text": "BTC 100k"})
        assert r.status_code == 200, r.text
        assert any(b.get("text") == "BTC 100k" and b.get("chat_id") == CHAT for b in e.tg.sent())
        # close again -> 404
        c.post("/ui/api/bridge", json={"action": "set_all", "enabled": False})
        assert c.get("/bridge/manifest", headers={"Authorization": f"Bearer {token}"}).status_code == 404
        audit = c.get("/ui/api/bridge").json()["audit"]
        assert audit and all(token not in json.dumps(a) and secret not in json.dumps(a) for a in audit)


def test_main_build_app_starts_with_gate_closed(tmp_path, monkeypatch):
    from ai_bot.main import GATE_DEFAULT
    assert GATE_DEFAULT == {"bridge": False, "mcp": False, "hook": False}


# ------------------------------------------------------------------ Telegram page


def test_telegram_status_and_command_sync(tmp_path):
    e = Env(tmp_path)
    with e.client(login=False) as c:
        login(c)
        t = c.get("/ui/api/telegram").json()
        assert t["configured"] is True and {"name": "help", "help": "Yardım / help"} in t["commands"]
        assert BOT_TOKEN not in json.dumps(t)
        r = c.post("/ui/api/telegram/commands/sync")
        assert r.status_code == 200 and r.json()["count"] >= 5
        sent = [b for m, b in e.tg.calls if m == "setMyCommands"]
        assert sent and any(x["command"] == "hafiza" for x in sent[-1]["commands"])


# ------------------------------------------------------------------ providers and model lists


def test_provider_presets_are_well_formed():
    ids = [p.id for p in PROVIDERS]
    for wanted in ("openrouter", "openai", "anthropic", "gemini", "xai", "groq", "deepseek", "mistral", "together",
                   "perplexity", "ollama", "lmstudio", "vllm", "custom"):
        assert wanted in ids
    for p in PROVIDERS:
        if not p.custom:
            assert re.match(r"^https?://[^/]+", p.base_url), p.id
    assert BY_ID["gemini"].base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert infer_provider("https://api.anthropic.com") == "anthropic"
    assert infer_provider("http://127.0.0.1:11434/v1") == "ollama"
    assert resolve_provider("auto", "https://example.org/v1").id == "custom"


@pytest.mark.parametrize("pid", [p.id for p in PROVIDERS if p.models != "none" and not p.custom])
def test_model_list_per_provider(tmp_path, pid):
    server = ProviderServer()
    e = Env(tmp_path, llm_transport=server.transport)
    p = BY_ID[pid]
    with e.client(login=False) as c:
        login(c)
        body = {"provider": pid, "base_url": p.base_url}
        if p.key:
            body["api_key"] = LLM_KEY
        r = c.post("/ui/api/llm/models", json=body)
        assert r.status_code == 200, r.text
        ids = [m["id"] for m in r.json()["models"]]
        assert ids and LLM_KEY not in r.text
        req = server.requests[-1]
        if pid == "anthropic":
            assert req.url.path == "/v1/models" and req.headers["x-api-key"] == LLM_KEY
            assert req.headers["anthropic-version"] == "2023-06-01"
            assert all(m["tools"] is True for m in r.json()["models"])
        elif pid == "ollama":
            assert req.url.path == "/api/tags" and "llama3.1:8b" in ids
        else:
            assert req.url.path.endswith("/models")
            assert req.headers.get("authorization") == (f"Bearer {LLM_KEY}" if p.key else None)
        if pid == "gemini":
            assert "gemini-2.5-flash" in ids  # "models/" prefix stripped
        if pid == "openrouter":
            tools = {m["id"]: m["tools"] for m in r.json()["models"]}
            assert tools["model-a"] is True and tools["model-b"] is None  # unknown without supported_parameters


def test_model_list_errors_are_readable(tmp_path):
    server = ProviderServer()
    e = Env(tmp_path, llm_transport=server.transport)
    with e.client(login=False) as c:
        login(c)
        r = c.post("/ui/api/llm/models", json={"provider": "perplexity", "base_url": BY_ID["perplexity"].base_url})
        assert r.status_code == 424 and "elle" in r.json()["error"]["message"]
        for status, words in ((401, "reddetti"), (429, "sınır"), (404, "bulunamadı")):
            server.fail_status = status
            r = c.post("/ui/api/llm/models", json={"provider": "openai", "base_url": BY_ID["openai"].base_url,
                                                   "api_key": LLM_KEY})
            assert r.status_code == 424 and words in r.json()["error"]["message"] and LLM_KEY not in r.text
            r = c.post("/ui/api/llm/test", json={"provider": "openai", "base_url": BY_ID["openai"].base_url,
                                                 "api_key": LLM_KEY, "model": "gpt-x"})
            assert r.status_code == 424 and words in r.json()["error"]["message"]


def test_saved_key_never_sent_to_another_host(tmp_path):
    server = ProviderServer()
    e = Env(tmp_path, llm_transport=server.transport, settings_kw={"llm_base_url": "https://openrouter.ai/api/v1"})
    with e.client(login=False) as c:
        login(c)
        c.put("/ui/api/secrets/LLM_API_KEY", json={"value": LLM_KEY})
        r = c.post("/ui/api/llm/models", json={"provider": "custom", "base_url": "https://evil.example/v1"})
        assert r.status_code == 422 and not server.requests  # unresolvable here: refused before any request
        c.post("/ui/api/llm/models", json={"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1"})
        assert server.requests[-1].headers["authorization"] == f"Bearer {LLM_KEY}"


def test_llm_save_switches_provider_live_and_remembers_recent(tmp_path):
    server = ProviderServer()
    e = Env(tmp_path, llm_transport=server.transport)
    with e.client(login=False) as c:
        login(c)
        r = c.post("/ui/api/llm/save", json={"provider": "anthropic", "base_url": "https://api.anthropic.com",
                                             "model": "claude-sonnet-4-5", "api_key": LLM_KEY})
        assert r.status_code == 200, r.text
        assert isinstance(e.ai.llm, AnthropicClient)
        assert c.get("/ui/api/llm/providers").json()["recent_models"][0] == "claude-sonnet-4-5"
        t = c.post("/ui/api/llm/test", json={}).json()
        assert t["provider"] == "anthropic" and t["saved"] is True
        req = server.requests[-1]
        assert req.url.path == "/v1/messages" and req.headers["x-api-key"] == LLM_KEY


# ------------------------------------------------------------------ Anthropic adapter mapping


def test_to_anthropic_maps_system_tools_and_results():
    msgs = [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "", "tool_calls": [tool_call("memory_list", {"limit": 3}, "c1"),
                                                            tool_call("web_search", '{"query": "x"}', "c2")]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        {"role": "tool", "tool_call_id": "c2", "content": '{"ok": false}'},
        {"role": "system", "content": "answer now"},
    ]
    tools = [{"type": "function", "function": {"name": "memory_list", "description": "d",
                                               "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}}}}]
    system, out, a_tools = to_anthropic(msgs, tools)
    assert system == "sys prompt"
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "memory_list", "input": {"limit": 3}}
    assert out[1]["content"][1]["input"] == {"query": "x"}
    results = [b for b in out[2]["content"] if b["type"] == "tool_result"]
    assert [b["tool_use_id"] for b in results] == ["c1", "c2"]
    assert out[2]["content"][-1] == {"type": "text", "text": "[system] answer now"}
    assert a_tools == [{"name": "memory_list", "description": "d",
                        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}}]


def test_from_anthropic_maps_tool_use():
    msg = from_anthropic({"content": [{"type": "text", "text": "Bakıyorum."},
                                      {"type": "tool_use", "id": "tu1", "name": "web_search", "input": {"query": "hava"}}],
                          "usage": {"input_tokens": 5, "output_tokens": 2}})
    assert msg["content"] == "Bakıyorum."
    assert msg["tool_calls"][0]["function"] == {"name": "web_search", "arguments": '{"query": "hava"}'}
    assert msg["_usage"] == {"prompt_tokens": 5, "completion_tokens": 2}


def test_agent_runs_tools_through_anthropic_adapter(tmp_path):
    server = ProviderServer()
    server.anthropic_script = [
        {"type": "message", "content": [{"type": "tool_use", "id": "tu1", "name": "memory_list", "input": {}}]},
        {"type": "message", "content": [{"type": "text", "text": "Hiç anı yok."}]},
    ]
    e = Env(tmp_path, llm_transport=server.transport,
            settings_kw={"llm_provider": "anthropic", "llm_base_url": "https://api.anthropic.com"})
    assert isinstance(e.ai.llm, AnthropicClient)
    answer = asyncio.run(e.ai.agent.run(CHAT, "ne hatırlıyorsun?"))
    assert answer == "Hiç anı yok."
    second = json.loads(server.requests[-1].content)
    assert second["system"] and second["tools"] and second["max_tokens"] > 0
    blocks = second["messages"][-1]["content"]
    assert blocks[0]["type"] == "tool_result" and blocks[0]["tool_use_id"] == "tu1"


def test_list_models_handles_unreachable():
    async def go():
        def boom(request):
            raise httpx.ConnectError("down")
        from ai_bot.llm import LLMError
        with pytest.raises(LLMError):
            await list_models(BY_ID["openai"], BY_ID["openai"].base_url, "k", transport=httpx.MockTransport(boom))
    asyncio.run(go())


# ------------------------------------------------------------------ closed page


def test_closed_page_is_short(tmp_path):
    e = Env(tmp_path, auth="komuta")
    with e.client(login=False) as c:
        page = c.get("/ui/")
        assert page.status_code == 403 and "<details" in page.text
        text = re.sub(r"<[^>]+>", " ", page.text.split("<body", 1)[1])
        visible = text.split("Ayrıntı")[0]
        assert len(" ".join(visible.split())) < 260  # one short paragraph before "Ayrıntı"


def test_wizard_is_dismissed_once_and_persisted(tmp_path, env_vars):
    e = Env(tmp_path, from_env=True)
    with e.client(login=False) as c:
        login(c)
        assert c.get("/ui/api/setup").json()["dismissed"] is False
        assert c.get("/ui/api/overview").json()["setup"]["dismissed"] is False
        assert c.post("/ui/api/setup/dismiss").json()["dismissed"] is True
        assert c.get("/ui/api/overview").json()["setup"]["dismissed"] is True
        st = c.get("/ui/api/setup").json()
        assert st["completed"] is False and st["all_ready"] is False  # dismissing does not fake completion


def test_model_prices_are_parsed_per_provider():
    from ai_bot.providers import parse_pricing
    assert parse_pricing({"pricing": {"prompt": "0.000003", "completion": "0.000015"}}) == (3.0, 15.0)  # OpenRouter, per token
    assert parse_pricing({"pricing": {"prompt": "0", "completion": "0"}}) == (0.0, 0.0)  # free
    assert parse_pricing({"pricing": {"prompt": "-1", "completion": "-1"}}) == (None, None)  # "varies"
    assert parse_pricing({"pricing": {"input": 0.88, "output": 0.88}}) == (0.88, 0.88)  # Together, per 1M
    assert parse_pricing({"pricing": {"prompt": "abc"}}) == (None, None)
    assert parse_pricing({"pricing": "x"}) == (None, None) and parse_pricing({}) == (None, None)


def test_model_list_carries_prices(tmp_path):
    import asyncio
    body = {"data": [{"id": "a/paid", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "supported_parameters": ["tools"]},
                     {"id": "b/free", "pricing": {"prompt": "0", "completion": "0"}}, {"id": "c/none"}]}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    prov = BY_ID["openrouter"]
    models = asyncio.run(list_models(prov, prov.base_url, None, transport=transport))
    by = {m.id: m.public() for m in models}
    assert by["a/paid"]["price_in"] == 1.0 and by["a/paid"]["price_out"] == 2.0
    assert by["b/free"]["price_in"] == 0.0 and by["c/none"]["price_in"] is None


def test_blocked_search_is_explained_in_turkish(tmp_path, monkeypatch):
    from stack_kit import ToolError
    e = Env(tmp_path)
    c = e.client()
    try:
        async def boom(*a, **k):
            raise ToolError("duckduckgo refused the automated request (bot protection).", code="search_unavailable")
        monkeypatch.setattr(e.ai.search, "search", boom)
        r = c.post("/ui/api/search/test")
        assert r.status_code == 424 and r.json()["error"]["code"] == "search_blocked"
        assert "Tavily" in r.json()["error"]["message"] and "bot koruması" in r.json()["error"]["message"]
    finally:
        c.__exit__(None, None, None)


def test_guarded_transport_falls_back_to_ipv4_when_ipv6_cannot_connect():
    """A pod without IPv6 must not die on the first (IPv6) address."""
    import asyncio
    from ai_bot.netpolicy import GuardedTransport, NetPolicy
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        if ":" in request.url.host:
            raise httpx.ConnectError("network unreachable", request=request)
        return httpx.Response(200, json={"ok": True})

    async def resolver(host, port):
        return ["2606:4700::1111", "104.18.2.115"]  # system order puts IPv6 first

    async def run():
        tr = GuardedTransport(NetPolicy(allow_private=False, resolver=resolver), httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=tr) as client:
            return await client.get("https://example.org/x")

    resp = asyncio.run(run())
    assert resp.status_code == 200 and tried == ["104.18.2.115"]  # IPv4 is tried first, no failed attempt at all


def test_guarded_transport_still_blocks_private_addresses():
    import asyncio
    from ai_bot.netpolicy import GuardedTransport, NetPolicy

    async def resolver(host, port):
        return ["104.18.2.115", "10.0.0.5"]  # one private address poisons the whole host

    async def run():
        tr = GuardedTransport(NetPolicy(allow_private=False, resolver=resolver), httpx.MockTransport(lambda r: httpx.Response(200)))
        async with httpx.AsyncClient(transport=tr) as client:
            await client.get("https://example.org/x")

    import pytest
    with pytest.raises(httpx.ConnectError):
        asyncio.run(run())


def test_two_instances_can_share_the_database_during_a_rolling_update(tmp_path):
    """Old and new instance overlap: both open the same file and write without 'database is locked'."""
    import threading
    from ai_bot.db import Database
    old = Database(tmp_path / "ai.db")
    new = Database(tmp_path / "ai.db")  # opened while the old instance is still running
    errors: list[Exception] = []

    def writer(db, base):
        try:
            for i in range(60):
                db.add_memory(base + i, f"kayıt {base}-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(old, 1000)), threading.Thread(target=writer, args=(new, 2000))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert old.count_all_memories() == 120
    old.close()
    assert new.count_all_memories() == 120  # the survivor keeps working after the old one left
    new.close()


def test_panel_language_is_kept_on_the_server(tmp_path):
    e = Env(tmp_path)
    c = e.client()
    try:
        assert c.get("/ui/api/setup").json()["lang"] is None  # not chosen yet: the wizard asks first
        assert c.post("/ui/api/setup/lang", json={"lang": "xx"}).status_code == 422
        assert c.post("/ui/api/setup/lang", json={"lang": "en"}).json()["lang"] == "en"
        assert c.get("/ui/api/setup").json()["lang"] == "en"
        assert c.get("/ui/api/overview").json()["setup"]["lang"] == "en"
    finally:
        c.__exit__(None, None, None)


def test_panel_page_inlines_icon_sprites(client):
    """Icons ship inside the HTML (no second request on slow networks)."""
    page = client.get("/ui/").text
    assert 'class="kui-sprite"' in page
    assert "icons.svg#" not in page
    assert '<symbol id="globe"' in page and '<symbol id="home"' in page  # app + kit symbols
    assert 'href="#globe"' in page


def test_panel_page_and_api_carry_build_id(client):
    import re
    page = client.get("/ui/").text
    meta = re.search(r'<meta name="kui-build" content="([0-9a-f]{12})">', page)
    assert meta
    assert client.get("/ui/api/overview").headers["X-UI-Build"] == meta.group(1)
