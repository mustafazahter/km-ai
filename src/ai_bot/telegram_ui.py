"""Telegram commands and the free-text handler.

``/start`` and ``/id`` are the kit's built-ins (they answer even when ``ALLOWED_CHAT_IDS`` is empty);
everything else answers only allowed chats.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from stack_kit.telegram import CommandContext, TelegramBot

if TYPE_CHECKING:
    from .core import AiBot

log = logging.getLogger("ai_bot.telegram")

HELP = """{name} — AI asistan / AI assistant

Bana normal mesaj yaz; web'de arayabilir, sayfa okuyabilir, hatırlatma kurabilir ve önemli şeyleri hatırlayabilirim.
Just write to me: I can search the web, read pages, schedule reminders and remember things.

Örnekler / examples:
• "Bugün İstanbul'da hava nasıl?"
• "Her sabah 9'da teknoloji haberlerini özetle"
• "20 dakika sonra çamaşırı hatırlat"
• "Adımın Deniz olduğunu hatırla"

Komutlar / commands:
/hafiza — anılar (sil: /hafiza sil <no>, hepsi: /hafiza temizle)
/gorevler — zamanlanmış görevler (iptal: /gorevler iptal <no>)
/model — kullanılan model (değiştir: /model <ad>, geri al: /model varsayilan)
/sifirla — sohbet geçmişini temizle (anılar kalır)
{idline}/help — bu metin / this text{extra}"""


def register(bot: TelegramBot, app: "AiBot") -> None:
    stack = bot.stack

    def cmd(name: str, help: str):
        def deco(fn):
            if name in stack.commands:
                log.warning("/%s is already registered by another module; ai-bot keeps the existing one", name)
                return fn
            return stack.command(name, help)(fn)
        return deco

    @cmd("help", "Yardım / help")
    async def help_cmd(ctx: CommandContext) -> str:
        others = sorted(c for c in stack.commands if c not in {"help", "hafiza", "gorevler", "model", "sifirla"})
        extra = ("\n\nDiğer komutlar / other commands: " + " ".join(f"/{c}" for c in others)) if others else ""
        idline = "" if getattr(ctx, "channel", None) is not None else "/id — sohbet kimliği / chat id\n"  # /id is Telegram-only
        return HELP.format(name=stack.name, extra=extra, idline=idline)

    @cmd("hafiza", "Anıları gör/sil / memories")
    async def hafiza(ctx: CommandContext) -> str:
        chat = ctx.chat_id
        if ctx.args[:1] and ctx.args[0].lower() in ("sil", "delete", "forget"):
            if len(ctx.args) < 2 or not ctx.args[1].lstrip("#").isdigit():
                return "Kullanım / usage: /hafiza sil <no>"
            mid = int(ctx.args[1].lstrip("#"))
            return "Silindi / forgotten." if app.db.delete_memory(chat, mid) else "Bu numarada anı yok / not found."
        if ctx.args[:1] and ctx.args[0].lower() in ("temizle", "clear"):
            total = app.db.count_memories(chat)
            if not total:
                return "Hafıza zaten boş / memory is empty."
            if not await ctx.confirm(f"{total} anının hepsi silinsin mi? / Delete all {total} memories?"):
                return "Vazgeçildi / cancelled."
            app.db.clear_memories(chat)
            return f"{total} anı silindi / memories deleted."
        data = app.memory.list(chat, limit=50)
        if not data["items"]:
            return "Henüz bir şey hatırlamıyorum / no memories yet."
        lines = [f"Anılar / memories ({data['total']}):"]
        lines += [f"#{m['id']} · {m['content']}" for m in data["items"]]
        if data["total"] > len(data["items"]):
            lines.append(f"… (+{data['total'] - len(data['items'])})")
        lines.append("\nSil / delete: /hafiza sil <no>")
        return "\n".join(lines)

    @cmd("gorevler", "Zamanlanmış görevler / scheduled tasks")
    async def gorevler(ctx: CommandContext) -> str:
        chat = ctx.chat_id
        if ctx.args[:1] and ctx.args[0].lower() in ("iptal", "cancel", "sil"):
            if len(ctx.args) < 2 or not ctx.args[1].lstrip("#").isdigit():
                return "Kullanım / usage: /gorevler iptal <no>"
            sid = int(ctx.args[1].lstrip("#"))
            return "İptal edildi / cancelled." if app.db.cancel_schedule(chat, sid) else "Bu numarada aktif görev yok."
        tasks = app.scheduler.list(chat)
        if not tasks:
            return "Zamanlanmış görev yok / no scheduled tasks."
        lines = [f"Görevler / tasks ({app.settings.tz}):"]
        for t in tasks:
            rep = f"tekrar: {t['cron']}" if t["cron"] else "tek sefer"
            kind = "AI" if t["kind"] == "prompt" else "mesaj"
            label = t["description"] or t["text"]
            lines.append(f"#{t['id']} · {t['next_run']} · {rep} · {kind}: {label[:80]}")
        lines.append("\nİptal / cancel: /gorevler iptal <no>")
        return "\n".join(lines)

    @cmd("model", "Modeli gör/değiştir / show or change model")
    async def model_cmd(ctx: CommandContext) -> str:
        chat = ctx.chat_id
        s = app.settings
        if not ctx.args:
            current = app.agent.model_for(chat)
            choices = f"\nSeçenekler / choices: {', '.join(s.llm_model_choices)}" if s.llm_model_choices else ""
            return f"Model: {current}\nVarsayılan / default: {s.llm_model}{choices}"
        wanted = ctx.args[0].strip()
        if wanted.lower() in ("varsayilan", "varsayılan", "default", "reset"):
            app.db.set_setting(chat, "model", None)
            return f"Varsayılan modele dönüldü / back to default: {s.llm_model}"
        if s.llm_model_choices and wanted not in s.llm_model_choices:
            return f"Bu model izinli değil / not allowed. Seçenekler: {', '.join(s.llm_model_choices)}"
        if len(wanted) > 120 or any(ch.isspace() for ch in wanted):
            return "Geçersiz model adı / invalid model name."
        app.db.set_setting(chat, "model", wanted)
        return f"Bu sohbet için model / model for this chat: {wanted}"

    @cmd("sifirla", "Sohbet geçmişini temizle / clear chat history")
    async def sifirla(ctx: CommandContext) -> str:
        n = app.db.clear_history(ctx.chat_id)
        return f"Sohbet geçmişi temizlendi ({n} mesaj). Anılar duruyor; onlar için /hafiza."

    async def on_text(ctx: CommandContext) -> None:
        typing = asyncio.create_task(_typing(bot, ctx.chat_id))
        try:
            answer = await app.run_ai(ctx.chat_id, ctx.text)
        finally:
            typing.cancel()
        await ctx.reply(answer)

    if bot._message_handler is not None:
        log.warning("replacing an existing free-text handler with the ai-bot handler")
    bot.on_message(on_text)


async def _typing(bot: TelegramBot, chat_id: int) -> None:
    try:
        while True:
            try:
                await bot.api("sendChatAction", chat_id=chat_id, action="typing")
            except Exception:
                return
            await asyncio.sleep(4.5)
    except asyncio.CancelledError:
        return
