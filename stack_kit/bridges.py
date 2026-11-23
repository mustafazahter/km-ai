"""Client for other stacks' bridges (and for the local registry).

``BRIDGES=mt5=http://mt5-trader:8080|<token>,me=local``
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from .errors import BridgeError, ErrorCode, error_body
from .redact import redact, register_secret
from .registry import Stack, get_stack
from .settings import env_str

log = logging.getLogger("stack_kit.bridges")

MANIFEST_TTL = 60.0
NEGATIVE_TTL = 15.0  # seconds a failed manifest fetch is remembered
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass
class Bridge:
    name: str
    url: str | None = None  # None means local
    token: str | None = None
    _manifest: dict | None = field(default=None, repr=False)
    _fetched_at: float = field(default=0.0, repr=False)
    _error: "BridgeError | None" = field(default=None, repr=False)
    _error_at: float = field(default=0.0, repr=False)

    @property
    def is_local(self) -> bool:
        return self.url is None

    def __repr__(self) -> str:  # never show the token
        return f"Bridge(name={self.name!r}, url={'local' if self.is_local else self.url!r})"


def parse_bridges(value: str | None) -> list[Bridge]:
    """Parse ``ad=url|token,ad2=local``. Raises :class:`BridgeError` on malformed entries."""
    bridges: list[Bridge] = []
    seen: set[str] = set()
    for raw in (value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        if "=" not in item:
            raise BridgeError("BRIDGES entries must look like name=url|token or name=local")
        name, target = (part.strip() for part in item.split("=", 1))
        if not _NAME_RE.match(name):
            raise BridgeError(f"invalid bridge name {name!r}")
        if name in seen:
            raise BridgeError(f"bridge {name!r} is defined twice")
        seen.add(name)
        if target.lower() == "local" or target.lower().startswith("local|"):
            bridges.append(Bridge(name))
            continue
        url, _, token = target.partition("|")
        url, token = url.strip().rstrip("/"), token.strip()
        if not url.startswith(("http://", "https://")):
            raise BridgeError(f"bridge {name!r}: url must start with http:// or https://")
        if not token:
            raise BridgeError(f"bridge {name!r}: token missing (name=url|token)")
        register_secret(token)
        bridges.append(Bridge(name, url, token))
    return bridges


_PART_URL_RE = re.compile(r"^BRIDGE_([A-Z0-9][A-Z0-9_]*)_URL$")


def bridges_from_env(environ: dict[str, str] | None = None) -> list[Bridge]:
    """``BRIDGES`` plus the split form ``BRIDGE_<NAME>_URL`` + ``BRIDGE_<NAME>_TOKEN``.

    The split form exists because Komuta does not interpolate env values: a URL from
    ``valueFrom.service`` and a token from ``valueFrom.secret`` cannot be joined into one
    ``BRIDGES`` string. ``<NAME>`` is upper case (``BRIDGE_MT5_URL`` -> bridge ``mt5``);
    ``BRIDGE_<NAME>_URL=local`` means the local registry. When both forms define the same
    name, the split form wins.
    """
    env = os.environ if environ is None else environ
    raw = env.get("BRIDGES")
    bridges = {b.name: b for b in parse_bridges(raw.strip() if raw and raw.strip() else None)}
    for key in sorted(env):
        m = _PART_URL_RE.match(key)
        if not m:
            continue
        url = (env.get(key) or "").strip()
        if not url:
            continue
        name = m.group(1).lower()
        if url.lower() == "local":
            bridges[name] = Bridge(name)
            continue
        token = (env.get(f"BRIDGE_{m.group(1)}_TOKEN") or "").strip()
        url = url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise BridgeError(f"{key} must start with http:// or https:// (or be 'local')")
        if not token:
            raise BridgeError(f"{key} is set but BRIDGE_{m.group(1)}_TOKEN is missing")
        register_secret(token)
        bridges[name] = Bridge(name, url, token)
    return list(bridges.values())


class Bridges:
    """Discover and call tools across bridges.

    Args:
        bridges: parsed bridge list (see :func:`parse_bridges`).
        stack: stack used for ``local`` bridges (default: current stack).
        timeout: HTTP timeout in seconds for remote calls.
        transport: optional ``httpx`` transport (tests use ``httpx.ASGITransport``).
        manifest_ttl: seconds a remote manifest is cached (protocol: 60).
        negative_ttl: seconds a failed manifest fetch is cached, so a dead bridge does not
            slow down every call (default 15; ``0`` disables).
    """

    def __init__(self, bridges: list[Bridge] | None = None, *, stack: Stack | None = None,
                 timeout: float = 30.0, transport: httpx.AsyncBaseTransport | None = None,
                 manifest_ttl: float = MANIFEST_TTL, negative_ttl: float = NEGATIVE_TTL):
        self.bridges = list(bridges or [])
        self._stack = stack
        self.timeout = timeout
        self.manifest_ttl = manifest_ttl
        self.negative_ttl = negative_ttl
        self.errors: dict[str, dict] = {}  # last discovery error per unreachable bridge
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    @classmethod
    def from_env(cls, var: str = "BRIDGES", **kwargs: Any) -> "Bridges":
        """Build from ``BRIDGES`` + ``BRIDGE_<NAME>_URL``/``_TOKEN`` (see :func:`bridges_from_env`).
        With a custom ``var`` only that variable is parsed."""
        if var == "BRIDGES":
            return cls(bridges_from_env(), **kwargs)
        return cls(parse_bridges(env_str(var)), **kwargs)

    @property
    def stack(self) -> Stack:
        return self._stack or get_stack()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "Bridges":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def invalidate(self) -> None:
        """Drop cached manifests and cached failures."""
        for b in self.bridges:
            b._manifest, b._fetched_at = None, 0.0
            b._error, b._error_at = None, 0.0

    # -- discovery
    async def manifest(self, bridge: Bridge, *, refresh: bool = False) -> dict:
        if bridge.is_local:
            return self.stack.manifest()
        now = time.monotonic()
        if not refresh and bridge._manifest is not None and now - bridge._fetched_at < self.manifest_ttl:
            return bridge._manifest
        # failures are cached even on refresh, otherwise a dead bridge would stall every call
        if bridge._error is not None and now - bridge._error_at < self.negative_ttl:
            raise BridgeError(bridge._error.message, bridge._error.code)
        try:
            resp = await self._client.get(f"{bridge.url}/bridge/manifest", headers=self._headers(bridge))
            if resp.status_code != 200:
                raise BridgeError(f"bridge {bridge.name}: manifest returned HTTP {resp.status_code}",
                                  ErrorCode.REMOTE_ERROR)
            data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
                raise BridgeError(f"bridge {bridge.name}: invalid manifest", ErrorCode.REMOTE_ERROR)
        except BridgeError as exc:
            err = exc
        except httpx.TimeoutException:
            err = BridgeError(f"bridge {bridge.name}: timeout", ErrorCode.TIMEOUT)
        except (httpx.HTTPError, ValueError) as exc:
            err = BridgeError(f"bridge {bridge.name}: {type(exc).__name__}", ErrorCode.UNREACHABLE)
        else:
            bridge._manifest, bridge._fetched_at = data, now
            bridge._error = None
            return data
        bridge._error, bridge._error_at = err, time.monotonic()
        raise BridgeError(err.message, err.code)

    async def _collect(self, refresh: bool = False) -> list[tuple[Bridge, dict]]:
        async def one(b: Bridge) -> list[tuple[Bridge, dict]] | None:
            try:
                m = await self.manifest(b, refresh=refresh)
            except BridgeError as exc:
                self.errors[b.name] = {"code": exc.code, "message": redact(exc.message)}
                return None
            self.errors.pop(b.name, None)
            return [(b, t) for t in m.get("tools", []) if isinstance(t, dict) and isinstance(t.get("name"), str)]

        results = await asyncio.gather(*(one(b) for b in self.bridges))
        entries: list[tuple[Bridge, dict]] = []
        owner: dict[str, str] = {}
        for res in results:
            for b, t in res or []:
                if t["name"] in owner and owner[t["name"]] != b.name:
                    raise BridgeError(
                        f"tool name conflict: {t['name']!r} is offered by bridges "
                        f"{owner[t['name']]!r} and {b.name!r}; remove one from BRIDGES",
                        ErrorCode.NAME_CONFLICT,
                    )
                if t["name"] in owner:
                    continue
                owner[t["name"]] = b.name
                entries.append((b, t))
        return entries

    async def tools(self, *, refresh: bool = False) -> list[dict]:
        """All tools of all reachable bridges, each with an extra ``bridge`` field.

        Unreachable bridges are skipped and reported in :attr:`errors`.
        Raises :class:`BridgeError` (code ``name_conflict``) if two bridges offer the same tool name.
        """
        return [{**t, "bridge": b.name} for b, t in await self._collect(refresh)]

    # -- calling
    def _known_owner(self, name: str) -> Bridge | None:
        """Owner of ``name`` from local registries and fresh cached manifests only (no network)."""
        now = time.monotonic()
        owners = []
        for b in self.bridges:
            if b.is_local:
                names = set(self.stack.tools)
            elif b._manifest is not None and now - b._fetched_at < self.manifest_ttl:
                names = {t.get("name") for t in b._manifest.get("tools", []) if isinstance(t, dict)}
            else:
                continue
            if name in names:
                owners.append(b)
        if len(owners) > 1:
            raise BridgeError(f"tool name conflict: {name!r} is offered by bridges "
                              f"{owners[0].name!r} and {owners[1].name!r}; remove one from BRIDGES",
                              ErrorCode.NAME_CONFLICT)
        return owners[0] if owners else None

    async def call(self, name: str, args: dict | None = None, *, confirm_id: str | None = None,
                   confirmed: bool = False, idempotency_key: str | None = None) -> dict:
        """Call a tool wherever it lives. Returns a protocol v1 result dict; never raises for
        remote/network problems (they become ``{ok:false, error:{code: timeout|unreachable|remote_error}}``).
        Raises :class:`BridgeError` only for a tool name conflict.

        If the owner is already known (local registry or a fresh cached manifest) the call goes
        there directly without waiting for other bridges.
        """
        target = self._known_owner(name)
        if target is None:
            entries = await self._collect()
            target = next((b for b, t in entries if t["name"] == name), None)
        if target is None:
            # the manifest may be stale: refresh once before giving up
            entries = await self._collect(refresh=True)
            target = next((b for b, t in entries if t["name"] == name), None)
        if target is None:
            return error_body(ErrorCode.NOT_FOUND, f"no bridge offers tool {name}"[:200])
        if target.is_local:
            return await self.stack.call(name, args or {}, confirm_id=confirm_id, confirmed=confirmed,
                                         idempotency_key=idempotency_key)
        payload: dict[str, Any] = {"args": args or {}}
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        if confirmed:
            payload["confirmed"] = True
        if confirm_id:
            payload["confirm_id"] = confirm_id
        try:
            resp = await self._client.post(f"{target.url}/bridge/call/{quote(name, safe='.')}",
                                           json=payload, headers=self._headers(target))
        except httpx.TimeoutException:
            return error_body(ErrorCode.TIMEOUT, f"bridge {target.name}: timeout")
        except httpx.HTTPError as exc:
            return error_body(ErrorCode.UNREACHABLE, f"bridge {target.name}: {type(exc).__name__}")
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and ("ok" in data):
            if resp.status_code == 404 and (data.get("error") or {}).get("code") == ErrorCode.NOT_FOUND:
                target._manifest = None
            return data
        return error_body(ErrorCode.REMOTE_ERROR, f"bridge {target.name}: HTTP {resp.status_code}")

    @staticmethod
    def _headers(bridge: Bridge) -> dict[str, str]:
        return {"Authorization": f"Bearer {bridge.token}"} if bridge.token else {}
