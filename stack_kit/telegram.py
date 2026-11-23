"""Telegram long-polling bot on top of httpx (no telegram library needed).

* ``ALLOWED_CHAT_IDS`` allow-list. Empty list -> only ``/id`` and ``/start`` answer.
* ``@command("name", help="...")`` routes ``/name args``; ``/help`` and ``/id`` are automatic.
* :meth:`TelegramBot.ask_confirm` shows inline Yes/No buttons and returns ``True``/``False``.
* :meth:`TelegramBot.run_tool` runs a tool and handles ``needs_confirmation`` with buttons.
* Network drops, 409 (another poller/webhook) and 429 (flood) are retried with backoff.
* The bot token never reaches logs or error texts.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import secrets as _secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from .errors import ToolError
from .redact import install_log_redaction, redact, register_secret
from .registry import Stack, _current, _maybe_await, get_stack
from .settings import env_list, secret

log = logging.getLogger("stack_kit.telegram")

MAX_MESSAGE = 4096
CALLBACK_PREFIX = "skc"


class TelegramAPIError(Exception):
    def __init__(self, method: str, code: int, description: str, retry_after: int | None = None):
        super().__init__(f"{method}: {code} {description}")
        self.method = method
        self.code = code
        self.description = description
        self.retry_after = retry_after


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` characters, preferring line breaks."""
    text = text if text else " "
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n") if text[cut:cut + 1] == "\n" else text[cut:]
    if text:
        chunks.append(text)
    return chunks


def parse_chat_ids(values: list[str] | None) -> set[int]:
    ids: set[int] = set()
    for v in values or []:
        try:
            ids.add(int(str(v).strip()))
        except ValueError:
            raise ValueError("ALLOWED_CHAT_IDS must be a comma separated list of integers") from None
    return ids


@dataclass
class CommandContext:
    """Passed to ``@command`` handlers and ``on_message`` handlers."""

    bot: "TelegramBot"
    chat_id: int
    user_id: int | None
    text: str
    command: str | None
    args: list[str]
    message: dict = field(repr=False, default_factory=dict)

    @property
    def arg_text(self) -> str:
        """Everything after the command, unchanged."""
        if self.command is None:
            return self.text
        parts = self.text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    async def reply(self, text: str, **kwargs: Any) -> list[dict]:
        return await self.bot.send(self.chat_id, text, **kwargs)

    async def confirm(self, text: str, timeout: float | None = None) -> bool:
        """Only the user who sent the command can press the buttons."""
        return await self.bot.ask_confirm(self.chat_id, text, timeout=timeout, user_id=self.user_id)

    async def run_tool(self, name: str, args: dict | None = None, via: Any = None) -> dict:
        """Run a tool; a confirmation can only be approved by the user who sent the command."""
        return await self.bot.run_tool(self.chat_id, name, args, via=via, user_id=self.user_id)


Handler = Callable[[CommandContext], Awaitable[Any] | Any]


class TelegramBot:
    """Long-polling Telegram bot.

    Args:
        token: bot token; default ``TELEGRAM_BOT_TOKEN`` env (registered for log redaction).
        allowed_chat_ids: allow-list; default ``ALLOWED_CHAT_IDS`` env (comma separated ints).
        stack: where ``@command`` handlers live (default: current stack).
        api_base: Telegram API base URL.
        poll_timeout: ``getUpdates`` long-poll seconds.
        confirm_timeout: seconds :meth:`ask_confirm` waits before answering ``False``.
        transport: optional ``httpx`` transport (tests use ``httpx.MockTransport``).
        backoff_base / backoff_max: retry delays (seconds) after errors.
        set_commands: publish the command list with ``setMyCommands`` on start.
        conflict_delay: minimum wait after a 409 conflict.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        allowed_chat_ids: set[int] | list[int] | None = None,
        stack: Stack | None = None,
        api_base: str = "https://api.telegram.org",
        poll_timeout: int = 30,
        confirm_timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        set_commands: bool = True,
        conflict_delay: float = 5.0,
    ):
        install_log_redaction()
        self.token = token or secret("TELEGRAM_BOT_TOKEN")
        register_secret(self.token)
        if allowed_chat_ids is None:
            self.allowed_chat_ids = parse_chat_ids(env_list("ALLOWED_CHAT_IDS"))
        else:
            self.allowed_chat_ids = {int(c) for c in allowed_chat_ids}
        self._stack = stack
        self.api_base = api_base.rstrip("/")
        self.poll_timeout = poll_timeout
        self.confirm_timeout = confirm_timeout
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.set_commands = set_commands
        self.conflict_delay = conflict_delay
        self._client = httpx.AsyncClient(timeout=poll_timeout + 15, transport=transport)
        self._offset: int | None = None
        self._stop = asyncio.Event()
        self._pending: dict[str, tuple[asyncio.Future, int, int | None]] = {}  # nonce -> (future, chat, user)
        self._tasks: set[asyncio.Task] = set()
        self._message_handler: Handler | None = None
        self.running = False

    # -- registration
    @property
    def stack(self) -> Stack:
        return self._stack or get_stack()

    def command(self, name: str, help: str = "") -> Callable[[Handler], Handler]:
        return self.stack.command(name, help)

    def on_message(self, func: Handler) -> Handler:
        """Handler for plain (non-command) text from allowed chats."""
        self._message_handler = func
        return func

    def is_allowed(self, chat_id: int) -> bool:
        return chat_id in self.allowed_chat_ids

    # -- API
    async def api(self, method: str, **params: Any) -> Any:
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        url = f"{self.api_base}/bot{self.token}/{method}"
        resp = await self._client.post(url, json={k: v for k, v in params.items() if v is not None})
        try:
            data = resp.json()
        except ValueError:
            raise TelegramAPIError(method, resp.status_code, "non-JSON response") from None
        if not data.get("ok"):
            retry = (data.get("parameters") or {}).get("retry_after")
            raise TelegramAPIError(method, int(data.get("error_code") or resp.status_code),
                                   redact(str(data.get("description", "")))[:200], retry)
        return data.get("result")

    async def send(self, chat_id: int, text: str, **kwargs: Any) -> list[dict]:
        """Send ``text`` (split into 4096-char chunks). Extra kwargs go to the last chunk."""
        chunks = split_message(text)
        sent = []
        for i, chunk in enumerate(chunks):
            extra = kwargs if i == len(chunks) - 1 else {}
            sent.append(await self._send_one(chat_id, chunk, **extra))
        return sent

    async def _send_one(self, chat_id: int, text: str, **kwargs: Any) -> dict:
        for attempt in range(3):
            try:
                return await self.api("sendMessage", chat_id=chat_id, text=text, **kwargs)
            except TelegramAPIError as exc:
                if exc.code == 429 and attempt < 2:
                    await asyncio.sleep(min(exc.retry_after or 1, 30))
                    continue
                raise
        raise RuntimeError("unreachable")

    # -- confirmations
    async def ask_confirm(self, chat_id: int, text: str, *, timeout: float | None = None,
                          yes: str = "Onayla / Confirm", no: str = "İptal / Cancel",
                          user_id: int | None = None) -> bool:
        """Send ``text`` with Yes/No inline buttons; wait for a press from the same chat.

        ``user_id``: only this Telegram user may press the buttons (others get an alert and the
        press is ignored). ``CommandContext.confirm``/``run_tool`` pass the command sender, so in
        group chats nobody else can approve. ``None`` accepts any user of the allowed chat.
        Returns ``False`` on "No", on timeout and when the message cannot be sent.
        Requires the bot to be polling (:meth:`run`) or updates fed to :meth:`process_update`.
        """
        nonce = _secrets.token_hex(8)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[nonce] = (fut, chat_id, user_id)
        markup = {"inline_keyboard": [[
            {"text": yes, "callback_data": f"{CALLBACK_PREFIX}:{nonce}:y"},
            {"text": no, "callback_data": f"{CALLBACK_PREFIX}:{nonce}:n"},
        ]]}
        try:
            sent = await self.send(chat_id, text, reply_markup=markup)
        except Exception as exc:
            self._pending.pop(nonce, None)
            log.warning("could not send confirmation message: %s", type(exc).__name__)
            return False
        try:
            return bool(await asyncio.wait_for(fut, timeout or self.confirm_timeout))
        except asyncio.TimeoutError:
            message_id = sent[-1].get("message_id") if sent and isinstance(sent[-1], dict) else None
            if message_id:
                with _suppress():
                    await self.api("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                                   reply_markup={"inline_keyboard": []})
                with _suppress():
                    await self.send(chat_id, "Onay süresi doldu, işlem yapılmadı. / Confirmation expired.")
            return False
        finally:
            self._pending.pop(nonce, None)

    async def run_tool(self, chat_id: int, name: str, args: dict | None = None, *, via: Any = None,
                       user_id: int | None = None) -> dict:
        """Call a tool through ``via`` (a :class:`Stack` or :class:`Bridges`, default this bot's stack).

        If the tool answers ``needs_confirmation``, the preview is shown with buttons and the tool
        runs only after "Confirm" (using the returned ``confirm_id``). A refusal returns
        ``{ok:false, error:{code:"cancelled"}}``. ``user_id`` restricts who may approve.
        """
        target = via or self.stack
        result = await target.call(name, args or {})
        if not result.get("needs_confirmation"):
            return result
        preview = result.get("preview")
        body = preview if isinstance(preview, str) else json.dumps(preview, ensure_ascii=False, indent=2)
        if len(body) > 3000:
            body = body[:3000] + "\n…"
        approved = await self.ask_confirm(chat_id, f"Onay gerekli / Confirmation required: {name}\n\n{body}",
                                          user_id=user_id)
        if not approved:
            return {"ok": False, "error": {"code": "cancelled", "message": "cancelled by user"}}
        return await target.call(name, args or {}, confirm_id=result.get("confirm_id"))

    # -- update handling
    async def process_update(self, update: dict) -> None:
        """Handle one Telegram update (polling calls this; useful for tests or webhook mode)."""
        if "callback_query" in update:
            await self._on_callback(update["callback_query"])
            return
        # only new messages run commands; edits (edited_message) are ignored so an edit never re-runs one
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat_id = (message.get("chat") or {}).get("id")
        text = message.get("text") or ""
        if chat_id is None or not text:
            return
        user_id = (message.get("from") or {}).get("id")
        if text.startswith("/"):
            first, *rest = text.split()
            cmd = first[1:].split("@", 1)[0].lower()
            ctx = CommandContext(self, chat_id, user_id, text, cmd, rest, message)
            await self._on_command(ctx)
        elif self.is_allowed(chat_id) and self._message_handler is not None:
            ctx = CommandContext(self, chat_id, user_id, text, None, text.split(), message)
            await self._invoke(self._message_handler, ctx)

    async def _on_command(self, ctx: CommandContext) -> None:
        allowed = self.is_allowed(ctx.chat_id)
        if ctx.command == "id":
            await ctx.reply(f"Chat ID: {ctx.chat_id}")
            return
        if ctx.command == "start" and "start" not in self.stack.commands:
            if allowed:
                await ctx.reply(f"Merhaba! {self.stack.name} hazır. Komutlar için /help\n"
                                f"Hello! {self.stack.name} is ready. Send /help for commands.")
            else:
                await ctx.reply(f"Bu sohbet yetkili değil / This chat is not authorized.\nChat ID: {ctx.chat_id}\n"
                                "ALLOWED_CHAT_IDS içine ekleyin / add it to ALLOWED_CHAT_IDS.")
            return
        if not allowed:
            log.info("ignored /%s from a chat that is not in ALLOWED_CHAT_IDS", ctx.command)
            return
        if ctx.command == "help" and "help" not in self.stack.commands:
            await ctx.reply(self.help_text())
            return
        cmd = self.stack.commands.get(ctx.command or "")
        if cmd is None:
            await ctx.reply("Bilinmeyen komut / Unknown command. /help")
            return
        await self._invoke(cmd.func, ctx)

    def help_text(self) -> str:
        lines = [f"{self.stack.name} — komutlar / commands:"]
        for c in sorted(self.stack.commands.values(), key=lambda c: c.name):
            lines.append(f"/{c.name} — {c.help}" if c.help else f"/{c.name}")
        lines.append("/help — bu liste / this list")
        lines.append("/id — sohbet kimliği / chat id")
        return "\n".join(lines)

    async def _invoke(self, func: Handler, ctx: CommandContext) -> None:
        token = _current.set(self.stack)
        try:
            if inspect.iscoroutinefunction(func):
                result = await func(ctx)
            else:
                result = await _maybe_await(func, ctx)
            if isinstance(result, str) and result:
                await ctx.reply(result)
        except ToolError as exc:
            await self._safe_reply(ctx, f"{redact(exc.message)}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("handler for %s failed", f"/{ctx.command}" if ctx.command else "message")
            await self._safe_reply(ctx, "Komut çalışırken hata oluştu / The command failed.")
        finally:
            _current.reset(token)

    async def _safe_reply(self, ctx: CommandContext, text: str) -> None:
        with _suppress():
            await ctx.reply(text)

    async def _on_callback(self, cq: dict) -> None:
        data = cq.get("data") or ""
        cq_id = cq.get("id")
        message = cq.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
            return
        _, nonce, answer = parts
        pending = self._pending.get(nonce)
        if pending is None or chat_id is None or pending[1] != chat_id or not self.is_allowed(chat_id):
            with _suppress():
                await self.api("answerCallbackQuery", callback_query_id=cq_id, text="Geçersiz veya süresi dolmuş.")
            return
        fut, _, owner = pending
        presser = (cq.get("from") or {}).get("id")
        if owner is not None and presser != owner:
            with _suppress():
                await self.api("answerCallbackQuery", callback_query_id=cq_id, show_alert=True,
                               text="Bu onay sana ait değil / This confirmation is not yours.")
            return
        approved = answer == "y"
        if not fut.done():
            fut.set_result(approved)
        with _suppress():
            await self.api("answerCallbackQuery", callback_query_id=cq_id,
                           text="Onaylandı" if approved else "İptal edildi")
        if message.get("message_id"):
            with _suppress():
                await self.api("editMessageReplyMarkup", chat_id=chat_id, message_id=message["message_id"],
                               reply_markup={"inline_keyboard": []})

    # -- polling
    async def run(self) -> None:
        """Poll ``getUpdates`` until :meth:`stop`. Handlers run as tasks so they can await buttons."""
        if not self.token:
            log.error("TELEGRAM_BOT_TOKEN is not set; Telegram bot not started")
            return
        self.running = True
        self._stop.clear()
        failures = 0
        if self.set_commands:
            await self._publish_commands()
        try:
            while not self._stop.is_set():
                try:
                    updates = await self.api("getUpdates", offset=self._offset, timeout=self.poll_timeout,
                                             allowed_updates=["message", "callback_query"])
                    failures = 0
                except TelegramAPIError as exc:
                    failures += 1
                    if exc.code in (401, 404):
                        log.error("Telegram rejected the bot token (HTTP %s); stopping the bot", exc.code)
                        return
                    if exc.code == 429:
                        delay = float(exc.retry_after if exc.retry_after is not None else 5)
                        log.warning("Telegram flood control; retrying in %ss", delay)
                    elif exc.code == 409:
                        delay = max(self.conflict_delay, self._backoff(failures))
                        log.warning("Telegram 409 conflict (another instance polling or a webhook is set); "
                                    "retrying in %ss", delay)
                    else:
                        delay = self._backoff(failures)
                        log.warning("getUpdates failed (%s); retrying in %ss", exc.code, delay)
                    await self._sleep(delay)
                    continue
                except httpx.HTTPError as exc:
                    failures += 1
                    delay = self._backoff(failures)
                    log.warning("Telegram network error (%s); retrying in %ss", type(exc).__name__, delay)
                    await self._sleep(delay)
                    continue
                for update in updates or []:
                    uid = update.get("update_id")
                    if isinstance(uid, int):
                        self._offset = uid + 1
                    task = asyncio.create_task(self._handle_safely(update))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        finally:
            self.running = False

    async def _handle_safely(self, update: dict) -> None:
        try:
            await self.process_update(update)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("update handling failed")

    async def _publish_commands(self) -> None:
        cmds = [{"command": c.name, "description": (c.help or c.name)[:256]} for c in self.stack.commands.values()]
        for builtin, desc in (("help", "Komutlar / commands"), ("id", "Chat ID")):
            if builtin not in self.stack.commands:
                cmds.append({"command": builtin, "description": desc})
        try:
            await self.api("setMyCommands", commands=cmds[:100])
        except Exception as exc:  # cosmetic; never fatal
            log.info("setMyCommands skipped: %s", type(exc).__name__)

    def _backoff(self, failures: int) -> float:
        return min(self.backoff_base * 2 ** max(0, failures - 1), self.backoff_max)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def stop(self) -> None:
        self._stop.set()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def aclose(self) -> None:
        await self.stop()
        await self._client.aclose()

    def __repr__(self) -> str:
        return f"TelegramBot(stack={self.stack.name!r}, allowed={len(self.allowed_chat_ids)} chat(s))"


class _suppress:
    """``contextlib.suppress(Exception)`` that also works with ``async with``-free code paths."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, Exception)


def command(name: str, help: str = "") -> Callable[[Handler], Handler]:
    """Register a Telegram command on the current stack (listed in the manifest)."""
    return get_stack().command(name, help)
