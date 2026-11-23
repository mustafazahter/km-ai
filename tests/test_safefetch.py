import asyncio

import httpx
import pytest

from ai_bot.safefetch import FetchBlocked, FetchError, SafeFetcher, ip_is_public
from tests.fakes import static_resolver

PUBLIC = "93.184.216.34"


def fetcher(handler=None, mapping=None, **kw) -> SafeFetcher:
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(200, text="ok")))
    return SafeFetcher(resolver=static_resolver(mapping or {}), transport=transport, **kw)


@pytest.mark.parametrize("addr", [
    "127.0.0.1", "127.8.9.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "0.0.0.0",
    "100.64.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "224.0.0.1",
    "2002:7f00:1::1",  # 6to4 wrapping 127.0.0.1
])
def test_private_addresses_are_not_public(addr):
    assert ip_is_public(addr) is False


def test_public_address_is_public():
    assert ip_is_public(PUBLIC) and ip_is_public("2606:4700:4700::1111")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.1.2.3/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://localhost/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://printer.local/",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/",
    "http://user:pass@example.com/",
    "http://example.com:22/",
    "javascript:alert(1)",
])
def test_blocked_urls(url):
    calls = []
    f = fetcher(lambda r: calls.append(r) or httpx.Response(200), mapping={"example.com": [PUBLIC]})
    with pytest.raises(FetchBlocked):
        asyncio.run(f.fetch(url))
    assert calls == []  # nothing was ever requested


def test_dns_name_resolving_to_private_ip_is_blocked():
    calls = []
    f = fetcher(lambda r: calls.append(r) or httpx.Response(200),
                mapping={"evil.example": ["10.0.0.7"], "mixed.example": [PUBLIC, "127.0.0.1"]})
    with pytest.raises(FetchBlocked):
        asyncio.run(f.fetch("http://evil.example/"))
    with pytest.raises(FetchBlocked):  # any private answer blocks (round-robin rebinding)
        asyncio.run(f.fetch("http://mixed.example/"))
    assert calls == []


def test_redirect_to_private_ip_is_blocked():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    f = fetcher(handler, mapping={"example.com": [PUBLIC]})
    with pytest.raises(FetchBlocked):
        asyncio.run(f.fetch("http://example.com/start"))
    assert len(seen) == 1  # the private hop was never requested


def test_redirect_to_name_resolving_private_is_blocked():
    def handler(request):
        return httpx.Response(301, headers={"location": "https://internal.example/admin"})

    f = fetcher(handler, mapping={"example.com": [PUBLIC], "internal.example": ["192.168.0.10"]})
    with pytest.raises(FetchBlocked):
        asyncio.run(f.fetch("http://example.com/"))


def test_public_fetch_is_pinned_to_checked_ip():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hello</p>")

    f = fetcher(handler, mapping={"example.com": [PUBLIC]})
    res = asyncio.run(f.fetch("https://example.com/page?q=1"))
    assert res.status == 200 and res.body == b"<p>hello</p>"
    req = seen[0]
    assert req.url.host == PUBLIC  # connection goes to the checked IP
    assert req.headers["host"] == "example.com"
    assert req.extensions.get("sni_hostname") == "example.com"
    assert res.url == "https://example.com/page?q=1"


def test_public_redirect_is_followed_and_rechecked():
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "https://other.example/b"})
        return httpx.Response(200, text="done")

    f = fetcher(handler, mapping={"example.com": [PUBLIC], "other.example": ["1.1.1.1"]})
    res = asyncio.run(f.fetch("http://example.com/a"))
    assert res.body == b"done" and res.url == "https://other.example/b"


def test_too_many_redirects():
    f = fetcher(lambda r: httpx.Response(302, headers={"location": "http://example.com/loop"}),
                mapping={"example.com": [PUBLIC]}, max_redirects=2)
    with pytest.raises(FetchError):
        asyncio.run(f.fetch("http://example.com/"))


def test_size_limit_truncates():
    f = fetcher(lambda r: httpx.Response(200, content=b"x" * 5000), mapping={"example.com": [PUBLIC]},
                max_bytes=1000)
    res = asyncio.run(f.fetch("http://example.com/"))
    assert len(res.body) == 1000 and res.truncated


def test_unresolvable_host():
    with pytest.raises(FetchError):
        asyncio.run(fetcher().fetch("http://nope.example/"))


def test_total_timeout():
    async def slow_resolver(host, port):
        await asyncio.sleep(5)
        return [PUBLIC]

    f = SafeFetcher(resolver=slow_resolver, timeout=0.2,
                    transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(FetchError):
        asyncio.run(f.fetch("http://slow.example/"))
