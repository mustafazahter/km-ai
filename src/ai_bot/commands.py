"""Slash commands on every channel.

On Telegram the kit's bot dispatches ``stack.commands``. The other channels (WhatsApp, Microsoft Teams,
Slack, e-mail) call :func:`dispatch` from their inbound pipeline, so the SAME registry serves them: a command
written once (``@command`` below, or the kit's own ``@command``) works everywhere.

* A message that starts with ``/name`` or ``!name`` and names a registered command runs the command and
  never reaches the AI. Anything else is a normal message (``!`` exists because Slack swallows ``/...``).
* Only allow-listed senders get here (the channel pipeline already checked); commands never bypass it.
* Channels whose sender cannot be proven (e-mail: ``can_confirm`` is False) run read-only commands only:
  ``/help``, a bare ``/hafiza`` ``/gorevler`` ``/model``, and commands registered with ``readonly=True``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stack_kit import ToolError, get_stack, redact
from stack_kit.registry import COMMAND_NAME_RE

from .agent import HUMAN_SCOPES

if TYPE_CHECKING:
    from .channels.base import Channel, Inbound

log = logging.getLogger("ai_bot.commands")

PREFIXES = ("/", "!")
READONLY: set[str] = {"help"}                 # built-ins that never change anything (plugins: see readonly_of)
READ_FORMS: set[str] = {"hafiza", "gorevler", "model"}  # read-only while called without arguments
CONFIRM_INTRO = "Onay gerekiyor:"   # followed by the command or tool name


def command(name: str, help: str = "", *, readonly: bool = False):
    """Register a slash command for EVERY channel (Telegram included).

    ``handler(ctx)`` returns the reply text (or ``None``). Write it ``async`` when it uses ``ctx.reply``,
    ``ctx.confirm`` or ``ctx.run_tool``; a plain ``def`` handler runs in a worker thread (so it cannot stall the
    other channels) and may only compute and return text. ``ctx`` offers
    ``chat_id``, ``args``, ``arg_text``, ``text``, ``reply()``, ``confirm()`` and ``run_tool()``; on the
    non-Telegram channels it also has ``channel`` (the channel name). ``readonly=True`` means the command
    changes nothing, so it may also run on channels where the sender cannot be verified (e-mail).
    """
    key = name.lstrip("/!").lower()

    def deco(fn):
        stack = get_stack()
        stack.command(key, help)(fn)
        if readonly:
            readonly_of(stack).add(key)
        return fn

    return deco


def readonly_of(stack: Any) -> set[str]:
    """Commands of ONE stack that declared ``readonly=True`` (kept on the stack, never process-wide)."""
    found = getattr(stack, "ai_readonly_commands", None)
    if found is None:
        found = set()
        stack.ai_readonly_commands = found
    return found


def runs_on_untrusted(stack: Any, name: str, args: list[str]) -> bool:
    return name in READONLY or name in readonly_of(stack) or (name in READ_FORMS and not args)


@dataclass
class ChannelCommandContext:
    """Same surface as the kit's Telegram ``CommandContext`` so one handler serves every channel."""

    channel: "Channel"
    inbound: "Inbound"
    chat_id: int
    text: str
    command: str
    args: list[str]
    user_id: int | None = None
    bot: Any = None
    message: dict = field(default_factory=dict, repr=False)

    @property
    def arg_text(self) -> str:
        parts = self.text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    async def reply(self, text: str, **_: Any) -> None:
        await self.channel.send_text(self.inbound.ctx, text)

    async def confirm(self, text: str, timeout: float | None = None) -> bool:
        """Human confirmation by one-time code from the same sender (always ``False`` on e-mail)."""
        return await self.channel.confirm(self.chat_id, self.command, text, intro=f"{CONFIRM_INTRO} /{self.command}", timeout=timeout)

    async def run_tool(self, name: str, args: dict | None = None, via: Any = None) -> dict:
        """Call a tool with the same human-approval rule as the AI's tool loop: a tool with ``confirm=True`` or a
        ``trade``/``admin`` scope, and anything reached over a bridge, runs only after the same sender approves
        (code reply). For a bridge tool the approved arguments are shown; the remote tool's own preview is not.
        On e-mail nobody can approve, so such tools are refused."""
        args = args or {}
        stack = self.channel.ai.stack
        target = via or stack
        tool = stack.tools.get(name) if via is None else None
        remote = via is not None
        human = remote or (tool is not None and (tool.confirm or tool.scope in HUMAN_SCOPES))
        refused = {"ok": False, "error": {"code": "cancelled", "message": "cancelled by user"}}
        if human and (remote or not tool.confirm):   # nothing may run before the approval (the tool has no preview step)
            if not await self.channel.confirm(self.chat_id, name, args, intro=f"{CONFIRM_INTRO} {name}"):
                return refused
        result = await target.call(name, args)
        if not result.get("needs_confirmation"):
            return result
        if tool is not None and tool.confirm:        # local confirm=True tool: approve its own preview
            if not await self.channel.confirm(self.chat_id, name, result.get("preview"), intro=f"{CONFIRM_INTRO} {name}"):
                return refused
        elif not human:
            return refused
        return await target.call(name, args, confirm_id=result.get("confirm_id"))


def parse(text: str) -> tuple[str, list[str]] | None:
    """``"/hafiza sil 3"`` -> ``("hafiza", ["sil", "3"])``; ``None`` when it is not command-shaped."""
    parts = text.split()
    if not parts or parts[0][:1] not in PREFIXES:
        return None
    name = parts[0][1:].lower()
    return (name, parts[1:]) if COMMAND_NAME_RE.match(name) else None


async def dispatch(channel: "Channel", inb: "Inbound", chat_id: int, text: str) -> bool:
    """Run a registered command for ``text``. ``False`` = not a command, hand the text to the AI."""
    parsed = parse(text)
    if parsed is None:
        return False
    name, args = parsed
    cmd = channel.ai.stack.commands.get(name)
    if cmd is None:
        return False
    if not channel.can_confirm and not runs_on_untrusted(channel.ai.stack, name, args):
        await channel.send_text(inb.ctx, f"/{name} bu kanalda kapalı: gönderen doğrulanamadığı için değişiklik yapan komutlar "
                                         "çalışmaz. / disabled on this channel (the sender cannot be verified).")
        return True
    ctx = ChannelCommandContext(channel, inb, chat_id, text, name, args)
    try:
        async with channel.ai.lock_for(chat_id):   # never in the middle of an AI turn of the same chat
            if inspect.iscoroutinefunction(cmd.func):
                result = await cmd.func(ctx)
            else:                                   # a blocking handler must not stall every channel
                result = await asyncio.to_thread(cmd.func, ctx)
                if inspect.isawaitable(result):
                    result = await result
    except ToolError as exc:
        result = redact(exc.message)
    except Exception as exc:  # noqa: BLE001 - one broken command must not stop the channel
        log.warning("command /%s failed (%s)", name, type(exc).__name__)
        result = "Komut çalışırken hata oluştu / The command failed."
    if isinstance(result, str) and result.strip():
        await channel.send_text(inb.ctx, result)
    return True
