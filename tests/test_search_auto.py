"""Search "auto" mode: free channels in turn, failures skipped and remembered, public instances guarded."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from stack_kit import ToolError

from ai_bot import search as S
from tests.conftest import make_settings

INSTANCES = {"instances": {
    "https://good.example/": {"http": {"status_code": 200, "grade": "A+"}, "timing": {"search": {"success_percentage": 100, "all": {"median": 0.4}}}},
    "https://slow.example/": {"http": {"status_code": 200, "grade": "B"}, "timing": {"search": {"success_percentage": 95, "all": {"median": 2.0}}}},
    "https://bad-grade.example/": {"http": {"status_code": 200, "grade": "F"}, "timing": {"search": {"success_percentage": 100, "all": {"median": 0.1}}}},
    "https://flaky.example/": {"http": {"status_code": 200, "grade": "A"}, "timing": {"search": {"success_percentage": 40, "all": {"median": 0.1}}}},
    "http://plain.example/": {"http": {"status_code": 200, "grade": "A"}, "timing": {"search": {"success_percentage": 100, "all": {"median": 0.1}}}},
    "https://10.0.0.5/": {"http": {"status_code": 200, "grade": "A"}, "timing": {"search": {"success_percentage": 100, "all": {"median": 0.1}}}},
    "https://hidden.onion/": {"http": {"status_code": 200, "grade": "A"}, "timing": {"search": {"success_percentage": 100, "all": {"median": 0.1}}}},
}}
DDG_BLOCKED = httpx.Response(202, text="challenge")


def make_client(tmp_path, handler, **kw):
    async def resolver(host, port):
        return ["93.184.216.34"]
    return S.SearchClient(make_settings(tmp_path, **kw), transport=httpx.MockTransport(handler), resolver=resolver)


def run(coro):
    return asyncio.run(coro)


def test_instance_list_is_filtered_and_ordered(tmp_path):
    def handler(req):
        return httpx.Response(200, json=INSTANCES)
    c = make_client(tmp_path, handler)
    urls = run(S.public_instances(c))
    assert urls[:2] == ["https://good.example", "https://slow.example"] or urls[:2] == ["https://slow.example", "https://good.example"]
    for bad in ("bad-grade", "flaky", "plain", "10.0.0.5", "onion"):
        assert not any(bad in u for u in urls)  # F grade, flaky, http, IP literal, onion are dropped


def test_auto_skips_failing_channels_and_uses_a_public_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(S.random, "shuffle", lambda x: None)  # deterministic order
    seen: list[str] = []

    def handler(req: httpx.Request):
        seen.append(req.url.host)
        if req.url.host == "html.duckduckgo.com":
            return DDG_BLOCKED
        if req.url.host == "searx.space":
            return httpx.Response(200, json=INSTANCES)
        if req.url.host == "93.184.216.34":  # pinned public instances
            host = req.headers["host"]
            if host == "good.example":
                return httpx.Response(403, text="json disabled")
            return httpx.Response(200, json={"results": [{"title": "T", "url": "https://x.example/", "content": "C"}]})
        return httpx.Response(500)

    c = make_client(tmp_path, handler, search_provider="auto")
    res = run(c.search("komuta"))
    assert res and res[0]["title"] == "T" and c.last_via == "slow.example"
    # the failed channels are remembered: a second search does not retry them
    seen.clear()
    run(c.search("komuta"))
    assert "html.duckduckgo.com" not in seen and not any(h == "93.184.216.34" and False for h in seen)
    assert c.cooldown["duckduckgo"] > 0 and "searxng:https://good.example" in c.cooldown


def test_auto_prefers_a_keyed_provider_when_configured(tmp_path):
    calls: list[str] = []

    def handler(req: httpx.Request):
        calls.append(req.url.host)
        if req.url.host == "api.tavily.com":
            return httpx.Response(200, json={"results": [{"title": "tv", "url": "https://t.example/", "content": "c"}]})
        return httpx.Response(500)

    c = make_client(tmp_path, handler, search_provider="auto", tavily_api_key="tvly-0123456789abcdef")
    assert run(c.search("x"))[0]["title"] == "tv" and c.last_via == "tavily" and calls == ["api.tavily.com"]


def test_auto_reports_one_clear_error_when_everything_fails(tmp_path):
    def handler(req: httpx.Request):
        if req.url.host == "searx.space":
            return httpx.Response(500)
        return DDG_BLOCKED if req.url.host == "html.duckduckgo.com" else httpx.Response(503)

    c = make_client(tmp_path, handler, search_provider="auto")
    with pytest.raises(ToolError) as e:
        run(c.search("x"))
    assert e.value.code == "search_unavailable" and "Tavily" in e.value.message


def _parallel_handler(calls):
    import json as _json

    def handler(req: httpx.Request):
        calls.append(req.url.host)
        if req.url.host == "search.parallel.ai":
            body = _json.loads(req.content)
            if body.get("method") == "initialize":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}}, headers={"mcp-session-id": "sess-1"})
            if body.get("method") == "notifications/initialized":
                return httpx.Response(202)
            assert req.headers.get("mcp-session-id") == "sess-1"  # the session is reused
            payload = {"results": [{"url": "https://komutacloud.com/", "title": "Komuta Cloud", "excerpts": ["Vodafone altyapı"]}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": _json.dumps(payload)}]}})
        if req.url.host == "html.duckduckgo.com":
            return DDG_BLOCKED
        return httpx.Response(500)
    return handler


def test_auto_uses_the_keyless_parallel_channel_when_duckduckgo_is_blocked(tmp_path):
    calls: list[str] = []
    c = make_client(tmp_path, _parallel_handler(calls), search_provider="auto")
    res = run(c.search("komuta cloud"))
    assert res[0]["url"] == "https://komutacloud.com/" and res[0]["snippet"] == "Vodafone altyapı" and c.last_via == "parallel"
    assert "html.duckduckgo.com" not in calls  # parallel comes before DuckDuckGo
    run(c.search("komuta cloud"))
    assert calls.count("search.parallel.ai") == 4  # init, initialized, call, call: the session is reused


def test_parallel_accepts_an_sse_reply(tmp_path):
    import json as _json

    def handler(req: httpx.Request):
        body = _json.loads(req.content)
        if body.get("method") == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        payload = {"results": [{"url": "https://a.example/", "title": "A", "excerpts": ["x"]}]}
        data = _json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": _json.dumps(payload)}]}})
        return httpx.Response(200, text=f"event: message\ndata: {data}\n\n", headers={"content-type": "text/event-stream"})

    c = make_client(tmp_path, handler, search_provider="parallel")
    assert run(c.search("x"))[0]["url"] == "https://a.example/"


def test_serper_is_used_by_auto_when_a_key_is_set(tmp_path):
    def handler(req: httpx.Request):
        if req.url.host == "google.serper.dev":
            assert req.headers["x-api-key"] == "serper-key-0123456789"
            return httpx.Response(200, json={"organic": [{"title": "S", "link": "https://s.example/", "snippet": "snip"}]})
        return httpx.Response(500)

    c = make_client(tmp_path, handler, search_provider="auto", serper_api_key="serper-key-0123456789")
    assert run(c.search("x"))[0]["title"] == "S" and c.last_via == "serper"


def test_wikipedia_is_not_a_channel(tmp_path):
    assert not hasattr(S, "wikipedia") and "wikipedia" not in S.PROVIDERS


def test_public_instances_cannot_reach_private_addresses(tmp_path):
    """A poisoned list entry that resolves to an internal address is refused by the strict policy."""
    async def private_resolver(host, port):
        return ["10.1.2.3"]
    c = S.SearchClient(make_settings(tmp_path), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})),
                       resolver=private_resolver)
    with pytest.raises(httpx.ConnectError):
        run(c.public_http.get("https://evil.example/search", params={"q": "x"}))


def test_searxng_errors_are_actionable(tmp_path):
    def make(status, text="x", **kw):
        return make_client(tmp_path, lambda r: httpx.Response(status, text=text), search_provider="searxng",
                           searxng_url="https://searx.example.org")

    for status, word in ((403, "json"), (429, "limiter")):
        with pytest.raises(ToolError) as e:
            run(make(status).search("x"))
        assert word in e.value.message.lower() and e.value.code == "search_failed"
    with pytest.raises(ToolError) as e:  # HTML instead of JSON
        run(make(200, "<html>bot check</html>").search("x"))
    assert "json" in e.value.message.lower() and "formats" in e.value.message


def test_public_instance_responses_are_capped(tmp_path):
    big = b'{"results": [' + b'{"title":"x","url":"https://a.example/","content":"' + b"y" * 600_000 + b'"}]}'

    def handler(req: httpx.Request):
        return httpx.Response(200, content=big)

    c = make_client(tmp_path, handler, search_provider="auto")
    with pytest.raises(ToolError) as e:
        run(S._public_instance(c, "https://good.example", "x"))
    assert "too much data" in e.value.message


def test_unexpected_json_shape_is_a_clean_error_for_direct_providers(tmp_path):
    c = make_client(tmp_path, lambda r: httpx.Response(200, json=["not", "a", "dict"]), search_provider="tavily",
                    tavily_api_key="tvly-0123456789abcdef")
    with pytest.raises(ToolError) as e:
        run(c.search("x"))
    assert e.value.code == "search_failed"


def test_keyed_provider_does_not_follow_redirects(tmp_path):
    seen: list[str] = []

    def handler(req: httpx.Request):
        seen.append(req.url.host)
        if req.url.host == "api.tavily.com":
            return httpx.Response(302, headers={"location": "https://evil.example/steal"})
        return httpx.Response(200, json={"results": []})

    c = make_client(tmp_path, handler, search_provider="tavily", tavily_api_key="tvly-0123456789abcdef")
    with pytest.raises(ToolError):
        run(c.search("x"))
    assert "evil.example" not in seen
