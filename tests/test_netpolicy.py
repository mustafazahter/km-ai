"""Network policy for admin-configurable endpoints (LLM base URL, model list, SearXNG): SSRF fixes."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ai_bot.memory import BLOCK_PATTERNS
from ai_bot.netpolicy import GuardedTransport, NetBlocked, NetPolicy, address_problem
from tests.fakes import static_resolver
from tests.test_ui import LLM_KEY, Env
from tests.test_ui_v2 import ProviderServer, login


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.1.2.3", "192.168.1.5", "::1", "::ffff:127.0.0.1", "fd12::1"])
def test_private_needs_opt_in(addr):
    assert address_problem(addr, allow_private=False)
    assert address_problem(addr, allow_private=True) is None


@pytest.mark.parametrize("addr", ["169.254.169.254", "::ffff:169.254.169.254", "fe80::1", "0.0.0.0", "::",
                                  "fd00:ec2::254", "100.100.100.200", "224.0.0.1"])
def test_always_blocked(addr):
    assert address_problem(addr, allow_private=True)


def test_public_ok():
    assert address_problem("93.184.216.34", allow_private=False) is None


def run(coro):
    return asyncio.run(coro)


def test_policy_checks():
    pol = NetPolicy(allow_private=True, own_port=8080,
                    resolver=static_resolver({"lan.example": ["192.168.1.10"], "meta.example": ["169.254.169.254"],
                                              "rebind.example": ["93.184.216.34", "127.0.0.1"]}))
    assert run(pol.check("http://lan.example:11434/v1")) == ("lan.example", "192.168.1.10")
    for url in ("http://metadata.google.internal/v1", "http://meta.example/v1", "http://127.0.0.1:8080/v1",
                "http://0.0.0.0:11434/v1", "http://[::ffff:169.254.169.254]/x", "http://127.0.0.1:11434/ui/api/x",
                "http://127.0.0.1:11434/bridge/call/x", "ftp://x.example", "http://user:pw@lan.example/v1"):
        with pytest.raises(NetBlocked):
            run(pol.check(url))
    strict = NetPolicy(allow_private=False, resolver=pol.resolver)
    for url in ("http://lan.example/v1", "http://rebind.example/v1", "http://localhost.invalid/v1"):
        with pytest.raises(NetBlocked):
            run(strict.check(url))


def test_guarded_transport_pins_ip_and_keeps_host():
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers["host"], request.extensions.get("sni_hostname")))
        return httpx.Response(200, json={})

    pol = NetPolicy(allow_private=False, resolver=static_resolver({"api.example": ["93.184.216.34"]}))

    async def go():
        async with httpx.AsyncClient(transport=GuardedTransport(pol, httpx.MockTransport(handler))) as c:
            await c.get("https://api.example/v1/models")
            with pytest.raises(httpx.ConnectError):
                await c.get("http://169.254.169.254/latest/meta-data/")
    run(go())
    assert seen == [("93.184.216.34", "api.example", "api.example")]


# ------------------------------------------------------------------ panel endpoints


def test_cloud_presets_locked_to_their_host(tmp_path):
    server = ProviderServer()
    e = Env(tmp_path, llm_transport=server.transport)
    with e.client(login=False) as c:
        login(c)
        for base in ("http://127.0.0.1:2375/v1", "http://169.254.169.254/v1", "https://evil.example/v1"):
            r = c.post("/ui/api/llm/models", json={"provider": "openai", "base_url": base, "api_key": LLM_KEY})
            assert r.status_code == 422 and r.json()["error"]["code"] == "url_locked"
            r = c.post("/ui/api/llm/save", json={"provider": "openai", "base_url": base, "model": "gpt-x"})
            assert r.status_code == 422
        assert not server.requests


def test_local_providers_allowed_but_metadata_and_own_port_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTP_PORT", "8090")
    server = ProviderServer()
    e = Env(tmp_path, llm_transport=server.transport, dns={"lan.example": ["192.168.1.10"],
                                                           "meta.example": ["169.254.169.254"]})
    with e.client(login=False) as c:
        login(c)
        assert c.post("/ui/api/llm/models", json={"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1"}).status_code == 200
        assert c.post("/ui/api/llm/models", json={"provider": "vllm", "base_url": "http://lan.example:8000/v1"}).status_code == 200
        n = len(server.requests)
        for body in ({"provider": "vllm", "base_url": "http://169.254.169.254/v1"},
                     {"provider": "vllm", "base_url": "http://meta.example/v1"},
                     {"provider": "custom", "base_url": "http://[::ffff:169.254.169.254]/v1", "allow_private": True},
                     {"provider": "custom", "base_url": "http://0.0.0.0:11434/v1", "allow_private": True},
                     {"provider": "custom", "base_url": "http://127.0.0.1:8090/v1", "allow_private": True},
                     {"provider": "custom", "base_url": "http://127.0.0.1:9999/ui/api", "allow_private": True},
                     {"provider": "custom", "base_url": "http://lan.example/v1"},  # private without the opt-in
                     {"provider": "custom", "base_url": "http://127.0.0.1:9999/v1"}):
            for path in ("/ui/api/llm/models", "/ui/api/llm/test"):
                r = c.post(path, json=body)
                assert r.status_code == 422, (path, body, r.text)
        assert len(server.requests) == n  # nothing was contacted
        r = c.post("/ui/api/llm/models", json={"provider": "custom", "base_url": "http://lan.example/v1", "allow_private": True})
        assert r.status_code == 200


def test_saved_key_only_to_saved_host(tmp_path):
    server = ProviderServer()
    e = Env(tmp_path, llm_transport=server.transport, dns={"lan.example": ["192.168.1.10"]})
    with e.client(login=False) as c:
        login(c)
        c.put("/ui/api/secrets/LLM_API_KEY", json={"value": LLM_KEY})
        c.post("/ui/api/llm/models", json={"provider": "custom", "base_url": "http://lan.example/v1", "allow_private": True})
        assert "authorization" not in server.requests[-1].headers
        assert server.requests[-1].headers["host"] == "lan.example"  # pinned to the checked IP
        assert server.requests[-1].url.host == "192.168.1.10"


def test_no_raw_body_reflection_and_no_redirects(tmp_path):
    secret_body = "INTERNAL-ADMIN-PAGE root:x:0:0"

    def handler(request):
        p = request.url.path
        if p.endswith("/models") and "redir" in request.url.path:
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        if p.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "ok-model"}, {"id": "<script>x</script>"},
                                                      {"id": "a" * 300}, {"id": "bad\nid"},
                                                      {"id": "m2", "display_name": "x\x00y" + secret_body}]})
        return httpx.Response(500, text=secret_body, json=None) if False else httpx.Response(400, json={"error": {"message": secret_body}})

    e = Env(tmp_path, llm_transport=httpx.MockTransport(handler))
    with e.client(login=False) as c:
        login(c)
        r = c.post("/ui/api/llm/models", json={"provider": "custom", "base_url": "http://127.0.0.1:9999/v1", "allow_private": True})
        ids = [m["id"] for m in r.json()["models"]]
        assert ids == ["m2", "ok-model"]
        assert all(len(m["name"]) <= 80 and "\x00" not in m["name"] for m in r.json()["models"])
        r = c.post("/ui/api/llm/test", json={"provider": "custom", "base_url": "http://127.0.0.1:9999/v1",
                                             "allow_private": True, "model": "ok-model"})
        assert r.status_code == 424 and secret_body not in r.text
        r = c.post("/ui/api/llm/models", json={"provider": "custom", "base_url": "http://127.0.0.1:9999/redir/v1",
                                               "allow_private": True})
        assert r.status_code == 424 and "yönlendirme" in r.json()["error"]["message"]


def test_searxng_url_policy(tmp_path):
    e = Env(tmp_path, dns={"searx.lan": ["169.254.10.10"]})
    e.ai.settings.search_provider, e.ai.settings.searxng_url = "searxng", "http://searx.lan"
    from stack_kit import ToolError
    with pytest.raises(ToolError) as info:
        asyncio.run(e.ai.search.search("q", 3))
    assert "network policy" in info.value.message
    assert e.ai.search.searx_http.follow_redirects is False
    with e.client(login=False) as c:
        login(c)
        assert c.put("/ui/api/config/SEARXNG_URL", json={"value": "http://169.254.169.254"}).status_code == 422
        assert c.put("/ui/api/config/LLM_BASE_URL", json={"value": "http://127.0.0.1:1/bridge/call"}).status_code == 422


@pytest.mark.parametrize("text", ["pw: hunter22", "pass: abc123", "pwd=xyz", "PIN: 1234"])
def test_memory_blocks_short_password_forms(text):
    assert any(p.search(text) for p in BLOCK_PATTERNS)
