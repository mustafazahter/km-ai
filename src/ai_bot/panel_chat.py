"""Panel chat: talk to the bot from the admin UI, pick the model, use the same tools.

Same agent, same safety rules as Telegram (tools needing a human are confirmed here, in the browser;
web content taints the turn; scheduled-only restrictions untouched). Differences:

* history is kept by the browser (plain user/assistant text, sent with each message), nothing is stored
  on the server and the Telegram chat history is never touched;
* a turn runs in the background and the page polls ``/chat/turn/{id}`` (works behind any proxy);
* a confirmation is a pending future answered by ``POST /chat/confirm`` (120 s, then it counts as "no").
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request

from stack_kit import redact
from stack_ui import UIUser, current_user

from .llm import LLMError

if TYPE_CHECKING:
    from .panel import Panel

log = logging.getLogger("ai_bot.panel_chat")

MAX_MESSAGE = 8000
MAX_HISTORY = 40
CONFIRM_TIMEOUT = 120
MAX_RUNNING = 2
LOCK_WAIT = 30
MAX_PREVIEW = 4000  # a confirmation card shows everything or the action is refused; never a cut-off view
KEEP_TURNS = 20
TURN_TTL = 900
MODEL_RE = re.compile(r"[^\s]{1,200}")


@dataclass
class ChatTurn:
    id: str
    user: str
    started: float = field(default_factory=time.time)
    status: str = "running"  # running | done | error | cancelled
    events: list[dict] = field(default_factory=list)
    answer: str | None = None
    error: str | None = None
    model: str = ""
    task: asyncio.Task | None = None
    pending: asyncio.Future | None = None
    pending_id: str | None = None
    tainted: bool = False

    def emit(self, event: dict) -> None:
        self.events.append({"i": len(self.events), "t": time.time(), **event})


def build_router(panel: "Panel", err, body_of) -> APIRouter:
    r = APIRouter()
    ai = panel.ai
    turns: dict[str, ChatTurn] = {}
    ai.panel_turns = turns  # AiBot.aclose cancels running turns

    def owned(turn_id: Any, user: UIUser) -> ChatTurn:
        turn = turns.get(str(turn_id or ""))
        if turn is None or turn.user != user.label:  # same answer for "missing" and "someone else's"
            raise err(404, "not_found", "Tur bulunamadı.")
        return turn

    def prune() -> None:
        now = time.time()
        for tid in [t for t, x in turns.items() if x.status != "running" and now - x.started > TURN_TTL]:
            turns.pop(tid, None)
        finished = [x for x in turns.values() if x.status != "running"]
        for x in sorted(finished, key=lambda y: y.started)[:-KEEP_TURNS]:
            turns.pop(x.id, None)

    def chat_for(requested: Any) -> int:
        """Tool context: an allowed chat (memory, schedules). Empty allow-list -> 0, chat tools refuse."""
        allowed = [c for c in ai.settings.allowed_chat_ids if c]
        if requested is not None and requested != "":
            try:
                rid = int(requested)
            except (TypeError, ValueError):
                raise err(422, "invalid_value", "chat_id must be an integer") from None
            if rid not in allowed:
                raise err(422, "invalid_value", "chat_id is not in the allow-list")
            return rid
        return allowed[0] if allowed else 0

    async def run_turn(turn: ChatTurn, chat_id: int, text: str, history: list[dict], model: str, tainted: bool) -> None:
        async def confirmer(_chat: int, tool_name: str, preview: Any) -> bool:
            shown = _text(preview)
            if len(shown) > MAX_PREVIEW:
                turn.emit({"type": "note", "text": f"{tool_name}: işlem ayrıntısı çok uzun, güvenle gösterilemedi; reddedildi."})
                return False
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            cid = secrets.token_urlsafe(8)
            turn.pending, turn.pending_id = fut, cid
            turn.emit({"type": "confirm", "id": cid, "tool": tool_name, "preview": shown})
            try:
                return await asyncio.wait_for(fut, CONFIRM_TIMEOUT) is True
            except asyncio.TimeoutError:
                turn.emit({"type": "note", "text": "Onay süresi doldu; işlem yapılmadı."})
                return False
            finally:
                turn.pending, turn.pending_id = None, None

        def on_event(ev: dict) -> None:
            if ev.get("type") == "tainted":
                turn.tainted = True
            turn.emit(ev)

        lock = ai.lock_for(chat_id)
        try:
            try:
                await asyncio.wait_for(lock.acquire(), LOCK_WAIT)
            except asyncio.TimeoutError:
                turn.error, turn.status = "Bu sohbet bağlamı şu an meşgul (Telegram'da onay bekleyen bir işlem olabilir).", "error"
                turn.emit({"type": "end", "status": turn.status})
                return
            try:
                turn.answer = await ai.agent.run(chat_id, text, confirmer=confirmer, save_history=False, model=model,
                                                 history=history, on_event=on_event, tainted=tainted)
            finally:
                lock.release()
            turn.status = "done"
        except LLMError as exc:
            from .providers import friendly_error, resolve_provider
            s_ = ai.settings
            turn.error = redact("AI servisine ulaşılamadı: " + friendly_error(exc, resolve_provider(s_.llm_provider, s_.llm_base_url)))[:300]
            turn.status = "error"
            log.warning("panel chat turn failed: %s", exc.message)
        except asyncio.CancelledError:
            turn.status = "cancelled"
            turn.error = "İptal edildi. Onaylanmış bir işlem başlamışsa tamamlanmış olabilir; sonucu Telegram/günlükten kontrol edin."
        except Exception:  # noqa: BLE001 - never leak internals into the panel
            log.exception("panel chat turn crashed")
            turn.error, turn.status = "Beklenmeyen hata; günlüğe bakın.", "error"
        turn.emit({"type": "end", "status": turn.status})

    @r.get("/chat/info")
    async def info(user: UIUser = Depends(current_user)):
        s = ai.settings
        allowed = [c for c in s.allowed_chat_ids if c]
        names = {c["chat_id"]: (c.get("name") or (("@" + c["username"]) if c.get("username") else None))
                 for c in ai.seen_chats.values()}
        return {"ok": True, "model": s.llm_model, "chats": [{"id": c, "name": names.get(c)} for c in allowed],
                "tools": sorted(ai.stack.tools.keys()), "max_message": MAX_MESSAGE,
                "configured": bool(s.llm_model) and (bool(s.llm_api_key) or not _needs_key(panel))}

    @r.post("/chat/send")
    async def send(request: Request, user: UIUser = Depends(current_user)):
        data = await body_of(request)
        text = data.get("message")
        if not isinstance(text, str) or not text.strip():
            raise err(422, "invalid_value", "message: text expected")
        if len(text) > MAX_MESSAGE:
            raise err(422, "invalid_value", f"message: at most {MAX_MESSAGE} characters")
        model = data.get("model") or ai.settings.llm_model
        if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
            raise err(422, "invalid_value", "model: invalid name")
        hist = data.get("history") or []
        if not isinstance(hist, list):
            raise err(422, "invalid_value", "history: list expected")
        history = [{"role": m.get("role"), "content": m.get("content")[:MAX_MESSAGE]} for m in hist[-MAX_HISTORY:]
                   if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
        chat_id = chat_for(data.get("chat_id"))
        tainted = data.get("tainted") is True  # the browser says earlier answers used outside content
        prune()
        if sum(1 for t in turns.values() if t.status == "running") >= MAX_RUNNING:
            raise err(429, "busy", "Çok fazla etkin sohbet turu var; biri bitince tekrar deneyin.")
        turn = ChatTurn(id=secrets.token_urlsafe(10), user=user.label, model=model)
        turns[turn.id] = turn
        turn.task = asyncio.create_task(run_turn(turn, chat_id, text.strip(), history, model, tainted))
        return {"ok": True, "turn": turn.id}

    @r.get("/chat/turn/{turn_id}")
    async def poll(turn_id: str, since: int = 0, user: UIUser = Depends(current_user)):
        turn = owned(turn_id, user)
        since = max(0, min(since, len(turn.events)))
        return {"ok": True, "status": turn.status, "events": turn.events[since:], "next": len(turn.events),
                "answer": turn.answer, "error": turn.error, "model": turn.model, "tainted": turn.tainted}

    @r.post("/chat/confirm")
    async def confirm(request: Request, user: UIUser = Depends(current_user)):
        data = await body_of(request)
        turn = owned(data.get("turn"), user)
        fut = turn.pending
        if fut is None or data.get("id") != turn.pending_id:
            raise err(404, "not_found", "Bekleyen onay yok (süresi dolmuş olabilir).")
        if fut.done():
            raise err(409, "already_answered", "Bu onay zaten yanıtlandı.")
        approve = data.get("approve") is True  # anything but a real true is a refusal
        fut.set_result(approve)
        turn.emit({"type": "note", "text": "Onaylandı." if approve else "Reddedildi."})
        return {"ok": True}

    @r.post("/chat/cancel")
    async def cancel(request: Request, user: UIUser = Depends(current_user)):
        data = await body_of(request)
        turn = owned(data.get("turn"), user)
        if turn.task is not None and not turn.task.done():
            turn.task.cancel()
        return {"ok": True}

    return r


def _needs_key(panel: "Panel") -> bool:
    from .providers import resolve_provider
    s = panel.ai.settings
    return bool(resolve_provider(s.llm_provider, s.llm_base_url).key)


def _text(value: Any) -> str:
    """Whole preview as redacted text (never truncated: the human must see what they approve)."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return redact(text)
