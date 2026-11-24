"""SSRF-safe HTTP fetching for ``web.read``.

Rules (do not loosen them; see docs/SECURITY.md):

* Only ``http``/``https`` URLs, no credentials in the URL, only allowed ports (default 80/443/8080/8443).
* The host name is resolved first; **every** resolved address must be public
  (``ipaddress.is_global`` and not multicast/reserved; IPv4-mapped IPv6 is unwrapped).
  Loopback, private (10/8, 172.16/12, 192.168/16), link-local (169.254/16 incl. cloud metadata),
  CGNAT (100.64/10), ULA (fc00::/7), ``::1``, ``0.0.0.0`` ... are refused.
* The connection is **pinned** to the checked address (the request goes to the IP, ``Host`` header
  and TLS SNI carry the name), so a second DNS answer cannot swap in a private address
  (DNS rebinding).
* Redirects are followed manually (at most ``max_redirects``) and every hop is checked again.
* Body size and total time are capped.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

Resolver = Callable[[str, int], Awaitable[list[str]]]

BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal",
                     "metadata.goog", "instance-data", "instance-data.ec2.internal"}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")


class FetchBlocked(Exception):
    """The URL was refused by the SSRF policy (message is safe to show)."""


class FetchError(Exception):
    """The fetch failed for a network/size/time reason (message is safe to show)."""


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    # IPv4 first: many pods have no IPv6 route, and the connection is pinned to one checked address
    return sorted({info[4][0] for info in infos}, key=lambda a: (":" in a, a))


def ip_is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip.teredo is not None:
            return False
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_link_local or ip.is_private:
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return bool(ip.is_global)


class SafeFetcher:
    def __init__(self, *, timeout: float = 15.0, max_bytes: int = 2_000_000, max_redirects: int = 5,
                 allowed_ports: list[int] | None = None, resolver: Resolver | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 user_agent: str = "Mozilla/5.0 (compatible; km-ai/1.0)"):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.allowed_ports = set(allowed_ports or [80, 443, 8080, 8443])
        self.resolver = resolver or system_resolver
        self.user_agent = user_agent
        # never let httpx follow redirects or read proxy env vars: every hop is checked here
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False,
                                         trust_env=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check_url(self, url: str) -> tuple[httpx.URL, str]:
        """Validate ``url``; return the parsed URL and the public IP to connect to."""
        parsed, addresses = await self.check_url_all(url)
        return parsed, addresses[0]

    async def check_url_all(self, url: str) -> tuple[httpx.URL, list[str]]:
        """Validate ``url``; return the parsed URL and every checked public address (IPv4 first)."""
        try:
            parts = urlsplit(url)
        except ValueError:
            raise FetchBlocked("invalid URL") from None
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise FetchBlocked("only http and https URLs are allowed")
        if parts.username or parts.password:
            raise FetchBlocked("credentials in URLs are not allowed")
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise FetchBlocked("URL has no host")
        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError:
            raise FetchBlocked("invalid port") from None
        if port not in self.allowed_ports:
            raise FetchBlocked(f"port {port} is not allowed")
        if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
            raise FetchBlocked("this host is not allowed")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [str(literal)]
        else:
            try:
                addresses = await asyncio.wait_for(self.resolver(host, port), timeout=self.timeout)
            except (OSError, asyncio.TimeoutError):
                raise FetchError("could not resolve host") from None
        if not addresses:
            raise FetchError("could not resolve host")
        for addr in addresses:
            if not ip_is_public(addr):
                raise FetchBlocked("host resolves to a private, loopback or link-local address")
        try:
            parsed = httpx.URL(url)
        except Exception:
            raise FetchBlocked("invalid URL") from None
        return parsed, sorted(addresses, key=lambda a: ":" in a)

    async def fetch(self, url: str) -> FetchResult:
        try:
            return await asyncio.wait_for(self._fetch(url), timeout=self.timeout)
        except asyncio.TimeoutError:
            raise FetchError("page took too long to load") from None

    async def _fetch(self, url: str) -> FetchResult:
        current = url
        for _hop in range(self.max_redirects + 1):
            parsed, ips = await self.check_url_all(current)
            for k, ip in enumerate(ips[:4]):  # a pod without IPv6 falls through to the next checked address
                try:
                    resp_status, headers, body, truncated = await self._request_pinned(parsed, ip)
                    break
                except FetchError as exc:
                    if "could not connect" not in str(exc) or k == len(ips[:4]) - 1:
                        raise
            if resp_status in (301, 302, 303, 307, 308) and headers.get("location"):
                current = urljoin(str(parsed), headers["location"])
                continue
            return FetchResult(url=str(parsed), status=resp_status,
                               content_type=headers.get("content-type", ""), body=body, truncated=truncated)
        raise FetchError("too many redirects")

    async def _request_pinned(self, parsed: httpx.URL, ip: str) -> tuple[int, httpx.Headers, bytes, bool]:
        host = parsed.host
        target = parsed.copy_with(host=ip)  # pinned: connect to the checked address
        default_port = 443 if parsed.scheme == "https" else 80
        host_header = host if parsed.port in (None, default_port) else f"{host}:{parsed.port}"
        request = self._client.build_request(
            "GET", target,
            headers={"Host": host_header, "User-Agent": self.user_agent,
                     "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.5"},
            extensions={"sni_hostname": host},
        )
        try:
            resp = await self._client.send(request, stream=True)
        except httpx.TimeoutException:
            raise FetchError("page took too long to load") from None
        except httpx.HTTPError as exc:
            raise FetchError(f"could not connect ({type(exc).__name__})") from None
        try:
            chunks: list[bytes] = []
            size = 0
            truncated = False
            if resp.status_code not in (301, 302, 303, 307, 308):
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        chunks.append(chunk[: len(chunk) - (size - self.max_bytes)])
                        truncated = True
                        break
                    chunks.append(chunk)
            return resp.status_code, resp.headers, b"".join(chunks), truncated
        finally:
            await resp.aclose()
