"""The ``AiBot`` container: settings, storage, clients and services wired together."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from stack_kit import Bridges, Stack, ToolError, register_secret
from stack_kit.telegram import TelegramBot

from .agent import Agent
from .config import Settings
from .db import Database
from .llm import LLMClient, LLMError
from .memory import MemoryService
from .providers import make_client
from .safefetch import Resolver, SafeFetcher
from .scheduler import Scheduler
from .search import SearchClient

if TYPE_CHECKING:
    from .channels.manager import ChannelManager

log = logging.getLogger("ai_bot")

# Chat id of the messaging-channel turn the current task is running (set by run_ai). Only inside such a turn
# may chat-scoped tools address that channel user; bridge/MCP/hook callers and ai.ask never see this set.
CHANNEL_TURN: contextvars.ContextVar[int | None] = contextvars.ContextVar("ai_bot_channel_turn", default=None)
MAX_CONFIRM_PREVIEW = 3000  # Telegram confirmation shows everything or the action is refused


class AiBot:
    def __init__(self, stack: Stack, settings: Settings, *, bot: TelegramBot | None = None,
                 bridges: Bridges | None = None, db: Database | None = None,
                 llm_transport: httpx.AsyncBaseTransport | None = None,
                 web_transport: httpx.AsyncBaseTransport | None = None,
                 search_transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Resolver | None = None):
        self.stack = stack
        self.settings = settings
        self.bot = bot
        self.bridges = bridges
        self.db = db or Database(settings.db_path)
        self.llm_transport = llm_transport
        self.resolver = resolver  # also used by the network policy of LLM / SearXNG requests
        self._closers: set[asyncio.Task] = set()
        self.llm = self._make_llm()
        self.telegram: TelegramRunner | None = None  # set by setup() when ai-bot owns the bot
        self.seen_chats: dict[int, dict] = {}  # recent Telegram chats (admin UI: chat id capture)
        self.channels: "ChannelManager | None" = None  # WhatsApp/Teams/Slack/e-mail; set by setup()
        self.started = time.time()
        self.fetcher = SafeFetcher(timeout=settings.web_read_timeout, max_bytes=settings.web_read_max_bytes,
                                   max_redirects=settings.web_read_max_redirects,
                                   allowed_ports=settings.web_read_ports, resolver=resolver, transport=web_transport)
        self.search = SearchClient(settings, transport=search_transport, resolver=resolver)
        self.memory = MemoryService(self.db, settings)
        self.scheduler = Scheduler(self)
        self.agent = Agent(self)
        self._chat_locks: dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------ live configuration
    def _make_llm(self) -> LLMClient:
        s = self.settings
        return make_client(s.llm_provider, s.llm_base_url, s.llm_api_key, timeout=s.llm_timeout,
                           retries=s.llm_retries, transport=self.llm_transport,
                           allow_private_custom=s.llm_allow_private, resolver=self.resolver)

    def rebuild_llm(self) -> None:
        """Swap in a client built from the current settings (base URL, key, timeout, retries).
        Turns already running keep the old client; it is closed a little later."""
        old, self.llm = self.llm, self._make_llm()

        async def _close_later() -> None:
            await asyncio.sleep(max(self.settings.turn_timeout, 30) + 10)
            try:
                await old.aclose()
            except Exception:
                pass

        try:
            task = asyncio.get_running_loop().create_task(_close_later())
            self._closers.add(task)
            task.add_done_callback(self._closers.discard)
        except RuntimeError:  # no loop (sync caller): nothing is in flight
            pass

    def note_update(self, update: dict) -> None:
        """Remember which chats wrote to the bot (id, type, name, last seen) — never the text."""
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return
        name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
        entry = self.seen_chats.pop(chat_id, {"count": 0})
        entry.update({"chat_id": chat_id, "type": str(chat.get("type") or ""), "name": str(name or "")[:80],
                      "username": str(chat.get("username") or "")[:64], "last_seen": time.time(),
                      "count": entry.get("count", 0) + 1})
        self.seen_chats[chat_id] = entry
        while len(self.seen_chats) > 20:
            self.seen_chats.pop(next(iter(self.seen_chats)))

    # ------------------------------------------------------------------ helpers
    @property
    def has_telegram(self) -> bool:
        return self.bot is not None and bool(self.bot.token) and self.settings.telegram_enabled

    def resolve_chat(self, chat_id: int | None) -> int:
        """Chat for chat-scoped tools. Only ALLOWED_CHAT_IDS; an empty list allows nothing
        (bridge/MCP callers cannot make the bot write to or message an arbitrary chat).
        Messaging-channel ids (WhatsApp/Teams/Slack/e-mail, see channels/base.py) are accepted only inside the
        turn that channel user started (CHANNEL_TURN) and only while the sender is in the allow-list of an
        enabled channel; bridge/MCP/hook/ai.ask callers get the Telegram allow-list behaviour."""
        if (self.channels is not None and chat_id is not None and CHANNEL_TURN.get() == chat_id
                and self.channels.owns(chat_id)):
            return int(chat_id)
        allowed = self.settings.allowed_chat_ids
        if not allowed:
            raise ToolError("ALLOWED_CHAT_IDS is empty: chat-scoped tools are disabled", code="forbidden")
        if chat_id is None:
            chat_id = allowed[0]
        if int(chat_id) not in allowed:
            raise ToolError("chat_id is not in ALLOWED_CHAT_IDS", code="forbidden")
        return int(chat_id)

    def lock_for(self, chat_id: int) -> asyncio.Lock:
        return self._chat_locks.setdefault(chat_id, asyncio.Lock())

    async def confirm_in_telegram(self, chat_id: int, tool_name: str, preview: Any) -> bool:
        """Human confirmation with inline buttons. Without a Telegram bot: always False."""
        if self.bot is None or not self.bot.token:
            return False
        body = preview if isinstance(preview, str) else json.dumps(preview, ensure_ascii=False, indent=2, default=str)
        if len(body) > MAX_CONFIRM_PREVIEW:
            # never ask about something that is shown cut off: refuse (nothing is executed)
            try:
                await self.bot.send(chat_id, f"{tool_name}: işlemin ayrıntısı çok uzun, güvenle gösterilemedi; "
                                             "işlem yapılmadı. / Details too long to show safely; refused.")
            except Exception:
                pass
            return False
        return await self.bot.ask_confirm(
            chat_id, f"AI bu işlemi yapmak istiyor / The AI wants to run: {tool_name}\n\n{body}\n\n"
                     "Onaylıyor musun? / Confirm?")

    async def deliver(self, chat_id: int, text: str) -> bool:
        """Send ``text`` to a chat. ``True`` when it was handed to Telegram / the channel, ``False`` when dropped."""
        if self.channels is not None and self.channels.handles(chat_id):
            return await self.channels.deliver(chat_id, text)
        if self.bot is None or not self.bot.token or not self.settings.telegram_enabled:
            log.info("Telegram is not configured or switched off; message for chat %s dropped", chat_id)
            return False
        await self.bot.send(chat_id, text)
        return True

    async def run_ai(self, chat_id: int, text: str, *, scheduled: bool = False) -> str:
        """Run one AI turn. ``scheduled`` turns do not touch the chat history."""
        token = None
        try:
            async with self.lock_for(chat_id):
                confirmer = self.confirm_in_telegram if self.has_telegram else None  # no human -> refuse
                tainted = False
                hide: frozenset[str] = frozenset()
                channel: str | None = "telegram"  # which per-channel instruction applies to this turn
                if self.channels is not None and self.channels.handles(chat_id):
                    hit = self.channels.lookup(chat_id)
                    channel = hit[0].name if hit else None
                    # channel users confirm by one-time code on their own channel; e-mail has no confirmer,
                    # its turns carry outside content (tainted) and cannot fetch URLs (no exfiltration channel)
                    confirmer = self.channels.confirmer_for(chat_id)
                    tainted = self.channels.tainted_for(chat_id)
                    hide = self.channels.hidden_tools_for(chat_id)
                    token = CHANNEL_TURN.set(chat_id)
                answer = await self.agent.run(chat_id, text, confirmer=confirmer, save_history=not scheduled,
                                              scheduled=scheduled, tainted=tainted, hide=hide, channel=channel)
        except LLMError as exc:
            log.warning("AI turn failed: %s", exc.message)
            return f"AI servisine ulaşılamadı / AI service error: {exc.message}"
        finally:
            if token is not None:
                CHANNEL_TURN.reset(token)
        self.stack.emit("ai.reply", {"chat_id": chat_id, "scheduled": scheduled, "prompt": text[:2000],
                                     "answer": answer[:4000]})
        return answer

    async def aclose(self) -> None:
        await self.scheduler.drain()
        if self.channels is not None:  # e-mail poller + inbound tasks
            try:
                await self.channels.aclose()
            except Exception:
                pass
        for task in list(self._closers):
            task.cancel()
        for turn in list(getattr(self, "panel_turns", {}).values()):  # panel chat turns still running
            if turn.task is not None and not turn.task.done():
                turn.task.cancel()
        for closer in (self.llm.aclose, self.fetcher.aclose, self.search.aclose):
            try:
                await closer()
            except Exception:
                pass
        if self.bridges is not None:
            await self.bridges.aclose()


class TelegramRunner:
    """Owns the polling task of a ``TelegramBot`` so the token can change while the service runs."""

    def __init__(self, bot: TelegramBot):
        self.bot = bot
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # stop/start/restart never interleave (two saves, two tabs)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done() and self.bot.running

    async def start(self) -> bool:
        async with self._lock:
            return self._start()

    def _start(self) -> bool:
        if not self.bot.token:
            return False
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.bot.run())
        return True

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def _stop(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._task = None  # cleared only after the old poller is gone
        self.bot.running = False

    async def restart(self, token: str | None = None) -> bool:
        """Stop polling, switch to ``token`` (if given) and start again (no-op without a token)."""
        async with self._lock:
            await self._stop()
            if token is not None:
                register_secret(token)
                self.bot.token = token
            return self._start()
