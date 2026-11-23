"""Web search providers behind one interface.

``SEARCH_PROVIDER``: ``auto`` (default: tries the free channels in turn and skips the ones that fail),
``parallel`` (keyless MCP), ``duckduckgo`` (no key; best effort HTML scraping), ``tavily``
(``TAVILY_API_KEY``), ``brave`` (``BRAVE_API_KEY``), ``serper`` (``SERPER_API_KEY``), ``searxng`` (``SEARXNG_URL``).
Add your own with :func:`register_provider` (see ``.claude/skills/add-search-provider``).
"""

from __future__ import annotations

import asyncio
import html
import random
import re
import time
from typing import Awaitable, Callable
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from stack_kit import ToolError

from .config import Settings
from .netpolicy import GuardedTransport, NetPolicy
from .safefetch import Resolver

Result = dict  # {"title", "url", "snippet"}
ProviderFn = Callable[["SearchClient", str, int], Awaitable[list[Result]]]
PROVIDERS: dict[str, ProviderFn] = {}


def register_provider(name: str) -> Callable[[ProviderFn], ProviderFn]:
    def deco(fn: ProviderFn) -> ProviderFn:
        PROVIDERS[name.lower()] = fn
        return fn

    return deco


class SearchClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Resolver | None = None):
        self.settings = settings
        headers = {"User-Agent": "Mozilla/5.0 (compatible; km-ai/1.0)"}
        # fixed public APIs (duckduckgo, tavily, brave)
        self.http = httpx.AsyncClient(timeout=15.0, transport=transport, follow_redirects=False, headers=headers)  # keys travel in headers/bodies: never follow a redirect
        # SEARXNG_URL is admin-configurable: network policy (private allowed for self-hosted, link-local,
        # metadata and this service refused, pinned IP), redirects never followed
        self.searx_http = httpx.AsyncClient(
            timeout=15.0, transport=GuardedTransport(NetPolicy(allow_private=True, resolver=resolver), transport),
            follow_redirects=False, headers=headers)
        # public SearXNG instances come from a third-party list: strict policy, public addresses only
        self.public_http = httpx.AsyncClient(
            timeout=4.0, transport=GuardedTransport(NetPolicy(allow_private=False, resolver=resolver), transport),
            follow_redirects=False, headers=headers)
        self.cooldown: dict[str, float] = {}      # channel -> unix time until which "auto" skips it
        self.last_via: str | None = None          # which channel answered last ("auto" mode)
        self._instances: tuple[float, list[str]] | None = None

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.searx_http.aclose()
        await self.public_http.aclose()

    async def search(self, query: str, max_results: int | None = None, provider: str | None = None) -> list[Result]:
        name = (provider or self.settings.search_provider or "duckduckgo").lower()
        fn = PROVIDERS.get(name)
        if fn is None:
            raise ToolError(f"unknown SEARCH_PROVIDER {name!r}; choose one of {sorted(PROVIDERS)}", code="config")
        n = max(1, min(max_results or self.settings.search_max_results, 10))
        try:
            results = await fn(self, query, n)
        except ToolError:
            raise
        except httpx.TimeoutException:
            raise ToolError("arama sağlayıcısı zaman aşımına uğradı (search provider timed out)", code="search_failed") from None
        except httpx.HTTPError as exc:
            raise ToolError(f"arama sağlayıcısına ulaşılamadı (search provider unreachable: {type(exc).__name__})", code="search_failed") from None
        except (ValueError, KeyError, TypeError, AttributeError):  # JSON that is not what the provider documents
            raise ToolError("arama sağlayıcısı beklenmeyen bir yanıt verdi (unexpected response)", code="search_failed") from None
        return [_clean(r) for r in results[:n]]


def _clean(r: Result) -> Result:
    return {
        "title": " ".join(str(r.get("title", "")).split())[:200],
        "url": str(r.get("url", ""))[:500],
        "snippet": " ".join(str(r.get("snippet", "")).split())[:400],
    }


def _check(resp: httpx.Response, name: str) -> None:
    if resp.status_code == 429:
        raise ToolError(f"{name}: rate limited, try again later", code="search_failed")
    if resp.status_code in (401, 403):
        raise ToolError(f"{name}: API key rejected (HTTP {resp.status_code})", code="search_failed")
    if resp.status_code >= 400:
        raise ToolError(f"{name}: HTTP {resp.status_code}", code="search_failed")


_DDG_RESULT = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'(?P<rest>.*?)(?=<a[^>]+class="[^"]*result__a|$)',
    re.S,
)
_DDG_SNIPPET = re.compile(r'class="[^"]*result__snippet[^"]*"[^>]*>(?P<s>.*?)</(?:a|div|td)>', re.S)
_TAG = re.compile(r"<[^>]+>")


def _strip(s: str) -> str:
    return html.unescape(_TAG.sub("", s))


def _ddg_url(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.netloc.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


@register_provider("duckduckgo")
async def duckduckgo(client: SearchClient, query: str, n: int) -> list[Result]:
    resp = await client.http.post("https://html.duckduckgo.com/html/", data={"q": query})
    _check(resp, "duckduckgo")
    if resp.status_code == 202 or ("result__a" not in resp.text and "No results" not in resp.text):
        # DuckDuckGo answers automated traffic with a challenge page (HTTP 202) from some networks.
        raise ToolError("duckduckgo refused the automated request (bot protection). Set SEARCH_PROVIDER to "
                        "tavily, brave or searxng for reliable search.", code="search_unavailable")
    out: list[Result] = []
    for m in _DDG_RESULT.finditer(resp.text):
        url = _ddg_url(m.group("href"))
        if "duckduckgo.com/y.js" in url:  # ads
            continue
        sm = _DDG_SNIPPET.search(m.group("rest"))
        out.append({"title": _strip(m.group("title")), "url": url, "snippet": _strip(sm.group("s")) if sm else ""})
        if len(out) >= n:
            break
    return out


@register_provider("tavily")
async def tavily(client: SearchClient, query: str, n: int) -> list[Result]:
    key = client.settings.tavily_api_key
    if not key:
        raise ToolError("SEARCH_PROVIDER=tavily needs TAVILY_API_KEY", code="config")
    resp = await client.http.post("https://api.tavily.com/search",
                                  json={"api_key": key, "query": query, "max_results": n})
    _check(resp, "tavily")
    return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
            for r in (resp.json().get("results") or [])]


@register_provider("brave")
async def brave(client: SearchClient, query: str, n: int) -> list[Result]:
    key = client.settings.brave_api_key
    if not key:
        raise ToolError("SEARCH_PROVIDER=brave needs BRAVE_API_KEY", code="config")
    resp = await client.http.get("https://api.search.brave.com/res/v1/web/search",
                                 params={"q": query, "count": n},
                                 headers={"X-Subscription-Token": key, "Accept": "application/json"})
    _check(resp, "brave")
    web = (resp.json().get("web") or {}).get("results") or []
    return [{"title": r.get("title"), "url": r.get("url"), "snippet": _strip(r.get("description") or "")} for r in web]


@register_provider("searxng")
async def searxng(client: SearchClient, query: str, n: int) -> list[Result]:
    base = (client.settings.searxng_url or "").rstrip("/")
    if not base:
        raise ToolError("SEARCH_PROVIDER=searxng needs SEARXNG_URL", code="config")
    try:
        resp = await client.searx_http.get(f"{base}/search", params={"q": query, "format": "json"})
    except httpx.ConnectError as exc:
        if "blocked by network policy" in str(exc):
            raise ToolError("searxng: SEARXNG_URL blocked by the network policy", code="blocked") from None
        raise ToolError("SearXNG sunucusuna bağlanılamadı: adresi, DNS'i ve güvenlik duvarını kontrol edin "
                        "(searxng: could not connect)", code="search_failed") from None
    if 300 <= resp.status_code < 400:
        raise ToolError("searxng: redirects are not followed; use the final URL in SEARXNG_URL", code="search_failed")
    if resp.status_code == 403:
        raise ToolError("SearXNG erişimi reddetti (HTTP 403): JSON biçimi kapalı olabilir. settings.yml içinde "
                        "search.formats listesine json ekleyin (searxng: HTTP 403, json format disabled?)", code="search_failed")
    if resp.status_code == 429:
        raise ToolError("SearXNG istek sınırına takıldı (HTTP 429): limiter bu sunucuyu bot sayıyor; kendi sunucunuzda "
                        "limiter'ı kapatın (searxng: rate limited)", code="search_failed")
    _check(resp, "searxng")
    try:
        data = resp.json()
    except ValueError:
        raise ToolError("SearXNG JSON döndürmedi: settings.yml içinde search.formats listesine json ekleyin ve "
                        "yeniden başlatın (searxng: response is not JSON)", code="search_failed") from None
    return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
            for r in (data.get("results") or []) if isinstance(r, dict)]


@register_provider("serper")
async def serper(client: SearchClient, query: str, n: int) -> list[Result]:
    key = client.settings.serper_api_key
    if not key:
        raise ToolError("SEARCH_PROVIDER=serper needs SERPER_API_KEY", code="config")
    resp = await client.http.post("https://google.serper.dev/search", json={"q": query, "num": n},
                                  headers={"X-API-KEY": key, "Content-Type": "application/json"})
    _check(resp, "serper")
    return [{"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet")}
            for r in (resp.json().get("organic") or []) if isinstance(r, dict)]


PARALLEL_MCP = "https://search.parallel.ai/mcp"  # free without an account or key (anonymous rate limit)
_MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def _mcp_body(resp: httpx.Response) -> dict:
    """A JSON-RPC reply that may come as plain JSON or as one SSE ``data:`` event."""
    text = resp.text
    if "data:" in text and not text.lstrip().startswith("{"):
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
                break
    data = __import__("json").loads(text)
    if isinstance(data, dict) and data.get("error"):
        raise ToolError("parallel: " + str((data["error"] or {}).get("message", "error"))[:120], code="search_failed")
    return data


@register_provider("parallel")
async def parallel(client: SearchClient, query: str, n: int) -> list[Result]:
    """Parallel's hosted Search MCP (Streamable HTTP): initialize -> tools/call web_search. No key, no account."""
    sid = getattr(client, "_parallel_sid", None)
    if sid is None:
        init = await client.http.post(PARALLEL_MCP, headers=_MCP_HEADERS, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "km-ai", "version": "1"}}})
        _check(init, "parallel")
        sid = init.headers.get("mcp-session-id")
        headers = {**_MCP_HEADERS, **({"Mcp-Session-Id": sid} if sid else {})}
        await client.http.post(PARALLEL_MCP, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        client._parallel_sid = sid or ""
    headers = {**_MCP_HEADERS, **({"Mcp-Session-Id": client._parallel_sid} if client._parallel_sid else {})}
    resp = await client.http.post(PARALLEL_MCP, headers=headers, timeout=25.0, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "web_search", "arguments": {"objective": query[:300], "search_queries": [query[:120]]}}})
    if resp.status_code in (400, 404, 410):  # session expired: start over once next time
        client._parallel_sid = None
    _check(resp, "parallel")
    body = _mcp_body(resp)
    content = ((body.get("result") or {}).get("content") or [])
    text = next((c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"), "")
    results = (__import__("json").loads(text).get("results") or []) if text else []
    out: list[Result] = []
    for r in results:
        if isinstance(r, dict) and r.get("url"):
            ex = r.get("excerpts")
            out.append({"title": r.get("title"), "url": r.get("url"),
                        "snippet": (ex[0] if isinstance(ex, list) and ex else "")})
    return out


# --------------------------------------------------------------------------- auto mode
# Free channels in turn; a channel that fails is skipped (and remembered for a while).

_FALLBACK_INSTANCES = ["https://searx.be", "https://search.inetol.net", "https://searx.tiekoetter.com",
                       "https://priv.au", "https://search.rhscz.eu"]  # used only when searx.space is unreachable
_COOLDOWN_SECONDS = 600.0
_INSTANCE_COOLDOWN_SECONDS = 900.0
_MAX_PUBLIC_TRIES = 8
_PUBLIC_BUDGET_SECONDS = 20.0  # all public instances together: a tool call must not hang


def _instance_ok(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.port not in (None, 443) or parts.username or parts.password:
        return False
    if host.endswith((".onion", ".i2p", ".local", ".internal")) or re.fullmatch(r"[\d.:]+", host):
        return False  # no tor/i2p/private names and no IP literals
    return True


async def public_instances(client: SearchClient) -> list[str]:
    """Good public SearXNG instances from searx.space (cached 6 h; built-in fallback list if unreachable)."""
    now = time.time()
    if client._instances and now - client._instances[0] < 6 * 3600:
        return client._instances[1]
    urls: list[str] = []
    try:
        resp = await client.http.get("https://searx.space/data/instances.json", timeout=8.0)
        data = resp.json() if resp.status_code == 200 else {}
        scored: list[tuple[float, str]] = []
        for url, info in (data.get("instances") or {}).items():
            if not isinstance(info, dict) or not _instance_ok(str(url)):
                continue
            http_i, timing = info.get("http") or {}, (info.get("timing") or {}).get("search") or {}
            if http_i.get("status_code") != 200 or str(http_i.get("grade", "")).upper()[:1] not in ("A", "B"):
                continue
            ok = timing.get("success_percentage")
            med = ((timing.get("all") or {}).get("median"))
            if not isinstance(ok, (int, float)) or ok < 90 or not isinstance(med, (int, float)):
                continue
            scored.append((float(med), str(url).rstrip("/")))
        urls = [u for _, u in sorted(scored)[:16]]
        random.shuffle(urls)  # spread the load over the good ones instead of hammering the fastest
    except (httpx.HTTPError, ValueError, AttributeError):
        urls = []
    if not urls:
        urls = list(_FALLBACK_INSTANCES)
    else:
        urls += [u for u in _FALLBACK_INSTANCES if u not in urls]  # known-good last resort after the scored ones
    client._instances = (now, urls)
    return urls


_PUBLIC_MAX_BYTES = 400_000   # a listed instance is untrusted: never read more than this
_PUBLIC_TOTAL_SECONDS = 6.0   # ... nor for longer than this in total (httpx timeouts are per read)


async def _read_capped(http: httpx.AsyncClient, url: str, params: dict) -> tuple[int, bytes]:
    async def go() -> tuple[int, bytes]:
        async with http.stream("GET", url, params=params) as resp:
            if resp.status_code != 200:
                return resp.status_code, b""
            chunks, size = [], 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > _PUBLIC_MAX_BYTES:
                    raise ToolError("search instance answered with too much data", code="search_failed")
                chunks.append(chunk)
            return 200, b"".join(chunks)
    try:
        return await asyncio.wait_for(go(), _PUBLIC_TOTAL_SECONDS)
    except asyncio.TimeoutError:
        raise ToolError("search instance too slow", code="search_failed") from None


async def _public_instance(client: SearchClient, base: str, query: str) -> list[Result]:
    status, body = await _read_capped(client.public_http, f"{base}/search", {"q": query, "format": "json"})
    if status != 200:  # 429 limiter / 403 json disabled: next instance
        raise ToolError(f"{base}: HTTP {status}", code="search_failed")
    data = __import__("json").loads(body)
    results = [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
               for r in (data.get("results") or []) if isinstance(r, dict)]
    if not results:
        raise ToolError(f"{base}: no results", code="search_failed")
    return results


def _channels(client: SearchClient) -> list[tuple[str, ProviderFn]]:
    """Order: what the user configured (own SearXNG, then keyed APIs), then the keyless free channels."""
    st = client.settings
    chain: list[tuple[str, ProviderFn]] = []
    if st.searxng_url:
        chain.append(("searxng", PROVIDERS["searxng"]))
    for name, key in (("tavily", st.tavily_api_key), ("brave", st.brave_api_key), ("serper", st.serper_api_key)):
        if key:
            chain.append((name, PROVIDERS[name]))
    chain.append(("parallel", PROVIDERS["parallel"]))   # keyless MCP endpoint, real web results
    chain.append(("duckduckgo", PROVIDERS["duckduckgo"]))
    return chain


@register_provider("auto")
async def auto(client: SearchClient, query: str, n: int) -> list[Result]:
    """Try every free channel in order, skip failures, remember what worked. Never raises per channel."""
    failures = 0

    def cooling(key: str) -> bool:
        return client.cooldown.get(key, 0.0) > time.time()

    def fail(key: str, seconds: float) -> None:
        client.cooldown[key] = time.time() + seconds

    for ignore_cooldown in (False, True):  # second pass: everything was cooling down, try anyway once
        for name, fn in _channels(client):
            if cooling(name) and not ignore_cooldown:
                continue
            try:
                results = await fn(client, query, n)
            except (ToolError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                fail(name, _COOLDOWN_SECONDS)
                failures += 1
                continue
            if results:
                client.last_via = name
                return results
            fail(name, _COOLDOWN_SECONDS)
        tries, started = 0, time.monotonic()
        for base in await public_instances(client):
            if tries >= _MAX_PUBLIC_TRIES or time.monotonic() - started > _PUBLIC_BUDGET_SECONDS:
                break
            key = "searxng:" + base
            if cooling(key) and not ignore_cooldown:
                continue
            tries += 1
            try:
                results = await _public_instance(client, base, query)
            except (ToolError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                fail(key, _INSTANCE_COOLDOWN_SECONDS)
                failures += 1
                continue
            client.last_via = urlsplit(base).hostname or base
            return results
        if failures:
            break  # something was really tried and failed: do not loop the same channels again
    raise ToolError("none of the free search channels answered; set a Tavily or Brave key for reliable search",
                    code="search_unavailable")
