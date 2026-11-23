"""Single-use confirmation ids bound to (tool name, argument digest)."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass

from .errors import ErrorCode

CONFIRM_TTL = 120  # seconds, fixed by protocol v1


def args_digest(tool_name: str, args: dict) -> str:
    """Stable SHA-256 over the tool name and canonical JSON of the (validated) arguments.

    Callers pass the python-mode dump, so values that the JSON dump would merge (``inf`` and
    ``nan`` both become ``null``) stay distinct (``Infinity`` vs ``NaN``).
    """
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=repr)
    return hashlib.sha256(f"{tool_name}\n{canonical}".encode()).hexdigest()


@dataclass
class _Pending:
    tool: str
    digest: str
    expires_at: float


class ConfirmStore:
    """In-memory store. Every ``consume`` attempt invalidates the id, even a failed one."""

    def __init__(self, ttl: float = CONFIRM_TTL, max_items: int = 1000, clock=time.monotonic):
        self.ttl = ttl
        self.max_items = max_items
        self._clock = clock
        self._items: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def issue(self, tool_name: str, args: dict) -> str:
        now = self._clock()
        confirm_id = "cf_" + secrets.token_urlsafe(18)
        with self._lock:
            self._purge(now)
            if len(self._items) >= self.max_items:
                oldest = min(self._items, key=lambda k: self._items[k].expires_at)
                del self._items[oldest]
            self._items[confirm_id] = _Pending(tool_name, args_digest(tool_name, args), now + self.ttl)
        return confirm_id

    def consume(self, confirm_id: str, tool_name: str, args: dict) -> str | None:
        """Return ``None`` on success, otherwise an error code (``confirm_*``)."""
        now = self._clock()
        with self._lock:
            pending = self._items.pop(confirm_id, None) if isinstance(confirm_id, str) else None
        if pending is None:
            return ErrorCode.CONFIRM_UNKNOWN
        if now > pending.expires_at:
            return ErrorCode.CONFIRM_EXPIRED
        if pending.tool != tool_name or not hmac.compare_digest(pending.digest, args_digest(tool_name, args)):
            return ErrorCode.CONFIRM_MISMATCH
        return None

    def __len__(self) -> int:
        return len(self._items)

    def _purge(self, now: float) -> None:
        for key in [k for k, v in self._items.items() if now > v.expires_at]:
            del self._items[key]


IDEMPOTENCY_TTL = 600  # seconds


@dataclass
class _IdemEntry:
    tool: str
    digest: str
    expires_at: float
    result: dict | None = None  # None while the call is still running


class IdempotencyStore:
    """Remembers outcomes of ``trade``/``admin`` calls by ``idempotency_key`` for ``ttl`` seconds.

    A repeated key with the same tool + argument digest returns the stored outcome (or an
    ``in_progress`` state) instead of running the tool again.
    """

    def __init__(self, ttl: float = IDEMPOTENCY_TTL, max_items: int = 1000, clock=time.monotonic):
        self.ttl = ttl
        self.max_items = max_items
        self._clock = clock
        self._items: dict[str, _IdemEntry] = {}
        self._lock = threading.Lock()

    def begin(self, key: str, tool_name: str, digest: str) -> tuple[str, dict | None]:
        """Return ``("new", None)``, ``("done", result)``, ``("running", None)`` or ``("conflict", None)``."""
        now = self._clock()
        with self._lock:
            for k in [k for k, v in self._items.items() if now > v.expires_at]:
                del self._items[k]
            entry = self._items.get(key)
            if entry is not None:
                if entry.tool != tool_name or not hmac.compare_digest(entry.digest, digest):
                    return "conflict", None
                return ("done", entry.result) if entry.result is not None else ("running", None)
            if len(self._items) >= self.max_items:
                oldest = min(self._items, key=lambda k: self._items[k].expires_at)
                del self._items[oldest]
            self._items[key] = _IdemEntry(tool_name, digest, now + self.ttl)
            return "new", None

    def finish(self, key: str, result: dict) -> None:
        with self._lock:
            entry = self._items.get(key)
            if entry is not None:
                entry.result = result

    def forget(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)
