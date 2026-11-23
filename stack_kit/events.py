"""Events: in-memory ring buffer + signed outgoing webhooks (``WEBHOOKS_OUT``).

Outgoing request (protocol v1)::

    POST <url>
    Content-Type: application/json
    X-Stack-Event: order.opened
    X-Stack-Timestamp: 1763571234            (unix seconds)
    X-Stack-Signature: sha256=<hex hmac_sha256(secret, timestamp + "." + body)>

    {"id": 7, "event": "order.opened", "ts": 1763571234.12, "stack": "mt5", "data": {...}}

Receivers verify with :func:`verify_signature`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic_core import to_jsonable_python

from .redact import redact, register_secret

log = logging.getLogger("stack_kit.events")

DEFAULT_TOLERANCE = 300  # seconds


# --------------------------------------------------------------------------- signatures


def _as_bytes(body: bytes | str) -> bytes:
    return body.encode() if isinstance(body, str) else body


def sign(secret: str, timestamp: str | int, body: bytes | str) -> str:
    """Return ``sha256=<hex>`` = HMAC-SHA256(secret, f"{timestamp}." + body)."""
    mac = hmac.new(secret.encode(), str(timestamp).encode() + b"." + _as_bytes(body), hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify_signature(
    secret: str,
    body: bytes | str,
    timestamp: str | int | None,
    signature: str | None,
    *,
    tolerance: int = DEFAULT_TOLERANCE,
    now: float | None = None,
) -> bool:
    """Verify an outgoing stack_kit webhook (``X-Stack-Timestamp`` + ``X-Stack-Signature``).

    Returns ``False`` (never raises) for a missing/garbled header, a timestamp outside
    ``tolerance`` seconds (replay protection; ``tolerance=0`` disables the check) or a
    wrong signature. Comparison is constant-time. ``body`` must be the raw request bytes.
    """
    if not secret or not signature or timestamp is None:
        return False
    try:
        ts = int(str(timestamp).strip())
    except ValueError:
        return False
    current = time.time() if now is None else now
    if tolerance and abs(current - ts) > tolerance:
        return False
    expected = sign(secret, ts, body)
    return hmac.compare_digest(expected.encode(), signature.strip().encode())


def hook_signature(secret: str, body: bytes | str) -> str:
    """Signature expected by ``POST /hook/{name}`` in ``X-Signature``: ``sha256=<hex hmac(secret, body)>``."""
    return "sha256=" + hmac.new(secret.encode(), _as_bytes(body), hashlib.sha256).hexdigest()


def verify_hook_signature(secret: str, body: bytes | str, header: str | None) -> bool:
    """Constant-time check of an ``X-Signature`` header. Accepts ``sha256=<hex>`` or bare hex."""
    if not secret or not header:
        return False
    value = header.strip()
    if not value.startswith("sha256="):
        value = "sha256=" + value
    return hmac.compare_digest(hook_signature(secret, body).encode(), value.lower().encode())


# --------------------------------------------------------------------------- event log


class EventLog:
    """Thread-safe ring buffer. Ids start at 1 and only grow within a process."""

    def __init__(self, maxlen: int = 500):
        self._items: deque[dict] = deque(maxlen=maxlen)
        self._next = 1
        self._lock = threading.Lock()

    def append(self, event: str, data: Any, stack: str) -> dict:
        with self._lock:
            record = {
                "id": self._next,
                "event": event,
                "ts": round(time.time(), 3),
                "stack": stack,
                "data": to_jsonable_python(data, fallback=str),
            }
            self._next += 1
            self._items.append(record)
            return record

    def since(self, since: int = 0, limit: int = 100) -> list[dict]:
        with self._lock:
            return [r for r in self._items if r["id"] > since][: max(0, limit)]

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._next - 1


# --------------------------------------------------------------------------- outgoing webhooks


@dataclass(frozen=True)
class WebhookTarget:
    url: str
    secret: str

    def safe_url(self) -> str:
        """URL without credentials, path or query (for logs and ``doctor``)."""
        parts = urlsplit(self.url)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{host}{port}/…"


def parse_webhooks_out(value: str | None) -> list[WebhookTarget]:
    """Parse ``WEBHOOKS_OUT`` = ``url|secret,url2|secret2``. A target without a secret is rejected."""
    targets: list[WebhookTarget] = []
    for raw in (value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        if "|" not in item:
            raise ValueError("WEBHOOKS_OUT entries must look like url|secret")
        url, secret = item.rsplit("|", 1)
        url, secret = url.strip(), secret.strip()
        if not url.startswith(("http://", "https://")) or not secret:
            raise ValueError("WEBHOOKS_OUT entries must look like http(s)://host/path|secret")
        register_secret(secret)
        targets.append(WebhookTarget(url, secret))
    return targets


def webhooks_out_from_env(environ: dict[str, str] | None = None) -> list[WebhookTarget]:
    """``WEBHOOKS_OUT`` plus the split form ``WEBHOOKS_OUT_URL`` + ``WEBHOOKS_OUT_SECRET`` and
    ``WEBHOOKS_OUT_URL_<N>`` + ``WEBHOOKS_OUT_SECRET_<N>`` (N = 2..9), appended in that order.
    The split form lets Komuta inject the secret with ``valueFrom.secret`` (no interpolation)."""
    import os

    env = os.environ if environ is None else environ
    targets = parse_webhooks_out(env.get("WEBHOOKS_OUT"))
    for suffix in [""] + [f"_{n}" for n in range(2, 10)]:
        url = (env.get(f"WEBHOOKS_OUT_URL{suffix}") or "").strip()
        sec = (env.get(f"WEBHOOKS_OUT_SECRET{suffix}") or "").strip()
        if not url and not sec:
            continue
        if not url or not sec:
            raise ValueError(f"WEBHOOKS_OUT_URL{suffix} and WEBHOOKS_OUT_SECRET{suffix} must be set together")
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"WEBHOOKS_OUT_URL{suffix} must start with http:// or https://")
        register_secret(sec)
        targets.append(WebhookTarget(url, sec))
    # the same target from both forms (e.g. an old entrypoint that also builds WEBHOOKS_OUT) is sent once
    return list(dict.fromkeys(targets))


class WebhookDispatcher:
    """Bounded queue + background worker delivering events to every target.

    * ``enqueue`` is safe from any thread (sync tools run in a worker thread).
    * Up to ``max_attempts`` tries per target, exponential backoff
      ``backoff_base * 2**(attempt-1)`` capped at ``backoff_max``.
    * Retried: network errors, 408, 429 and 5xx. Other 4xx are final.
    * Queue full -> the new event is dropped and counted in ``stats["dropped"]``.
    """

    def __init__(
        self,
        targets: list[WebhookTarget] | None = None,
        *,
        queue_size: int = 1000,
        max_attempts: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        timeout: float = 10.0,
        concurrency: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
        drain_timeout: float = 5.0,
    ):
        self.targets = list(targets or [])
        self.queue_size = queue_size
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.timeout = timeout
        self.concurrency = concurrency
        self.transport = transport
        self.drain_timeout = drain_timeout
        self.stats = {"queued": 0, "delivered": 0, "failed": 0, "dropped": 0}
        self._pending: deque[dict] = deque()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()
        self._client: httpx.AsyncClient | None = None

    # -- producer side
    def enqueue(self, record: dict) -> bool:
        if not self.targets:
            return False
        loop = self._loop
        if loop is None or loop.is_closed():
            with self._lock:
                if len(self._pending) >= self.queue_size:
                    self._drop(record)
                    return False
                self._pending.append(record)
                self.stats["queued"] += 1
            return True
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            return self._put(record)
        loop.call_soon_threadsafe(self._put, record)
        return True

    def _put(self, record: dict) -> bool:
        assert self._queue is not None
        if self._queue.full():
            self._drop(record)
            return False
        self._queue.put_nowait(record)
        self.stats["queued"] += 1
        return True

    def _drop(self, record: dict) -> None:
        self.stats["dropped"] += 1
        log.warning("webhook queue full; dropped event %s #%s", record.get("event"), record.get("id"))

    # -- lifecycle
    async def start(self) -> None:
        if self._worker is not None or not self.targets:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self.queue_size)
        self._client = httpx.AsyncClient(timeout=self.timeout, transport=self.transport)
        with self._lock:
            while self._pending:
                self._queue.put_nowait(self._pending.popleft())
        self._worker = asyncio.create_task(self._run(), name="stack_kit.webhooks")

    async def drain(self, timeout: float = 10.0) -> None:
        """Wait until the queue is empty and in-flight deliveries finished (used by tests/shutdown)."""
        if self._queue is None:
            return
        deadline = time.monotonic() + timeout
        while (not self._queue.empty() or self._inflight) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

    async def stop(self, drain_timeout: float | None = None) -> None:
        """Wait up to ``drain_timeout`` (default ``self.drain_timeout``, 5 s) for queued and in-flight
        deliveries, then cancel the rest. Undelivered events are counted in ``stats["dropped"]`` and logged."""
        if self._worker is None:
            return
        await self.drain(self.drain_timeout if drain_timeout is None else drain_timeout)
        assert self._queue is not None
        left = self._queue.qsize() + len(self._inflight)
        if left:
            self.stats["dropped"] += left
            log.warning("shutdown: %d webhook deliver%s not finished and dropped",
                        left, "y" if left == 1 else "ies")
        self._worker.cancel()
        for task in list(self._inflight):
            task.cancel()
        await asyncio.gather(self._worker, *self._inflight, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
        self._worker = None
        self._loop = None
        self._queue = None
        self._client = None

    # -- consumer side
    async def _run(self) -> None:
        assert self._queue is not None
        sem = asyncio.Semaphore(self.concurrency)
        while True:
            record = await self._queue.get()
            body = json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode()
            for target in self.targets:
                await sem.acquire()
                task = asyncio.create_task(self._deliver(target, record["event"], body))
                self._inflight.add(task)

                def _done(t: asyncio.Task, _sem: asyncio.Semaphore = sem) -> None:
                    self._inflight.discard(t)
                    _sem.release()

                task.add_done_callback(_done)
            self._queue.task_done()

    async def _deliver(self, target: WebhookTarget, event: str, body: bytes) -> bool:
        assert self._client is not None
        last = ""
        for attempt in range(1, self.max_attempts + 1):
            ts = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "stack-kit-webhook/1",
                "X-Stack-Event": event,
                "X-Stack-Timestamp": ts,
                "X-Stack-Signature": sign(target.secret, ts, body),
            }
            retry = True
            try:
                resp = await self._client.post(target.url, content=body, headers=headers)
                if 200 <= resp.status_code < 300:
                    self.stats["delivered"] += 1
                    return True
                last = f"HTTP {resp.status_code}"
                retry = resp.status_code in (408, 429) or resp.status_code >= 500
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # httpx errors, InvalidURL, ... : count and retry, never kill the task
                last = type(exc).__name__
            if not retry or attempt == self.max_attempts:
                break
            await asyncio.sleep(min(self.backoff_base * 2 ** (attempt - 1), self.backoff_max))
        self.stats["failed"] += 1
        log.warning("webhook to %s failed for %s after %d attempt(s): %s",
                    target.safe_url(), event, attempt, redact(last))
        return False
