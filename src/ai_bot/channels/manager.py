"""ChannelManager: owns the channels, their inbound router, background tasks and the chat-id lookup.

Created by ``ai_bot.setup`` and exposed as ``ai.channels``. ``main.build_app`` mounts ``manager.router``
(the ``/channels/*`` webhooks; they answer 404 while the channel is disabled) and binds the settings and
secret stores so panel values apply live. Pollers (e-mail) start on app startup; ``aclose`` stops them and
cancels every task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
from typing import TYPE_CHECKING, Any, Awaitable

import httpx
from fastapi import APIRouter, FastAPI

from . import schema
from .base import Channel, ChannelError, chat_id_for, is_channel_chat
from .config import ChannelConfig
from .email_channel import Email
from .slack import Slack
from .teams import Teams
from .whatsapp import WhatsApp

if TYPE_CHECKING:
    from ..core import AiBot

log = logging.getLogger("ai_bot.channels")


class ChannelManager:
    def __init__(self, ai: "AiBot", *, store: Any = None, secrets: Any = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.ai = ai
        self.config = ChannelConfig(store, secrets)
        self.secret_names = set(schema.SECRET_NAMES)
        self.transport = transport  # tests inject httpx.MockTransport; None = real network
        self.channels: dict[str, Channel] = {}
        for cls in (WhatsApp, Teams, Slack, Email):
            self.channels[cls.name] = cls(self)
        self.router = APIRouter()
        for ch in self.channels.values():
            ch.register(self.router)
        self._tasks: set[asyncio.Task] = set()
        self._ctx: dict[int, tuple[str, dict]] = {}
        self._index_key: tuple | None = None
        self._index: dict[int, tuple[Channel, str]] = {}
        self._refs_lock = threading.Lock()
        self.started = False

    # ------------------------------------------------------------------ wiring
    def bind(self, store: Any, secrets: Any) -> None:
        """Use the panel's stores (panel > env > default). Safe to call more than once."""
        self.config.bind(store, secrets)

    def mount(self, app: FastAPI) -> None:
        app.include_router(self.router)

    async def start(self) -> None:
        self.started = True
        self.channels["email"].start()  # idles until enabled+configured (settings are read on every cycle)

    async def aclose(self) -> None:
        email = self.channels["email"]
        await email.stop()
        for ch in self.channels.values():
            for pend in list(ch.pending.values()):
                if not pend.future.done():
                    pend.future.cancel()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def spawn(self, coro: Awaitable) -> None:
        task = asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def idle(self, timeout: float = 5.0) -> None:
        """Wait until every background task finished (tests)."""
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)

    # ------------------------------------------------------------------ chat ids
    def _build_index(self) -> dict[int, tuple[Channel, str]]:
        key = tuple((n, tuple(ch.allowed())) for n, ch in self.channels.items() if ch.enabled())
        if key != self._index_key:
            self._index = {chat_id_for(n, ext): (self.channels[n], ext) for n, allowed in key for ext in allowed}
            self._index_key = key
        return self._index

    @staticmethod
    def handles(chat_id: Any) -> bool:
        """True for ids in the channel range (whether or not the sender is still allowed)."""
        return is_channel_chat(chat_id)

    def lookup(self, chat_id: Any) -> tuple[Channel, str] | None:
        """(channel, external id) of a channel chat id, only while the sender is in the allow-list of an
        ENABLED channel."""
        if not is_channel_chat(chat_id):
            return None
        return self._build_index().get(int(chat_id))

    def owns(self, chat_id: Any) -> bool:
        return self.lookup(chat_id) is not None

    def remember(self, chat_id: int, channel: str, sender: str, ctx: dict) -> None:
        self._ctx.pop(chat_id, None)
        self._ctx[chat_id] = (sender, ctx)
        while len(self._ctx) > 500:
            self._ctx.pop(next(iter(self._ctx)))

    def context(self, chat_id: int) -> tuple[str, dict] | None:
        hit = self.lookup(chat_id)
        if hit is None:
            return None
        known = self._ctx.get(chat_id)
        if known is not None:
            return known
        ctx = hit[0].target_for(chat_id, hit[1])
        return (hit[1], ctx) if ctx else None

    def confirmer_for(self, chat_id: int):
        hit = self.lookup(chat_id)
        if hit is None or not hit[0].can_confirm:
            return None
        return hit[0].confirm

    def hidden_tools_for(self, chat_id: int) -> frozenset[str]:
        hit = self.lookup(chat_id)
        return hit[0].hidden_tools if hit else frozenset()

    def tainted_for(self, chat_id: int) -> bool:
        hit = self.lookup(chat_id)
        return bool(hit and hit[0].tainted)

    async def deliver(self, chat_id: int, text: str) -> bool:
        """Send a message the bot starts itself (reminders, scheduled results) to a channel user.
        ``True`` only when the provider accepted it."""
        hit = self.lookup(chat_id)
        if hit is None:
            log.info("no active channel for chat %s; message dropped", chat_id)
            return False
        ch, external = hit
        ctx = ch.target_for(chat_id, external)
        if ctx is None:
            ch.note_error(f"{ch.name}_no_target")
            return False
        try:
            await ch.send_text(ctx, text)
        except ChannelError:
            return False  # already recorded as the channel's last error
        return True

    # ------------------------------------------------------------------ Teams conversation references
    def _refs_path(self):
        return self.ai.settings.data_dir / "channel-refs.json"

    def _read_refs(self) -> dict:
        try:
            data = json.loads(self._refs_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_ref(self, channel: str, external: str, ref: dict) -> None:
        """Remember where a user can be reached (Teams needs a conversation reference to message first).
        Holds ids and a service URL only, no secrets; the URL is validated again before every send."""
        key = f"{channel}:{external}"
        with self._refs_lock:
            data = self._read_refs()
            if data.get(key) == ref:
                return
            data[key] = ref
            path = self._refs_path()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(prefix=".channel-refs.", dir=path.parent)
                try:
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(data, fh)
                    os.replace(tmp, path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except OSError:
                log.warning("could not store the %s conversation reference", channel)

    def load_ref(self, channel: str, external: str) -> dict | None:
        with self._refs_lock:
            ref = self._read_refs().get(f"{channel}:{external}")
        return ref if isinstance(ref, dict) else None

    # ------------------------------------------------------------------ panel
    def describe(self) -> list[dict]:
        return [ch.view() for ch in self.channels.values()]
