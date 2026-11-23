"""Built-in tools. Registered with the kit's ``@tool`` so they are visible to the AI, ``/bridge`` and ``/mcp``.

``chat_id`` parameters: the AI never sees them (the agent fills in the current chat). Bridge/MCP
callers may pass one; when omitted the first ``ALLOWED_CHAT_IDS`` entry is used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from stack_kit import Stack, ToolError

from .htmltext import html_to_text
from .safefetch import FetchBlocked, FetchError

if TYPE_CHECKING:
    from .core import AiBot

ChatId = Annotated[int | None, Field(description="Telegram chat id (optional; default: first ALLOWED_CHAT_IDS)")]


def register(stack: Stack, app: "AiBot") -> None:
    @stack.tool("web.search", "Web'de ara; başlık, URL ve özet döner / Search the web: titles, URLs, snippets.",
                scope="read", timeout=30)
    async def web_search(query: Annotated[str, Field(min_length=1, max_length=400, description="search query")],
                         max_results: Annotated[int, Field(ge=1, le=10)] = 5) -> dict:
        results = await app.search.search(query, max_results)
        return {"provider": app.settings.search_provider, "results": results}

    @stack.tool("web.read", "Bir web sayfasının okunabilir metnini getirir (yalnız herkese açık http/https) / "
                "Fetch the readable text of a public web page.", scope="read", timeout=40)
    async def web_read(url: Annotated[str, Field(min_length=8, max_length=2000, description="http(s) URL")],
                       max_chars: Annotated[int | None, Field(ge=200, le=50000)] = None) -> dict:
        try:
            res = await app.fetcher.fetch(url)
        except FetchBlocked as exc:
            raise ToolError(f"blocked: {exc}", code="blocked") from None
        except FetchError as exc:
            raise ToolError(str(exc), code="fetch_failed") from None
        if res.status >= 400:
            raise ToolError(f"page returned HTTP {res.status}", code="fetch_failed")
        ctype = res.content_type.lower()
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            raw = res.body.decode(charset, errors="replace")
        except LookupError:
            raw = res.body.decode("utf-8", errors="replace")
        if "html" in ctype or (not ctype and raw.lstrip()[:1] == "<"):
            title, text = html_to_text(raw)
        elif ctype.startswith("text/") or "json" in ctype or "xml" in ctype or not ctype:
            title, text = "", raw.strip()
        else:
            raise ToolError(f"unsupported content type {ctype.split(';')[0] or 'unknown'}", code="unsupported")
        limit = max_chars or app.settings.web_read_max_chars
        truncated = res.truncated or len(text) > limit
        return {"url": res.url, "title": title, "text": text[:limit], "truncated": truncated}

    # -------------------------------------------------------------- schedule
    @stack.tool("schedule.add", "Zamanlanmış görev ekle: cron, göreli ('10 dakika sonra') veya ISO zaman. "
                "kind=message metni gönderir, kind=prompt zamanı gelince AI'ya istem olarak verir / "
                "Schedule a message or an AI prompt (cron, relative or ISO time).", scope="read")
    def schedule_add(
        when: Annotated[str, Field(min_length=1, max_length=100,
                                   description="cron '0 9 * * *' | '10 dakika sonra' | 'in 2 hours' | ISO time")],
        text: Annotated[str, Field(min_length=1, max_length=2000, description="message text or AI prompt")],
        kind: Literal["message", "prompt"] = "message",
        description: Annotated[str, Field(max_length=200, description="short label")] = "",
        chat_id: ChatId = None,
    ) -> dict:
        return app.scheduler.add(app.resolve_chat(chat_id), when, text, kind, description)

    @stack.tool("schedule.list", "Aktif zamanlanmış görevleri listele / List active scheduled tasks.", scope="read")
    def schedule_list(chat_id: ChatId = None) -> dict:
        return {"timezone": app.settings.tz, "tasks": app.scheduler.list(app.resolve_chat(chat_id))}

    @stack.tool("schedule.cancel", "Zamanlanmış görevi iptal et / Cancel a scheduled task.", scope="read")
    def schedule_cancel(task_id: Annotated[int, Field(ge=1)], chat_id: ChatId = None) -> dict:
        return app.scheduler.cancel(app.resolve_chat(chat_id), task_id)

    # -------------------------------------------------------------- memory
    @stack.tool("memory.remember", "Kalıcı bir bilgiyi hatırla (tek kısa cümle; sır kaydetme) / "
                "Store one durable fact about the user (no secrets).", scope="read")
    def memory_remember(content: Annotated[str, Field(min_length=1, max_length=500)],
                        tags: Annotated[str, Field(max_length=200, description="optional keywords")] = "",
                        chat_id: ChatId = None) -> dict:
        return app.memory.remember(app.resolve_chat(chat_id), content, tags)

    @stack.tool("memory.recall", "Hafızada ara (boş sorgu = en yeniler) / Search memories (empty = most recent).",
                scope="read")
    def memory_recall(query: Annotated[str, Field(max_length=400)] = "",
                      limit: Annotated[int, Field(ge=1, le=20)] = 5, chat_id: ChatId = None) -> dict:
        return {"items": app.memory.recall(app.resolve_chat(chat_id), query, limit)}

    @stack.tool("memory.forget", "Bir anıyı sil (kimlikle) / Forget a memory by id.", scope="read")
    def memory_forget(memory_id: Annotated[int, Field(ge=1)], chat_id: ChatId = None) -> dict:
        return app.memory.forget(app.resolve_chat(chat_id), memory_id)

    @stack.tool("memory.list", "Anıları listele / List memories.", scope="read")
    def memory_list(limit: Annotated[int, Field(ge=1, le=100)] = 20, offset: Annotated[int, Field(ge=0)] = 0,
                    chat_id: ChatId = None) -> dict:
        return app.memory.list(app.resolve_chat(chat_id), limit, offset)

    # -------------------------------------------------------------- AI over the bridge (n8n, other stacks)
    @stack.tool("ai.ask", "AI'ya soru sor; araçlarla çalışır ve cevabı döner. notify=true ise cevap Telegram'a da "
                "gönderilir. Onay gerektiren araçlar Telegram düğmesiyle sorulur / Ask the AI (with tools).",
                scope="read", timeout=300)
    async def ai_ask(prompt: Annotated[str, Field(min_length=1, max_length=4000)],
                     notify: bool = False, chat_id: ChatId = None) -> dict:
        chat = app.resolve_chat(chat_id)
        answer = await app.run_ai(chat, prompt, scheduled=True)
        if notify:
            await app.deliver(chat, answer)
        return {"answer": answer, "chat_id": chat}

    # -------------------------------------------------------------- incoming webhook (TradingView, n8n ...)
    if "notify" not in stack.hooks:
        @stack.hook("notify", body_secret_field="secret")
        async def notify_hook(req) -> dict:
            """POST /hook/notify — forwards a message to Telegram (only ALLOWED_CHAT_IDS; the AI is not run).

            Body: JSON ``{"text": "...", "chat_id": 123}`` (``chat_id`` optional) or plain text. Auth: HMAC
            signature, the bridge token, or the hook secret inside the JSON body as ``"secret"`` (for
            senders that cannot sign, e.g. TradingView). Closed (404) until hooks are enabled in the panel.
            """
            chat_id = None
            try:
                data = req.json()
            except ValueError:
                data = req.text()
            if isinstance(data, dict):
                text = data.get("text") or data.get("message") or ""
                chat_id = data.get("chat_id")
            else:
                text = data if isinstance(data, str) else ""
            text = str(text).strip()
            if not text:
                raise ToolError("text is empty", code="invalid_args")
            if chat_id is not None and not isinstance(chat_id, int):
                raise ToolError("chat_id must be an integer", code="invalid_args")
            chat = app.resolve_chat(chat_id)
            delivered = await app.deliver(chat, text[:3500])
            return {"delivered": bool(delivered), "chat_id": chat}
