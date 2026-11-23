"""Outbound network policy for admin-configurable endpoints (LLM base URL, model lists, SearXNG).

The panel lets a signed-in admin type URLs; without a policy that is a server-side request forgery
(SSRF) tool. Rules (see docs/GUVENLIK.md "Ağ politikası"):

* only ``http``/``https``, no credentials in the URL, no ``/ui``, ``/bridge``, ``/mcp``, ``/hook`` paths;
* the host is resolved and **every** address is checked; the connection is then **pinned** to the
  checked address (``Host`` header + TLS SNI keep the name), so DNS rebinding cannot swap it;
* always refused: link-local (169.254.0.0/16, fe80::/10, incl. cloud metadata 169.254.169.254 and
  fd00:ec2::254), ``0.0.0.0`` / ``::``, multicast, reserved, cloud metadata host names, and this
  service's own listener (``HTTP_PORT`` on a local/private address);
* ``allow_private=False`` (cloud/custom default): only public addresses;
  ``allow_private=True`` (Ollama, LM Studio, vLLM, SearXNG, or a custom URL the admin explicitly
  marked as private network): loopback and private ranges are allowed too;
* redirects are never followed (clients are built with ``follow_redirects=False``).

Fixed cloud presets (OpenRouter, OpenAI, Anthropic, ...) are locked to their known hosts by
:mod:`ai_bot.providers`; requests to those hosts skip the DNS step (public, fixed names).
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from stack_kit import env_int

from .safefetch import Resolver, system_resolver

METADATA_HOSTS = {"metadata", "metadata.google.internal", "metadata.goog", "instance-data",
                  "instance-data.ec2.internal", "metadata.azure.com", "169.254.169.254", "fd00:ec2::254"}
METADATA_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"),
                ipaddress.ip_address("100.100.100.200")}  # alibaba metadata sits in CGNAT space
BLOCKED_FIRST_SEGMENTS = {"ui", "bridge", "mcp", "hook"}


class NetBlocked(Exception):
    """The URL is refused by the policy. ``str(exc)`` is safe to show."""


def _unwrap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
    return ip


def address_problem(addr: str, *, allow_private: bool) -> str | None:
    """Why ``addr`` may not be contacted (``None`` = allowed)."""
    try:
        ip = _unwrap(ipaddress.ip_address(addr.split("%", 1)[0]))
    except ValueError:
        return "geçersiz adres"
    if ip in METADATA_IPS:
        return "bulut metadata adresi engelli"
    if ip.is_unspecified:
        return "0.0.0.0 / :: engelli"
    if ip.is_link_local:
        return "link-local adres (169.254.0.0/16, fe80::/10) engelli"
    if isinstance(ip, ipaddress.IPv6Address) and ip.teredo is not None:
        return "teredo adresi engelli"
    local = ip.is_loopback or ip.is_private or (
        isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    if ip.is_multicast or (ip.is_reserved and not ip.is_loopback):
        return "ayrılmış/çoklu yayın adresi engelli"
    if local and not allow_private:
        return "özel ağ / loopback adresi bu sağlayıcı için kapalı"
    if not local and not ip.is_global:
        return "genel olmayan adres engelli"
    return None


def _is_local(addr: str) -> bool:
    try:
        ip = _unwrap(ipaddress.ip_address(addr.split("%", 1)[0]))
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_unspecified


@dataclass
class NetPolicy:
    allow_private: bool = False
    resolver: Resolver | None = None
    own_port: int | None = None
    trusted_hosts: set[str] = field(default_factory=set)  # fixed cloud preset hosts: no DNS step
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if self.own_port is None:
            self.own_port = env_int("HTTP_PORT", 8080)

    def check_static(self, url: str) -> tuple[str, int, str]:
        """Syntax checks only. Returns ``(scheme, port, host)``."""
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise NetBlocked("geçersiz adres") from None
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise NetBlocked("yalnız http:// veya https:// adresleri")
        if parts.username or parts.password:
            raise NetBlocked("adreste kullanıcı adı/parola olamaz")
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise NetBlocked("adreste host yok")
        if host in METADATA_HOSTS:
            raise NetBlocked("bulut metadata adresi engelli")
        segments = [s for s in (parts.path or "").split("/") if s]
        if segments and segments[0].lower() in BLOCKED_FIRST_SEGMENTS:
            raise NetBlocked("panel/köprü yolları (/ui, /bridge, /mcp, /hook) hedef olamaz")
        return scheme, port or (443 if scheme == "https" else 80), host

    async def check(self, url: str) -> tuple[str, str | None]:
        """Full check. Returns ``(host, pinned_ip)``; ``pinned_ip`` is ``None`` for trusted hosts."""
        host, addresses = await self.check_all(url)
        return host, (addresses[0] if addresses else None)

    async def check_all(self, url: str) -> tuple[str, list[str]]:
        """Like :meth:`check` but returns every checked address, IPv4 first (empty for trusted hosts)."""
        scheme, port, host = self.check_static(url)
        if host in self.trusted_hosts:
            return host, []
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [str(literal)]
        else:
            try:
                addresses = await asyncio.wait_for((self.resolver or system_resolver)(host, port), timeout=self.timeout)
            except (OSError, asyncio.TimeoutError):
                raise NetBlocked("host çözümlenemedi") from None
        if not addresses:
            raise NetBlocked("host çözümlenemedi")
        for addr in addresses:
            problem = address_problem(addr, allow_private=self.allow_private)
            if problem:
                raise NetBlocked(problem)
            if port == self.own_port and _is_local(addr):
                raise NetBlocked("bu servisin kendi portu hedef olamaz")
        return host, sorted(addresses, key=lambda a: ":" in a)  # stable: IPv4 before IPv6


class GuardedTransport(httpx.AsyncBaseTransport):
    """Checks every request against a :class:`NetPolicy` and pins it to the checked IP."""

    def __init__(self, policy: NetPolicy, inner: httpx.AsyncBaseTransport | None = None):
        self.policy = policy
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            host, addresses = await self.policy.check_all(str(request.url))
        except NetBlocked as exc:
            raise httpx.ConnectError(f"blocked by network policy: {exc}", request=request) from None
        if not addresses:  # trusted host: no pinning
            return await self.inner.handle_async_request(request)
        original = request.url
        default_port = 443 if original.scheme == "https" else 80
        last: Exception | None = None
        for ip in addresses[:4]:  # every address was checked; a pod without IPv6 simply falls through to IPv4
            if ip == host:
                return await self.inner.handle_async_request(request)
            request.headers["Host"] = host if original.port in (None, default_port) else f"{host}:{original.port}"
            request.extensions = {**request.extensions, "sni_hostname": host}
            request.url = original.copy_with(host=ip)
            try:
                return await self.inner.handle_async_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last = exc
        raise last if last is not None else httpx.ConnectError("no address to connect to", request=request)

    async def aclose(self) -> None:
        await self.inner.aclose()
