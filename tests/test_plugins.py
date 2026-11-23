import asyncio

import httpx

from stack_kit import Stack, load_plugins

from tests.conftest import CHAT
from tests.fakes import static_resolver

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Birinci haber</title><link>https://news.example/1</link><pubDate>Thu, 19 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>Ikinci haber</title><link>https://news.example/2</link></item>
</channel></rss>"""


def test_plugins_load_cleanly(make_harness):
    h = make_harness()
    loaded = load_plugins(h.stack, "plugins")
    assert set(loaded) == {"haber_ozet", "ornek_arac"} and h.stack.plugin_errors == []
    r = asyncio.run(h.stack.call("ornek.zar", {"sides": 6, "count": 3}))
    assert r["ok"] and len(r["result"]["rolls"]) == 3
    r = asyncio.run(h.stack.call("ornek.bagis", {"miktar": 10}))
    assert r["needs_confirmation"] is True  # confirm=True example


def test_haber_plugin_uses_safe_fetcher(make_harness):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/rss+xml"}, content=RSS)

    h = make_harness(web_transport=httpx.MockTransport(handler),
                     resolver=static_resolver({"news.example": ["93.184.216.34"]}))
    load_plugins(h.stack, "plugins")
    r = asyncio.run(h.stack.call("haber.basliklar", {"feed": "https://news.example/rss", "limit": 5}))
    assert r["ok"], r
    items = r["result"]["feeds"][0]["items"]
    assert [i["title"] for i in items] == ["Birinci haber", "Ikinci haber"]
    blocked = asyncio.run(h.stack.call("haber.basliklar", {"feed": "http://127.0.0.1/rss"}))
    assert blocked["ok"] is False and "blocked" not in str(r) and "private" in blocked["error"]["message"]


def test_web_read_extracts_text(make_harness):
    html = b"<html><head><title>Sayfa</title><script>evil()</script></head><body><nav>menu</nav>" \
           b"<h1>Baslik</h1><p>Merhaba <b>dunya</b></p></body></html>"
    h = make_harness(web_transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html)),
        resolver=static_resolver({"site.example": ["93.184.216.34"]}))
    r = asyncio.run(h.stack.call("web.read", {"url": "https://site.example/"}))
    assert r["ok"] and r["result"]["title"] == "Sayfa"
    assert "Merhaba dunya" in r["result"]["text"] and "evil" not in r["result"]["text"]


def test_web_search_providers(make_harness):
    ddg_html = ('<div><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.example%2Fx">'
                'A &amp; B</a><a class="result__snippet" href="x">Snippet <b>one</b></a></div>')

    def handler(request: httpx.Request):
        host = request.headers.get("host") or request.url.host
        if host == "html.duckduckgo.com":
            return httpx.Response(200, text=ddg_html)
        if host == "api.tavily.com":
            return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t.example", "content": "c"}]})
        if host == "api.search.brave.com":
            assert request.headers["x-subscription-token"] == "brave-key-123456"
            return httpx.Response(200, json={"web": {"results": [{"title": "Br", "url": "https://b.example",
                                                                  "description": "<strong>d</strong>"}]}})
        if host == "searx.example":
            return httpx.Response(200, json={"results": [{"title": "S", "url": "https://s.example", "content": "s"}]})
        return httpx.Response(404)

    h = make_harness(tavily_api_key="tvly-key-123456", brave_api_key="brave-key-123456",
                     searxng_url="https://searx.example")
    h.app.search.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # SEARXNG_URL goes through the network policy: resolved, checked and pinned (Host header keeps the name)
    from ai_bot.netpolicy import GuardedTransport, NetPolicy
    from tests.fakes import static_resolver
    h.app.search.searx_http = httpx.AsyncClient(transport=GuardedTransport(
        NetPolicy(allow_private=True, resolver=static_resolver({"searx.example": ["93.184.216.34"]})),
        httpx.MockTransport(handler)))

    async def go():
        out = {}
        for p in ("duckduckgo", "tavily", "brave", "searxng"):
            out[p] = await h.app.search.search("q", 3, provider=p)
        return out

    out = asyncio.run(go())
    assert out["duckduckgo"] == [{"title": "A & B", "url": "https://a.example/x", "snippet": "Snippet one"}]
    assert out["tavily"][0]["url"] == "https://t.example"
    assert out["brave"][0]["snippet"] == "d"
    assert out["searxng"][0]["title"] == "S"


def test_duckduckgo_challenge_page_is_reported(make_harness):
    import pytest
    from stack_kit import ToolError
    h = make_harness()
    h.app.search.http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(202, text="<html><title>DuckDuckGo</title></html>")))
    with pytest.raises(ToolError) as exc:
        asyncio.run(h.app.search.search("q", provider="duckduckgo"))
    assert exc.value.code == "search_unavailable"
