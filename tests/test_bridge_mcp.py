"""Kit integration: the built-in tools are served on /bridge and /mcp."""

from fastapi.testclient import TestClient

from stack_kit import BridgeConfig, create_app

from tests.conftest import CHAT

TOKEN = "bridge-token-for-tests-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def client(h) -> TestClient:
    return TestClient(create_app(h.stack, config=BridgeConfig(token=TOKEN), plugins_dir="plugins"))


def rpc(c, method, params=None, id_=1):
    r = c.post("/mcp", headers={**AUTH, "Accept": "application/json, text/event-stream"},
               json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()


def test_manifest_and_bridge_calls(make_harness):
    h = make_harness()
    with client(h) as c:
        assert c.get("/health").json()["ok"] is True
        assert c.get("/bridge/manifest").status_code == 401
        m = c.get("/bridge/manifest", headers=AUTH).json()
        names = {t["name"] for t in m["tools"]}
        assert {"web.search", "web.read", "schedule.add", "memory.remember", "ai.ask", "ornek.zar",
                "haber.basliklar"} <= names
        assert {"hafiza", "gorevler", "model", "sifirla", "help"} <= {x["name"] for x in m["commands"]}
        r = c.post("/bridge/call/memory.remember", headers=AUTH, json={"args": {"content": "Köprüden gelen anı."}})
        assert r.json()["ok"] is True
        assert h.app.db.recent_memories(CHAT, 1)[0]["content"] == "Köprüden gelen anı."  # default chat
        r = c.post("/bridge/call/memory.remember", headers=AUTH, json={"args": {"content": "x", "chat_id": 42}})
        assert r.json()["error"]["code"] == "forbidden"  # not in ALLOWED_CHAT_IDS
        r = c.post("/bridge/call/web.read", headers=AUTH, json={"args": {"url": "http://169.254.169.254/latest"}})
        assert r.json()["ok"] is False and r.json()["error"]["code"] == "blocked"


def test_mcp_tools_list_and_call(make_harness):
    h = make_harness()
    h.app.memory.remember(CHAT, "MCP üzerinden görünen anı.")
    with client(h) as c:
        init = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "t", "version": "1"}})
        assert init["result"]["serverInfo"]["name"] == "ai-bot"
        tools = {t["name"] for t in rpc(c, "tools/list")["result"]["tools"]}
        assert {"web_search", "web_read", "memory_list", "schedule_add", "ai_ask"} <= tools
        res = rpc(c, "tools/call", {"name": "memory_list", "arguments": {}})["result"]
        assert res["isError"] is False and "MCP üzerinden görünen anı." in res["content"][0]["text"]
        res = rpc(c, "tools/call", {"name": "schedule_add", "arguments": {"when": "1 saat sonra", "text": "MCP"}})["result"]
        assert res["isError"] is False
        assert h.app.scheduler.list(CHAT)[0]["text"] == "MCP"
        # confirm tools from plugins stay two-step over MCP
        res = rpc(c, "tools/call", {"name": "ornek_bagis", "arguments": {"miktar": 1}})["result"]
        assert "NOT executed" in res["content"][0]["text"]


def test_events_are_emitted(make_harness):
    h = make_harness()
    with client(h) as c:
        c.post("/bridge/call/memory.remember", headers=AUTH, json={"args": {"content": "Olay testi."}})
        events = c.get("/bridge/events", headers=AUTH).json()
        names = [e["event"] for e in events["events"]]
        assert "memory.remembered" in names
        m = c.get("/bridge/manifest", headers=AUTH).json()
        assert {"ai.reply", "schedule.fired"} <= set(m["events"])
