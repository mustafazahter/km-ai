"""Live channel configuration: panel value > environment variable > default.

The channels read their settings through this object instead of ``Settings`` fields, so a value saved
in the panel applies without a restart and without touching ``config.py``. Secrets come from the
secret store (or the environment) and are never returned by any API.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from stack_kit import env_str, register_secret

from . import schema


def split_list(value: Any) -> list[str]:
    """``"a, b ,,a"`` -> ``["a", "b"]`` (trimmed, no empties, no duplicates, order kept)."""
    if isinstance(value, (list, tuple)):
        items = [str(x) for x in value]
    else:
        items = str(value or "").split(",")
    out: list[str] = []
    for item in items:
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return out


class ChannelConfig:
    """Snapshot reader over a ``SettingsStore`` + ``SecretStore`` (both optional: env + defaults only)."""

    ttl = 1.5  # seconds a snapshot is reused (hot paths read several values per request)

    def __init__(self, store: Any = None, secrets: Any = None):
        self.store = store
        self.secrets = secrets
        self._specs = {s.name: s for s in schema.SCHEMA}
        self._snap: dict[str, Any] | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    def bind(self, store: Any, secrets: Any) -> None:
        self.store, self.secrets = store, secrets
        self.invalidate()

    @property
    def bound(self) -> bool:
        return self.store is not None

    def invalidate(self) -> None:
        with self._lock:
            self._snap = None

    def _load(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, spec in self._specs.items():
            if self.store is not None:
                out[name] = self.store.resolve(name)["value"]
                continue
            raw = env_str(name)
            value = spec.default
            if raw is not None:
                try:
                    value = spec.coerce(raw)
                except ValueError:
                    pass
            out[name] = value
        for name in schema.SECRET_NAMES:
            value = None
            if self.secrets is not None:
                value = self.secrets.get(name)
            value = value or env_str(name)
            if value:
                register_secret(value)
            out[name] = value or None
        return out

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if self._snap is not None and now - self._at < self.ttl:
                return self._snap
        snap = self._load()
        with self._lock:
            self._snap, self._at = snap, now
        return snap

    # -- typed accessors
    def get(self, name: str) -> Any:
        return self.snapshot().get(name)

    def text(self, name: str) -> str:
        value = self.snapshot().get(name)
        return str(value).strip() if value not in (None, False) else ""

    def flag(self, name: str) -> bool:
        return bool(self.snapshot().get(name))

    def number(self, name: str, default: int = 0) -> int:
        try:
            return int(self.snapshot().get(name))
        except (TypeError, ValueError):
            return default

    def items(self, name: str) -> list[str]:
        return split_list(self.snapshot().get(name))

    def secret(self, name: str) -> str:
        return str(self.snapshot().get(name) or "")

    def source(self, name: str) -> str:
        """``ui`` / ``env`` / ``default`` / ``none`` (for the panel)."""
        if name in schema.SECRET_NAMES:
            if self.secrets is not None and self.secrets.has(name):
                return "ui"
            return "env" if env_str(name) else "none"
        return self.store.resolve(name)["source"] if self.store is not None else "default"
