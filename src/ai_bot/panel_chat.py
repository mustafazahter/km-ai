"""Panel chat: talk to the bot from the admin UI, pick the model, use the same tools.

Same agent, same safety rules as Telegram (tools needing a human are confirmed here, in the browser;
web content taints the turn; scheduled-only restrictions untouched). Differences:

* conversations are stored on the server (``panel_conversations`` / ``panel_messages``: plain user and assistant
  text only, secrets redacted, bound to the signed-in admin) so they can be reopened and deleted; they are kept
  apart from the Telegram and channel histories, which are never touched;
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
# well-known key shapes (a subset of memory.BLOCK_PATTERNS: the broad word-based ones would mangle normal chat)
SECRET_SHAPES = [re.compile(p) for p in (
    r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b", r"\bsk-[A-Za-z0-9_-]{16,}", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bAKIA[0-9A-Z]{16}\b", r"\bxox[abprs]-[A-Za-z0-9-]{10,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")]
MAX_CONVERSATIONS = 200      # per admin; the oldest are dropped
MAX_STORED_MESSAGES = 400    # per conversation
MAX_STORED_CHARS = 32000     # per stored message
CONV_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,40}")


def scrub(text: str) -> str:
    """Registered secrets are masked by redact(); also mask well-known key shapes someone pasted or a tool echoed."""
    out = redact(text)
    for pattern in SECRET_SHAPES:
        out = pattern.sub("[maskelendi]", out)
    return out[:MAX_STORED_CHARS]


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
    conversation: str = ""
    new_conversation: bool = False
    store: bool = True      # False while the panel protection is off (everybody would share one history)
    stored: bool = False

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

    def title_of(text: str) -> str:
        one = " ".join(text.split())
        return (one[:57] + "...") if len(one) > 60 else one

    def persist(turn: ChatTurn, chat_id: int, text: str) -> None:
        """Store the finished turn. A conversation deleted while the turn ran is not brought back."""
        if not turn.store or not turn.answer or not turn.conversation:
            return
        try:
            exists = ai.db.panel_get(turn.conversation, turn.user) is not None
            if not exists:
                if not turn.new_conversation:
                    return  # it was deleted meanwhile
                ai.db.panel_create(turn.conversation, turn.user, title_of(scrub(text)) or "Sohbet", turn.model, chat_id)
                ai.db.panel_prune(turn.user, MAX_CONVERSATIONS)
            ai.db.panel_add_turn(turn.conversation, scrub(text), scrub(turn.answer), turn.model, turn.tainted, MAX_STORED_MESSAGES)
            turn.stored = True
        except Exception:  # noqa: BLE001 - a storage problem must not lose the answer the user is waiting for
            log.exception("panel chat turn could not be stored")
            if turn.new_conversation:
                try:
                    ai.db.panel_delete(turn.conversation, turn.user)  # no empty shell in the list
                except Exception:  # noqa: BLE001
                    log.exception("panel chat leftover could not be removed")

    async def run_turn(turn: ChatTurn, chat_id: int, text: str, history: list[dict], model: str, tainted: bool) -> None:
        turn.tainted = tainted  # a conversation that started tainted is stored as tainted
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
            persist(turn, chat_id, text)
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
        conv_id = data.get("conversation")
        conv: dict | None = None
        if conv_id not in (None, "") and history_off(user):
            raise err(404, "not_found", "Sohbet bulunamadı.")
        if conv_id not in (None, ""):
            if not isinstance(conv_id, str) or not CONV_ID_RE.fullmatch(conv_id):
                raise err(422, "invalid_value", "conversation: invalid id")
            conv = ai.db.panel_get(conv_id, user.label)
            if conv is None:  # same answer for "missing" and "someone else's"
                raise err(404, "not_found", "Sohbet bulunamadı.")
            if any(t.status == "running" and t.conversation == conv_id for t in turns.values()):
                raise err(409, "busy", "Bu sohbette bir yanıt hâlâ hazırlanıyor.")
        if conv:  # a stored conversation is the source of truth for its context
            history = [{"role": m["role"], "content": m["content"][:MAX_MESSAGE]} for m in ai.db.panel_messages(conv["id"], MAX_HISTORY)]
        else:     # a new one may bring plain user/assistant context along (anything else is dropped)
            hist = data.get("history") or []
            if not isinstance(hist, list):
                raise err(422, "invalid_value", "history: list expected")
            history = [{"role": m.get("role"), "content": m.get("content")[:MAX_MESSAGE]} for m in hist[-MAX_HISTORY:]
                       if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
        chat_id = chat_for(data.get("chat_id") if conv is None else (data.get("chat_id") or conv.get("chat_id") or None))
        # outside content was read earlier in this conversation (stored) or the browser says so: the stricter rules apply
        tainted = bool(conv and conv.get("tainted")) or data.get("tainted") is True
        prune()
        if sum(1 for t in turns.values() if t.status == "running") >= MAX_RUNNING:
            raise err(429, "busy", "Çok fazla etkin sohbet turu var; biri bitince tekrar deneyin.")
        turn = ChatTurn(id=secrets.token_urlsafe(10), user=user.label, model=model,
                        conversation=conv["id"] if conv else secrets.token_urlsafe(9), new_conversation=conv is None,
                        store=not history_off(user))
        turns[turn.id] = turn
        turn.task = asyncio.create_task(run_turn(turn, chat_id, text.strip(), history, model, tainted))
        return {"ok": True, "turn": turn.id}

    @r.get("/chat/turn/{turn_id}")
    async def poll(turn_id: str, since: int = 0, user: UIUser = Depends(current_user)):
        turn = owned(turn_id, user)
        since = max(0, min(since, len(turn.events)))
        return {"ok": True, "status": turn.status, "events": turn.events[since:], "next": len(turn.events),
                "answer": turn.answer, "error": turn.error, "model": turn.model, "tainted": turn.tainted,
                "conversation": turn.conversation if turn.stored else ""}

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

    # ------------------------------------------------------------ stored conversations
    def history_off(user: UIUser) -> bool:
        """With the panel protection switched off every visitor is the same 'user': nothing is kept or listed."""
        return user.method in ("unprotected", "off")

    @r.get("/chat/conversations")
    async def conversations(user: UIUser = Depends(current_user)):
        if history_off(user):
            return {"ok": True, "items": [], "disabled": True}
        items = ai.db.panel_list(user.label, MAX_CONVERSATIONS)
        return {"ok": True, "items": [{"id": c["id"], "title": c["title"], "model": c["model"], "updated": c["updated"],
                                       "messages": c["messages"]} for c in items]}

    @r.get("/chat/conversations/{conv_id}")
    async def conversation(conv_id: str, user: UIUser = Depends(current_user)):
        conv = ai.db.panel_get(conv_id, user.label) if CONV_ID_RE.fullmatch(conv_id) and not history_off(user) else None
        if conv is None:
            raise err(404, "not_found", "Sohbet bulunamadı.")
        return {"ok": True, "id": conv["id"], "title": conv["title"], "model": conv["model"], "chat_id": conv["chat_id"],
                "tainted": bool(conv["tainted"]), "updated": conv["updated"],
                "messages": [{"role": m["role"], "content": m["content"], "ts": m["ts"]} for m in ai.db.panel_messages(conv["id"])]}

    @r.delete("/chat/conversations/{conv_id}")
    async def delete_conversation(conv_id: str, user: UIUser = Depends(current_user)):
        if history_off(user) or not CONV_ID_RE.fullmatch(conv_id) or ai.db.panel_get(conv_id, user.label) is None:
            raise err(404, "not_found", "Sohbet bulunamadı.")
        for t in turns.values():  # a reply still being written for it is stopped, not stored
            if t.conversation == conv_id and t.user == user.label and t.task is not None and not t.task.done():
                t.task.cancel()
        ai.db.panel_delete(conv_id, user.label)
        log.info("panel conversation deleted by %s", user.label)
        return {"ok": True}

    @r.delete("/chat/conversations")
    async def delete_all_conversations(user: UIUser = Depends(current_user)):
        if history_off(user):
            return {"ok": True, "deleted": 0}
        for t in turns.values():
            if t.user == user.label and t.task is not None and not t.task.done():
                t.task.cancel()
        n = ai.db.panel_delete_all(user.label)
        log.info("%s panel conversations deleted by %s", n, user.label)
        return {"ok": True, "deleted": n}

    return r


def _needs_key(panel: "Panel") -> bool:
    from .providers import resolve_provider
    s = panel.ai.settings
    return bool(resolve_provider(s.llm_provider, s.llm_base_url).key)


def _text(value: Any) -> str:
    """Whole preview as redacted text (never truncated: the human must see what they approve)."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return redact(text)
