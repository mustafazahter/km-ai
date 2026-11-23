"""Admin UI (``/ui``) for ai-bot: overview, setup wizard, settings, memory, tasks, tools, log.

Built on ``stack_ui`` (identity, password/first-time setup, CSRF, security headers, stores, the
bridge gate API at ``/ui/api/bridge``). Every route here sits under
``/ui/api`` and therefore requires a signed-in user; state-changing routes also require the CSRF
token (both added by ``mount_ui``). Rules kept in this file:

* secrets are write-only: no response ever contains a token or key (only ``set`` + ``…last4``);
  test endpoints never echo what they were given and never put a token into an error or a log;
* only :data:`~ai_bot.runtime_config.SCHEMA` settings can change; security locks are code;
* every change is applied to the running bot at once (:class:`~ai_bot.runtime_config.LiveConfig`).
"""

from __future__ import annotations

import collections
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request

from stack_kit import BridgeError, Stack, ToolError, env_str, redact, register_secret
from stack_ui import UIAuthError, UIConfig, UIRuntime, UIUser, current_user, mount_ui

from . import runtime_config as rc
from .agent import HIDDEN_FROM_AI, HUMAN_SCOPES
from .llm import LLMError
from .netpolicy import NetBlocked
from .providers import (BY_ID, effective_base, friendly_error, list_models, make_client, policy_for,
                        public_providers, resolve_provider)

if TYPE_CHECKING:
    from .core import AiBot

log = logging.getLogger("ai_bot.panel")

UI_DIR = Path(__file__).resolve().parent / "ui"
TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,64}$")
EVENT_HIDDEN_KEYS = {"prompt", "answer", "text", "content"}



# --------------------------------------------------------------------------- log buffer


class LogBuffer(logging.Handler):
    """Last N log records for the UI. Messages are redacted (registered secrets become ``***``);
    tracebacks are reduced to the exception type."""

    def __init__(self, size: int = 400):
        super().__init__(level=logging.INFO)
        self.records: collections.deque[dict] = collections.deque(maxlen=size)
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if record.exc_info and record.exc_info[0] is not None:
            msg += f" ({record.exc_info[0].__name__})"
        self._seq += 1
        self.records.append({"id": self._seq, "ts": record.created, "level": record.levelname.lower(),
                             "logger": record.name, "message": redact(msg)[:600]})

    def tail(self, *, min_level: int = logging.INFO, limit: int = 200) -> list[dict]:
        out = [r for r in self.records if logging.getLevelName(r["level"].upper()) >= min_level]
        return out[-limit:][::-1]


_LOG_BUFFER: LogBuffer | None = None


def log_buffer() -> LogBuffer:
    global _LOG_BUFFER
    if _LOG_BUFFER is None:
        _LOG_BUFFER = LogBuffer()
        logging.getLogger().addHandler(_LOG_BUFFER)
    return _LOG_BUFFER


# --------------------------------------------------------------------------- helpers


def _err(status: int, code: str, message: str) -> UIAuthError:
    return UIAuthError(status, code, message)


async def _body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise _err(413, "payload_too_large", "body too large")
    try:
        data = json.loads(raw or b"{}")
    except (ValueError, RecursionError):
        raise _err(400, "bad_request", "body must be JSON") from None
    if not isinstance(data, dict):
        raise _err(400, "bad_request", "body must be a JSON object")
    return data


def _host(url: str | None) -> str | None:
    """scheme://host[:port] only (never user:password, path or query)."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return None
    return f"{parts.scheme}://{host}{port}" if host else None


def _opt_str(body: dict, key: str, max_len: int = 8192) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_len:
        raise _err(422, "invalid_value", f"{key}: text expected")
    return value.strip() or None


# --------------------------------------------------------------------------- panel


class Panel:
    def __init__(self, ai: "AiBot", live: rc.LiveConfig, *, http_transport: httpx.AsyncBaseTransport | None = None,
                 telegram_api_base: str = "https://api.telegram.org", app: FastAPI | None = None):
        self.ai = ai
        self.live = live
        self.app = app
        self.transport = http_transport
        self.telegram_api_base = telegram_api_base.rstrip("/")
        self.logs = log_buffer()
        self.router = self._build()

    # -- shared state
    @property
    def stack(self) -> Stack:
        return self.ai.stack

    def _search_status(self) -> dict:
        s = self.ai.settings
        provider = s.search_provider
        ready = {"auto": True, "parallel": True, "duckduckgo": True, "tavily": bool(s.tavily_api_key), "brave": bool(s.brave_api_key),
                 "serper": bool(s.serper_api_key),
                 "searxng": bool(s.searxng_url)}.get(provider, False)
        warning = None
        if provider == "auto":
            warning = ("Otomatik mod ücretsiz kanalları sırayla dener ve hata vereni atlar (anahtarlıysa Tavily/Brave, sonra "
                       "Parallel (anahtarsız), DuckDuckGo, sonra herkese açık SearXNG sunucuları). Sorgular üçüncü taraf sunuculara gidebilir; "
                       "en güvenilir ve gizli sonuç için kendi Tavily/Brave anahtarınız.")
        elif provider == "duckduckgo":
            warning = ("Anahtarsız DuckDuckGo güvenilmez olabilir: bulut sunucularından gelen otomatik "
                       "istekleri bot korumasıyla reddedebilir. Güvenilir arama için Tavily, Brave veya SearXNG.")
        elif not ready:
            warning = {"tavily": "TAVILY_API_KEY gerekli.", "brave": "BRAVE_API_KEY gerekli.", "serper": "SERPER_API_KEY gerekli.",
                       "searxng": "SEARXNG_URL gerekli."}.get(provider, "Bilinmeyen sağlayıcı.")
        return {"provider": provider, "ready": ready, "warning": warning}

    @property
    def ui(self) -> UIRuntime | None:
        return getattr(self.app.state, "ui", None) if self.app is not None else None

    @property
    def gate(self):
        return getattr(self.app.state, "gate", None) if self.app is not None else None

    def field(self, name: str) -> dict:
        """Where one wizard/settings field comes from. Secrets: only ``set`` + ``…last4``."""
        if name in rc.SECRET_NAMES:
            d = self.live.secrets.describe([name])[0]
            return {"name": name, "secret": True, "set": d["set"], "preview": d["preview"], "source": d["source"],
                    "env_set": bool(env_str(name))}
        spec = self.live.store.schema[name]
        info = self.live.store.resolve(name)
        value = info["value"]
        return {"name": name, "secret": False, "set": value not in (None, ""), "value": value,
                "source": info["source"], "env_set": env_str(spec.env or name) is not None}

    def panel_field(self) -> dict:
        ui = self.ui
        if ui is None:
            return {"name": "UI_PASSWORD", "secret": True, "set": False, "preview": None, "source": "none",
                    "env_set": False}
        src = ui.password_source  # env | stored | none
        return {"name": "UI_PASSWORD", "secret": True, "set": src != "none" or ui.config.auth == "komuta",
                "preview": None, "source": {"env": "env", "stored": "ui"}.get(src, "komuta" if ui.config.auth == "komuta" else "none"),
                "env_set": src == "env"}

    def _search_fp(self) -> str:
        s = self.ai.settings
        return rc.fingerprint(s.search_provider, s.searxng_url, s.tavily_api_key, s.brave_api_key, s.serper_api_key)

    def _chats_fp(self) -> str:
        return rc.fingerprint(*[str(c) for c in sorted(c for c in self.ai.settings.allowed_chat_ids if c)])

    CHANNEL_CHOICES = {"telegram": "Telegram", "whatsapp": "WhatsApp", "teams": "Microsoft Teams", "slack": "Slack",
                       "email": "E-posta", "none": "Yalnız panel sohbeti"}

    def chosen_channel(self, st: dict) -> str | None:
        """The channel the wizard continues with: the user's pick, else what an existing install already uses."""
        picked = st.get("channel")
        if picked in self.CHANNEL_CHOICES:
            return picked
        s = self.ai.settings
        if s.telegram_bot_token or any(c for c in s.allowed_chat_ids):
            return "telegram"
        mgr = self.ai.channels
        if mgr is not None:
            for ch in mgr.channels.values():
                if ch.enabled():
                    return ch.name
        return None

    def _channel_step(self, name: str) -> dict:
        mgr = self.ai.channels
        ch = mgr.channels.get(name) if mgr is not None else None
        title = self.CHANNEL_CHOICES[name]
        if ch is None:
            return {"id": "channel_setup", "title": f"{title} kurulumu", "state": "todo", "channel": name,
                    "detail": "Kanal bu kurulumda yok", "fields": []}
        v = ch.view()
        if v["status"] == "ready" and v["allowed"]:
            state, detail = "done", f"{title} açık · {len(v['allowed'])} izinli"
        elif v["status"] == "ready":
            state, detail = "todo", "İzin listesi boş: kimse yazamaz"
        elif v["status"] == "error":
            state, detail = "todo", "Sağlayıcı hata veriyor; ayarları ve Test et'i kontrol edin"
        elif v["status"] == "missing":
            state, detail = "todo", "Eksik ayar: " + ", ".join(str(m) for m in v["missing"][:3])
        else:
            state, detail = "todo", "Kapalı: ayarları girip anahtarı açın"
        return {"id": "channel_setup", "title": f"{title} kurulumu", "state": state, "channel": name,
                "detail": detail, "fields": []}

    def _channel_summary(self) -> list[dict]:
        """Every chat channel in one list (Telegram first) for the overview."""
        s = self.ai.settings
        running = bool(self.ai.telegram.running) if self.ai.telegram else bool(self.ai.bot and self.ai.bot.running)
        tg_state = ("off" if not s.telegram_enabled else "ready" if running else
                    "missing" if not s.telegram_bot_token else "connecting")
        out = [{"name": "telegram", "title": "Telegram", "status": tg_state}]
        mgr = self.ai.channels
        if mgr is not None:
            out += [{"name": c["name"], "title": c["title"], "status": c["status"]} for c in mgr.describe()]
        return out

    def _shown_model(self) -> str:
        """Model the wizard pre-fills: the provider's recommendation while nothing was chosen yet (fresh install: no key,
        built-in default). Running installs keep showing (and using) their real model."""
        s = self.ai.settings
        if not s.llm_api_key and self.field("LLM_MODEL")["source"] == "default":
            rec = resolve_provider(s.llm_provider, s.llm_base_url).model
            if rec:
                return rec
        return s.llm_model

    def setup_status(self) -> dict:
        """Wizard steps. ``state``: ``done`` (verified: tested, or typed in the panel and confirmed),
        ``ready`` (filled from env/default but NOT yet verified: still has to be confirmed) or ``todo``.
        Only ``done`` counts as finished; a value coming from the environment is never taken on trust."""
        s = self.ai.settings
        st = self.live.setup.read()
        token = s.telegram_bot_token
        tg = st.get("telegram") or {}
        llm = st.get("llm") or {}
        search = st.get("search") or {}
        acks = st.get("confirmed") or {}
        prov = resolve_provider(s.llm_provider, s.llm_base_url)

        def env_like(*fields: dict) -> bool:
            return all(f["source"] in ("env", "default") for f in fields if f["set"]) and any(f["set"] for f in fields)

        steps = []
        pf = self.panel_field()
        ui = self.ui
        protection = ui.protection_enabled if ui is not None else True
        if pf["set"]:
            detail = {"env": "UI_PASSWORD ortam değişkeninden", "ui": "Parola belirlendi",
                      "komuta": "Komuta girişi"}.get(pf["source"], "Ayarlı")
            state = "done"
        else:
            detail, state = "Parola yok", "todo"
        if not protection:
            detail, state = "Koruma KAPALI", "todo"
        steps.append({"id": "panel", "title": "Panel parolası", "state": state, "detail": detail,
                      "fields": [pf], "protection": protection})

        chosen = self.chosen_channel(st)
        steps.append({"id": "channel", "title": "Kanal", "state": "done" if chosen else "todo", "choice": chosen,
                      "choices": [{"id": k, "title": v} for k, v in self.CHANNEL_CHOICES.items()],
                      "detail": self.CHANNEL_CHOICES[chosen] if chosen else "Hangi kanaldan konuşacağınızı seçin",
                      "fields": []})
        completed_at = float(st.get("completed_at") or 0)
        late = bool(completed_at) and float(st.get("channel_at") or 0) > completed_at  # picked after the wizard was finished
        if chosen not in (None, "none", "telegram"):
            steps.append({**self._channel_step(chosen), "late": late})

        f_tok = self.field("TELEGRAM_BOT_TOKEN")
        tested = bool(token) and tg.get("fp") == rc.fingerprint(token)
        state = "done" if tested else ("ready" if token else "todo")
        detail = (f"@{tg['username']}" if tested and tg.get("username") else
                  "Ortamdan geldi, doğrulanmadı" if state == "ready" and f_tok["source"] == "env" else
                  "Token kayıtlı, test edilmedi" if token else "Token yok")
        # Telegram keeps its steps while it is the pick, or while it is already in use next to another pick
        tg_in_use = chosen == "telegram" or (chosen not in (None, "none") and bool(token or any(c for c in s.allowed_chat_ids)))
        if tg_in_use:
            steps.append({"id": "telegram", "title": "Telegram botu", "state": state, "detail": detail, "fields": [f_tok],
                          "late": late})

        f_chat = self.field("ALLOWED_CHAT_IDS")
        chats = [c for c in s.allowed_chat_ids if c]  # 0 is a placeholder, not a chat
        chat_fp = self._chats_fp()
        if not chats:
            state = "todo"
        elif f_chat["source"] != "ui" and acks.get("chats") != chat_fp:  # env/default lists are never trusted
            state = "ready"
        else:
            state = "done"
        if tg_in_use:
            steps.append({"id": "chats", "title": "Sohbet kimlikleri", "state": state, "fields": [f_chat], "fp": chat_fp,
                          "late": late,
                          "detail": (f"{len(chats)} sohbet izinli" + (" · onay bekliyor" if state == "ready" else ""))
                          if chats else "İzin listesi boş"})

        f_llm = [self.field("LLM_PROVIDER"), self.field("LLM_BASE_URL"), self.field("LLM_MODEL"), self.field("LLM_API_KEY")]
        tested = llm.get("fp") == rc.fingerprint(s.llm_base_url, s.llm_model, s.llm_api_key)
        filled = bool(s.llm_model) and (bool(s.llm_api_key) or not prov.key)
        state = "done" if tested else ("ready" if filled else "todo")
        steps.append({"id": "llm", "title": "Yapay zekâ modeli", "state": state, "fields": f_llm,
                      "provider": prov.id,
                      "detail": f"{prov.name} · {self._shown_model()}" + (" · test edildi" if tested else
                                                                 " · doğrulanmadı" if filled else " · anahtar yok")})

        f_search = [self.field("SEARCH_PROVIDER"), self.field("TAVILY_API_KEY"), self.field("BRAVE_API_KEY"), self.field("SERPER_API_KEY"),
                    self.field("SEARXNG_URL")]
        ss = self._search_status()
        search_fp = self._search_fp()
        confirmed = (search.get("fp") == search_fp or acks.get("search") == search_fp) and ss["ready"]
        state = "done" if confirmed else ("ready" if ss["ready"] else "todo")
        steps.append({"id": "search", "title": "Web arama", "state": state, "fields": f_search, "optional": True, "fp": search_fp,
                      "detail": s.search_provider + ("" if confirmed else " · onay bekliyor" if ss["ready"] else " · ayar eksik")})

        for x in steps:
            x["done"] = x["state"] == "done"
        done = sum(1 for x in steps if x["done"])
        all_ready = all(x["done"] for x in steps if not x.get("late"))  # a channel added later never reopens a finished setup
        completed = bool(st.get("completed_at")) and all_ready
        steps.append({"id": "summary", "title": "Bitti", "state": "done" if completed else "todo",
                      "done": completed, "detail": "Uygulandı" if completed else "Bekliyor", "fields": []})
        return {"steps": steps, "done": done, "total": len(steps) - 1, "completed": completed,
                "all_ready": all_ready, "completed_at": st.get("completed_at"),
                "channel": chosen, "dismissed": bool(st.get("dismissed_at")), "lang": st.get("lang") if st.get("lang") in ("tr", "en") else None}  # first-run wizard shown once; afterwards the checklist

    # -- routes
    def _build(self) -> APIRouter:  # noqa: C901 - one place for every route keeps the security review easy
        r = APIRouter()
        ai = self.ai
        live = self.live

        # ------------------------------------------------------------ overview
        @r.get("/overview")
        async def overview(user: UIUser = Depends(current_user)):
            s = ai.settings
            st = live.setup.read()
            tg = st.get("telegram") or {}
            token = s.telegram_bot_token
            nxt = ai.db.all_schedules(limit=1)
            next_task = None
            if nxt and nxt[0]["next_run"]:
                next_task = {"id": nxt[0]["id"], "next_run": nxt[0]["next_run"],
                             "label": (nxt[0]["description"] or nxt[0]["text"])[:80]}
            bridges = []
            if ai.bridges is not None:
                for b in ai.bridges.bridges:
                    err = ai.bridges.errors.get(b.name)
                    bridges.append({"name": b.name, "kind": "local" if b.is_local else "remote",
                                    "host": None if b.is_local else _host(b.url),
                                    "status": "error" if err else ("ok" if b.is_local or b._manifest else "unknown"),
                                    "error": err["message"] if err else None})
            llm_test = st.get("llm") or {}
            return {
                "ok": True,
                "stack": {"name": self.stack.name, "version": self.stack.version,
                          "uptime": int(time.time() - ai.started)},
                "telegram": {
                    "enabled": s.telegram_enabled,
                    "configured": bool(token),
                    "running": bool(ai.telegram.running) if ai.telegram else bool(ai.bot and ai.bot.running),
                    "managed": ai.telegram is not None,
                    "username": tg.get("username") if token and tg.get("fp") == rc.fingerprint(token) else None,
                    "allowed": len(s.allowed_chat_ids),
                    "waiting": sum(1 for c in ai.seen_chats if c not in set(s.allowed_chat_ids)),
                },
                "llm": {"host": _host(s.llm_base_url), "model": s.llm_model, "key_set": bool(s.llm_api_key),
                        "tested": llm_test.get("fp") == rc.fingerprint(s.llm_base_url, s.llm_model, s.llm_api_key),
                        "tested_at": llm_test.get("at")},
                "search": self._search_status(),
                "channels": self._channel_summary(),
                "memory": {"total": ai.db.count_all_memories(), "chats": len(ai.db.memory_chats())},
                "tasks": {"active": ai.db.count_all_active_schedules(), "next": next_task, "timezone": s.tz},
                "tools": {"local": len(self.stack.tools)},
                "bridges": bridges,
                "plugin_errors": [redact(json.dumps(e, default=str))[:300] for e in self.stack.plugin_errors],
                "errors": self.logs.tail(min_level=logging.WARNING, limit=6),
                "setup": self.setup_status(),
                "gate": ({k: self.gate.enabled(k) for k in ("bridge", "mcp", "hook")} if self.gate is not None else None),
                "protection": self.ui.protection_enabled if self.ui is not None else True,
            }

        # ------------------------------------------------------------ setup wizard
        @r.get("/setup")
        async def setup(user: UIUser = Depends(current_user)):
            s = ai.settings
            ui = self.ui
            return {"ok": True, **self.setup_status(), "llm_providers": public_providers(),
                    "llm_provider": resolve_provider(s.llm_provider, s.llm_base_url).id,
                    "recent_models": live.setup.read().get("recent_models") or [],
                    "search": self._search_status(), "providers": rc.SEARCH_PROVIDERS,
                    "values": {"LLM_PROVIDER": s.llm_provider, "LLM_BASE_URL": s.llm_base_url, "LLM_MODEL": self._shown_model(),
                               "LLM_ALLOW_PRIVATE_NETWORK": s.llm_allow_private,
                               "SEARCH_PROVIDER": s.search_provider, "SEARXNG_URL": s.searxng_url or ""},
                    "secrets": live.secrets.describe(rc.SECRET_NAMES),
                    "auth": {"mode": ui.config.auth if ui else None,
                             "password": ui.password_source if ui else None,
                             "protection": ui.protection_enabled if ui else True}}

        @r.post("/setup/complete")
        async def setup_complete(user: UIUser = Depends(current_user)):
            result = await live.apply()
            status = self.setup_status()
            if not status["all_ready"]:
                missing = ", ".join(x["title"] for x in status["steps"][:-1] if not x["done"])
                raise _err(409, "setup_incomplete", f"Önce şu adımları tamamlayın: {missing}")
            live.setup.update(completed_at=time.time(), dismissed_at=time.time())
            log.info("setup wizard completed by %s", user.label)
            return {"ok": True, "applied": result, **self.setup_status()}

        @r.post("/setup/search")
        async def setup_search(request: Request, user: UIUser = Depends(current_user)):
            """Save provider (+ key / URL) in one go and mark the step."""
            body = await _body(request)
            provider = _opt_str(body, "provider", 32)
            if provider not in rc.SEARCH_PROVIDERS:
                raise _err(422, "invalid_value", "SEARCH_PROVIDER: one of " + ", ".join(rc.SEARCH_PROVIDERS))
            values: dict[str, Any] = {"SEARCH_PROVIDER": provider}
            if provider == "searxng":
                values["SEARXNG_URL"] = _opt_str(body, "searxng_url", 500) or ""
            secrets: dict[str, str] = {}
            key = _opt_str(body, "api_key")
            if key and provider in ("tavily", "brave", "serper"):
                secrets[{"tavily": "TAVILY_API_KEY", "brave": "BRAVE_API_KEY", "serper": "SERPER_API_KEY"}[provider]] = key
            # refuse before saving anything: a provider without its key would break web.search at once
            s = ai.settings
            if provider == "tavily" and not (key or s.tavily_api_key):
                raise _err(422, "invalid_value", "Tavily için anahtar (TAVILY_API_KEY) gerekli.")
            if provider == "brave" and not (key or s.brave_api_key):
                raise _err(422, "invalid_value", "Brave için anahtar (BRAVE_API_KEY) gerekli.")
            if provider == "serper" and not (key or s.serper_api_key):
                raise _err(422, "invalid_value", "Serper için anahtar (SERPER_API_KEY) gerekli.")
            if provider == "searxng" and not values.get("SEARXNG_URL"):
                raise _err(422, "invalid_value", "SearXNG için sunucu adresi (SEARXNG_URL) gerekli.")
            result = await self._save(values, secrets, user)
            live.setup.update(search={"provider": provider, "fp": self._search_fp(), "at": time.time()})
            return {"ok": True, "applied": result, "search": self._search_status(), **self.setup_status()}

        @r.post("/setup/lang")
        async def setup_lang(request: Request, user: UIUser = Depends(current_user)):
            """Panel language (tr/en), kept on the server so a new browser starts in the same language."""
            body = await _body(request)
            lang = _opt_str(body, "lang", 4)
            if lang not in ("tr", "en"):
                raise _err(422, "invalid_value", "lang: tr or en")
            live.setup.update(lang=lang)
            return {"ok": True, "lang": lang}

        @r.post("/setup/channel")
        async def setup_channel(request: Request, user: UIUser = Depends(current_user)):
            """Wizard: which channel the user wants to talk through; the next step is that channel's setup."""
            body = await _body(request)
            name = _opt_str(body, "channel", 16)
            if name not in self.CHANNEL_CHOICES:
                raise _err(422, "invalid_value", "channel: " + ", ".join(self.CHANNEL_CHOICES))
            live.setup.update(channel=name, channel_at=time.time())
            return {"ok": True, **self.setup_status()}

        @r.post("/setup/dismiss")
        async def setup_dismiss(user: UIUser = Depends(current_user)):
            """"Sonra yap": the first-run wizard stops opening by itself; the Kurulum page keeps the checklist."""
            live.setup.update(dismissed_at=time.time())
            return {"ok": True, **self.setup_status()}

        @r.post("/setup/confirm")
        async def setup_confirm(request: Request, user: UIUser = Depends(current_user)):
            """The user looked at a value that came from the environment (or the default) and accepts it."""
            body = await _body(request)
            step = _opt_str(body, "step", 16)
            seen_fp = _opt_str(body, "fp", 64)  # what the user was looking at
            acks = dict(live.setup.read().get("confirmed") or {})
            if step == "chats":
                if not any(c for c in ai.settings.allowed_chat_ids):
                    raise _err(422, "invalid_value", "İzin listesi boş: önce bir sohbet ekleyin.")
                now_fp = self._chats_fp()
            elif step == "search":
                if not self._search_status()["ready"]:
                    raise _err(422, "invalid_value", "Arama sağlayıcısının ayarı eksik.")
                now_fp = self._search_fp()
            else:
                raise _err(422, "invalid_value", "step: chats or search")
            if seen_fp != now_fp:
                raise _err(409, "stale", "Değerler siz bakarken değişti; sayfayı yenileyip tekrar kontrol edin.")
            acks[step] = now_fp
            live.setup.update(confirmed=acks)
            return {"ok": True, **self.setup_status()}

        # ------------------------------------------------------------ telegram
        @r.post("/telegram/test")
        async def telegram_test(request: Request, user: UIUser = Depends(current_user)):
            body = await _body(request)
            typed = _opt_str(body, "token", 128)
            token = typed or ai.settings.telegram_bot_token
            if not token:
                raise _err(422, "missing", "Önce bir bot token'ı girin.")
            if not TELEGRAM_TOKEN_RE.match(token):
                raise _err(422, "invalid_value", "Bu bir Telegram bot token'ına benzemiyor (123456:ABC… biçimi).")
            register_secret(token)
            info = await self._get_me(token)
            live.setup.update(telegram={"fp": rc.fingerprint(token), "username": info.get("username"),
                                        "at": time.time()})
            return {"ok": True, "bot": {"username": info.get("username"), "name": info.get("first_name"),
                                        "id": info.get("id")},
                    "saved": token == ai.settings.telegram_bot_token}

        @r.get("/telegram/chats")
        async def telegram_chats(user: UIUser = Depends(current_user)):
            allowed = list(ai.settings.allowed_chat_ids)
            seen = sorted(ai.seen_chats.values(), key=lambda c: c["last_seen"], reverse=True)
            return {"ok": True, "allowed": allowed, "source": live.store.resolve("ALLOWED_CHAT_IDS")["source"],
                    "seen": [{**c, "allowed": c["chat_id"] in allowed} for c in seen],
                    "running": bool(ai.telegram and ai.telegram.running) or bool(ai.bot and ai.bot.running)}

        @r.post("/telegram/chats")
        async def add_chat(request: Request, user: UIUser = Depends(current_user)):
            body = await _body(request)
            chat_id = body.get("chat_id")
            if isinstance(chat_id, str) and re.fullmatch(r"-?\d{1,20}", chat_id.strip()):
                chat_id = int(chat_id.strip())
            if not isinstance(chat_id, int) or isinstance(chat_id, bool) or abs(chat_id) >= 10 ** 20:
                raise _err(422, "invalid_value", "chat_id: tam sayı bekleniyor")
            current = list(ai.settings.allowed_chat_ids)
            if chat_id not in current:
                current.append(chat_id)
            result = await self._save({"ALLOWED_CHAT_IDS": ",".join(str(c) for c in current)}, {}, user)
            return {"ok": True, "allowed": list(ai.settings.allowed_chat_ids), "applied": result}

        @r.delete("/telegram/chats/{chat_id}")
        async def remove_chat(chat_id: int, user: UIUser = Depends(current_user)):
            current = [c for c in ai.settings.allowed_chat_ids if c != chat_id]
            result = await self._save({"ALLOWED_CHAT_IDS": ",".join(str(c) for c in current)}, {}, user)
            return {"ok": True, "allowed": list(ai.settings.allowed_chat_ids), "applied": result}

        @r.post("/telegram/restart")
        async def telegram_restart(user: UIUser = Depends(current_user)):
            if ai.telegram is None:
                raise _err(409, "not_managed", "Telegram botu bu serviste başka bir modüle ait; servisi yeniden başlatın.")
            if not ai.settings.telegram_enabled:
                raise _err(409, "disabled", "Telegram kapalı; önce kanalı açın.")
            started = await ai.telegram.restart()
            log.info("Telegram polling restarted from the admin UI by %s", user.label)
            return {"ok": True, "running": started}

        @r.get("/telegram")
        async def telegram_status(user: UIUser = Depends(current_user)):
            s = ai.settings
            st = live.setup.read().get("telegram") or {}
            token = s.telegram_bot_token
            bot = ai.bot
            errors = [x for x in self.logs.tail(min_level=logging.WARNING, limit=400)
                      if x["logger"] in ("stack_kit.telegram", "ai_bot.telegram")][:8]
            commands = [{"name": c.name, "help": c.help} for c in sorted(self.stack.commands.values(), key=lambda c: c.name)]
            return {"ok": True,
                    "enabled": s.telegram_enabled,
                    "token": self.field("TELEGRAM_BOT_TOKEN"),
                    "configured": bool(token or (bot and bot.token)),
                    "running": bool(ai.telegram.running) if ai.telegram else bool(bot and bot.running),
                    "managed": ai.telegram is not None,
                    "bot": ({"username": st.get("username"), "tested_at": st.get("at")}
                            if token and st.get("fp") == rc.fingerprint(token) else None),
                    "allowed": list(s.allowed_chat_ids), "allowed_source": self.field("ALLOWED_CHAT_IDS")["source"],
                    "confirm_timeout": s.confirm_timeout, "commands": commands,
                    "commands_synced_at": live.setup.read().get("commands_synced_at"),
                    "errors": errors}

        @r.post("/telegram/commands/sync")
        async def telegram_commands_sync(user: UIUser = Depends(current_user)):
            """Publish the command list to Telegram (setMyCommands), like the bot does at start-up."""
            bot = ai.bot
            if bot is None or not bot.token:
                raise _err(409, "telegram_off", "Önce bot token'ını kaydedin.")
            cmds = [{"command": c.name, "description": (c.help or c.name)[:256]} for c in self.stack.commands.values()]
            for builtin, desc in (("help", "Komutlar / commands"), ("id", "Chat ID")):
                if builtin not in self.stack.commands:
                    cmds.append({"command": builtin, "description": desc})
            try:
                await bot.api("setMyCommands", commands=cmds[:100])
            except Exception as exc:  # TelegramAPIError text is already redacted; never include the URL
                raise _err(424, "telegram_error", f"Telegram komut listesini kabul etmedi ({type(exc).__name__}).") from None
            live.setup.update(commands_synced_at=time.time())
            log.info("Telegram command list synced from the admin UI by %s", user.label)
            return {"ok": True, "count": len(cmds[:100])}

        # ------------------------------------------------------------ llm
        @r.get("/llm/providers")
        async def llm_providers(user: UIUser = Depends(current_user)):
            s = ai.settings
            return {"ok": True, "providers": public_providers(),
                    "current": {"provider": resolve_provider(s.llm_provider, s.llm_base_url).id,
                                "setting": s.llm_provider, "base_url": s.llm_base_url, "model": s.llm_model,
                                "key": self.field("LLM_API_KEY")},
                    "recent_models": live.setup.read().get("recent_models") or []}

        async def _llm_target(body: dict) -> tuple:
            """Provider, base URL (cloud presets locked to their host), key and private-network flag, after
            the network policy check (link-local, metadata, 0.0.0.0, this service and panel paths refused)."""
            s = ai.settings
            pid = _opt_str(body, "provider", 32)
            if pid is not None and pid not in BY_ID and pid != "auto":
                raise _err(422, "invalid_value", "bilinmeyen sağlayıcı")
            typed_base = (_opt_str(body, "base_url", 500) or "").rstrip("/")
            prov = resolve_provider(pid or s.llm_provider, typed_base or s.llm_base_url)
            try:
                base = effective_base(prov, typed_base or (s.llm_base_url if not pid or pid == s.llm_provider
                                                           or resolve_provider(s.llm_provider, s.llm_base_url).id == prov.id
                                                           else prov.base_url))
            except NetBlocked as exc:
                raise _err(422, "url_locked", str(exc)) from None
            if not re.fullmatch(rc.URL_PATTERN, base or ""):
                raise _err(422, "invalid_value", "Adres http:// veya https:// ile başlamalı.")
            allow_private = body.get("allow_private")
            allow_private = s.llm_allow_private if allow_private is None else allow_private is True
            try:
                await policy_for(prov, allow_private_custom=allow_private, resolver=ai.resolver).check(base)
            except NetBlocked as exc:
                hint = " Yerel/özel ağdaki bir sunucu için 'özel ağ' kutusunu işaretleyin." if prov.custom and not allow_private \
                    and "özel ağ" in str(exc) else ""
                raise _err(422, "url_blocked", f"Adres engellendi: {exc}.{hint}") from None
            typed_key = _opt_str(body, "api_key")
            # the SAVED key only goes to the saved host: a typed URL cannot make the server leak it elsewhere
            same_host = (_host(base) or "") == (_host(s.llm_base_url) or "")
            key = typed_key or (s.llm_api_key if same_host else None)
            register_secret(key)
            return prov, base, key, allow_private

        @r.post("/llm/models")
        async def llm_models(request: Request, user: UIUser = Depends(current_user)):
            """Fetch the provider's model list (the key may be typed and not saved yet; it is never echoed)."""
            body = await _body(request)
            prov, base, key, allow_private = await _llm_target(body)
            try:
                models = await list_models(prov, base, key, transport=ai.llm_transport,
                                           allow_private_custom=allow_private, resolver=ai.resolver)
            except NetBlocked as exc:
                raise _err(422, "url_blocked", f"Adres engellendi: {exc}.") from None
            except LLMError as exc:
                raise _err(424, "models_failed", redact(friendly_error(exc, prov))[:200]) from None
            return {"ok": True, "provider": prov.id, "count": len(models), "models": [m.public() for m in models],
                    "recent": live.setup.read().get("recent_models") or []}

        @r.post("/llm/test")
        async def llm_test(request: Request, user: UIUser = Depends(current_user)):
            body = await _body(request)
            s = ai.settings
            prov, base, key, allow_private = await _llm_target(body)
            model = _opt_str(body, "model", 200) or s.llm_model
            if not re.fullmatch(r"[^\s]{1,200}", model):
                raise _err(422, "invalid_value", "Geçersiz model adı.")
            client = make_client(prov.id, base, key, timeout=min(max(s.llm_timeout, 5), 45), retries=0,
                                 transport=ai.llm_transport, allow_private_custom=allow_private, resolver=ai.resolver)
            started = time.monotonic()
            try:
                msg = await client.chat(model=model, messages=[{"role": "user", "content": "Reply with the word: ok"}],
                                        max_tokens=16, temperature=0)
            except LLMError as exc:
                raise _err(424, "llm_failed", redact(friendly_error(exc, prov))[:300]) from None
            finally:
                await client.aclose()
            ms = int((time.monotonic() - started) * 1000)
            saved = base == s.llm_base_url.rstrip("/") and model == s.llm_model and key == s.llm_api_key
            if saved:
                live.setup.update(llm={"fp": rc.fingerprint(base, model, key), "at": time.time(), "model": model})
            reply = re.sub(r"[\x00-\x1f\x7f-\x9f<>]", "", redact(str(msg.get("content") or "").strip()))[:80]
            return {"ok": True, "provider": prov.id, "model": model, "latency_ms": ms, "reply": reply, "saved": saved}

        @r.post("/llm/save")
        async def llm_save(request: Request, user: UIUser = Depends(current_user)):
            """Save provider + base URL + model (+ key) in one go and remember the model as recent."""
            body = await _body(request)
            pid = _opt_str(body, "provider", 32) or "auto"
            if pid not in BY_ID and pid != "auto":
                raise _err(422, "invalid_value", "bilinmeyen sağlayıcı")
            prov, base, _key, allow_private = await _llm_target({**body, "provider": pid})
            model = _opt_str(body, "model", 200)
            if not model:
                raise _err(422, "invalid_value", "LLM_MODEL: model seçin veya yazın")
            values = {"LLM_PROVIDER": pid, "LLM_BASE_URL": base, "LLM_MODEL": model}
            if prov.custom:
                values["LLM_ALLOW_PRIVATE_NETWORK"] = bool(allow_private)
            secrets = {}
            key = _opt_str(body, "api_key")
            if key:
                secrets["LLM_API_KEY"] = key
            result = await self._save(values, secrets, user)
            recent = [m for m in (live.setup.read().get("recent_models") or []) if m != model]
            live.setup.update(recent_models=[model] + recent[:9])
            return {"ok": True, "applied": result}

        # ------------------------------------------------------------ search
        @r.post("/search/test")
        async def search_test(user: UIUser = Depends(current_user)):
            started = time.monotonic()
            try:
                results = await ai.search.search("Komuta cloud platform", 3)
            except ToolError as exc:
                if exc.code == "search_unavailable" and ai.settings.search_provider == "auto":
                    raise _err(424, "search_blocked", "Hiçbir ücretsiz arama kanalı yanıt vermedi (DuckDuckGo bot korumasında, herkese "
                               "açık sunucular engelliyor veya Parallel hız sınırında olabilir). Güvenilir arama için ücretsiz bir "
                               "Tavily veya Serper anahtarı ekleyin.") from None
                if exc.code == "search_unavailable":  # the provider blocks automated requests from this server
                    raise _err(424, "search_blocked", "DuckDuckGo bu sunucudan gelen otomatik aramayı bot koruması "
                               "olarak reddetti (bulut sunucularında sık görülür). Güvenilir arama için ücretsiz bir "
                               "Tavily anahtarı alın (tavily.com) veya Brave / SearXNG kullanın.") from None
                raise _err(424, "search_failed", redact(exc.message)[:300]) from None
            live.setup.update(search={"provider": ai.settings.search_provider, "fp": self._search_fp(), "at": time.time()})
            return {"ok": True, "provider": ai.settings.search_provider, "via": ai.search.last_via, "count": len(results),
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "results": [{"title": x["title"][:120], "url": x["url"][:300]} for x in results[:3]]}

        # ------------------------------------------------------------ settings
        @r.get("/config")
        async def get_config(user: UIUser = Depends(current_user)):
            items = []
            for setting in rc.SCHEMA:
                info = live.store.resolve(setting.name)
                items.append({**info, "group": rc.GROUP_OF[setting.name], "schema": setting.schema(),
                              "live": True, "env_set": env_str(setting.env or setting.name) is not None})
            return {"ok": True, "settings": items, "groups": rc.GROUPS, "locks": rc.SECURITY_LOCKS,
                    "secrets": [self.field(n) for n in rc.SECRET_NAMES], "applied_at": live.applied_at}

        @r.get("/config/audit")
        async def config_audit(limit: int = 50, user: UIUser = Depends(current_user)):
            return {"ok": True, "audit": live.store.audit(max(1, min(limit, 200)))}

        @r.post("/config/batch")
        async def config_batch(request: Request, user: UIUser = Depends(current_user)):
            body = await _body(request)
            values = body.get("values") or {}
            secrets = body.get("secrets") or {}
            if not isinstance(values, dict) or not isinstance(secrets, dict):
                raise _err(400, "bad_request", "values and secrets must be objects")
            result = await self._save(values, {k: v for k, v in secrets.items() if v not in (None, "")}, user)
            return {"ok": True, "applied": result}

        @r.put("/config/{name}")
        async def put_config(name: str, request: Request, user: UIUser = Depends(current_user)):
            body = await _body(request)
            result = await self._save({name: body.get("value")}, {}, user)
            return {"ok": True, "setting": live.store.resolve(name), "applied": result}

        @r.delete("/config/{name}")
        async def reset_config(name: str, user: UIUser = Depends(current_user)):
            if name not in live.store.schema:
                raise _err(404, "not_found", "bu ayar arayüzden değiştirilemez")
            live.store.reset(name, actor=user.label)
            result = await live.apply()
            return {"ok": True, "setting": live.store.resolve(name), "applied": result}

        @r.get("/secrets")
        async def get_secrets(user: UIUser = Depends(current_user)):
            return {"ok": True, "secrets": [self.field(n) for n in rc.SECRET_NAMES]}

        @r.put("/secrets/{name}")
        async def put_secret(name: str, request: Request, user: UIUser = Depends(current_user)):
            body = await _body(request)
            result = await self._save({}, {name: body.get("value")}, user)
            return {"ok": True, "secret": live.secrets.describe([name])[0], "applied": result}

        @r.delete("/secrets/{name}")
        async def delete_secret(name: str, user: UIUser = Depends(current_user)):
            if name not in rc.SECRET_NAMES:
                raise _err(404, "not_found", "unknown secret")
            live.secrets.delete(name)
            log.info("UI secret %s removed by %s", name, user.label)
            result = await live.apply()
            return {"ok": True, "secret": live.secrets.describe([name])[0], "applied": result}

        @r.post("/apply")
        async def apply(user: UIUser = Depends(current_user)):
            return {"ok": True, "applied": await live.apply()}

        # ------------------------------------------------------------ memory
        @r.get("/memory/chats")
        async def memory_chats(user: UIUser = Depends(current_user)):
            allowed = set(ai.settings.allowed_chat_ids)
            chats = [{**c, "allowed": c["chat_id"] in allowed} for c in ai.db.memory_chats()]
            return {"ok": True, "chats": chats}

        @r.get("/memory")
        async def memory_list(chat_id: int, q: str = "", limit: int = 50, offset: int = 0,
                              user: UIUser = Depends(current_user)):
            limit = max(1, min(limit, 100))
            if q.strip():
                items = ai.memory.recall(chat_id, q[:400], min(limit, 20))
                return {"ok": True, "items": items, "total": len(items), "query": q}
            return {"ok": True, **ai.memory.list(chat_id, limit, max(0, offset))}

        @r.delete("/memory/{chat_id}/{memory_id}")
        async def memory_delete(chat_id: int, memory_id: int, user: UIUser = Depends(current_user)):
            try:
                ai.memory.forget(chat_id, memory_id)
            except ToolError as exc:
                raise _err(404, "not_found", exc.message) from None
            log.info("memory #%s of a chat deleted from the admin UI by %s", memory_id, user.label)
            return {"ok": True}

        @r.delete("/memory/{chat_id}")
        async def memory_clear(chat_id: int, user: UIUser = Depends(current_user)):
            n = ai.db.clear_memories(chat_id)
            log.info("%s memories of a chat cleared from the admin UI by %s", n, user.label)
            return {"ok": True, "deleted": n}

        # ------------------------------------------------------------ tasks
        @r.get("/tasks")
        async def tasks(include_inactive: bool = False, user: UIUser = Depends(current_user)):
            rows = ai.db.all_schedules(include_inactive=include_inactive)
            out = [{"id": t["id"], "chat_id": t["chat_id"], "kind": t["kind"], "text": t["text"][:500],
                    "description": t["description"], "cron": t["cron"], "next_run": t["next_run"],
                    "last_run": t["last_run"], "runs": t["runs"], "active": bool(t["active"]),
                    "created": t["created"]} for t in rows]
            return {"ok": True, "tasks": out, "timezone": ai.settings.tz}

        @r.delete("/tasks/{task_id}")
        async def task_cancel(task_id: int, user: UIUser = Depends(current_user)):
            row = ai.db.get_schedule(task_id)
            if row is None or not row["active"]:
                raise _err(404, "not_found", "aktif görev yok")
            ai.db.cancel_schedule(row["chat_id"], task_id)
            log.info("task #%s cancelled from the admin UI by %s", task_id, user.label)
            return {"ok": True}

        # ------------------------------------------------------------ tools & bridges
        @r.get("/tools")
        async def tools(user: UIUser = Depends(current_user)):
            local = []
            for t in self.stack.tools.values():
                module = getattr(t.func, "__module__", "") or ""
                local.append({"name": t.name, "description": t.description[:300], "scope": t.scope,
                              "source": "builtin" if module.startswith("ai_bot") else "plugin",
                              "needs_confirmation": bool(t.confirm or t.scope in HUMAN_SCOPES),
                              "hidden_from_ai": t.name in HIDDEN_FROM_AI})
            remote: list[dict] = []
            bridges = []
            if ai.bridges is not None and ai.bridges.bridges:
                try:
                    for t in await ai.bridges.tools():
                        if t.get("name") in self.stack.tools:
                            continue
                        scope = str(t.get("scope") or "read")
                        remote.append({"name": str(t.get("name")), "description": str(t.get("description") or "")[:300],
                                       "scope": scope, "source": "bridge", "bridge": t.get("bridge"),
                                       "needs_confirmation": bool(t.get("confirm")) or scope in HUMAN_SCOPES,
                                       "hidden_from_ai": False})
                except BridgeError as exc:
                    log.warning("bridge discovery failed: %s", exc.message)
                for b in ai.bridges.bridges:
                    err = ai.bridges.errors.get(b.name)
                    bridges.append({"name": b.name, "kind": "local" if b.is_local else "remote",
                                    "host": None if b.is_local else _host(b.url),
                                    "status": "error" if err else "ok", "error": err["message"] if err else None,
                                    "tools": sum(1 for t in remote if t.get("bridge") == b.name)})
            return {"ok": True, "local": local, "remote": remote, "bridges": bridges,
                    "plugins": list(self.stack.loaded_plugins),
                    "plugin_errors": [redact(json.dumps(e, default=str))[:300] for e in self.stack.plugin_errors]}

        @r.post("/bridges/test")
        async def bridges_test(user: UIUser = Depends(current_user)):
            if ai.bridges is None or not ai.bridges.bridges:
                return {"ok": True, "bridges": []}
            ai.bridges.invalidate()
            try:
                found = await ai.bridges.tools(refresh=True)
            except BridgeError as exc:
                raise _err(424, exc.code, redact(exc.message)[:300]) from None
            out = []
            for b in ai.bridges.bridges:
                err = ai.bridges.errors.get(b.name)
                out.append({"name": b.name, "kind": "local" if b.is_local else "remote",
                            "host": None if b.is_local else _host(b.url), "status": "error" if err else "ok",
                            "error": err["message"] if err else None,
                            "tools": sum(1 for t in found if t.get("bridge") == b.name)})
            return {"ok": True, "bridges": out}

        # ------------------------------------------------------------ log
        @r.get("/logs")
        async def logs(level: str = "info", limit: int = 200, user: UIUser = Depends(current_user)):
            min_level = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}.get(level.lower(),
                                                                                                     logging.INFO)
            log_rows = self.logs.tail(min_level=min_level, limit=max(1, min(limit, 400)))
            ev = self.stack.event_log
            events = []
            for e in ev.since(max(0, ev.last_id - 60), 60)[::-1]:
                data = e.get("data")
                if isinstance(data, dict):
                    data = {k: v for k, v in data.items() if k not in EVENT_HIDDEN_KEYS}
                events.append({"id": e.get("id"), "ts": e.get("ts"), "event": e.get("event"),
                               "data": redact(json.dumps(data, ensure_ascii=False, default=str))[:300]})
            return {"ok": True, "logs": log_rows, "events": events}

        from .panel_chat import build_router as _chat_router
        r.include_router(_chat_router(self, _err, _body))
        from .panel_channels import build_router as _channels_router
        r.include_router(_channels_router(self, _err, _body))
        return r

    # -- actions
    async def _get_me(self, token: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=12.0, transport=self.transport) as client:
                resp = await client.post(f"{self.telegram_api_base}/bot{token}/getMe")
        except httpx.TimeoutException:
            raise _err(424, "telegram_unreachable", "Telegram'a ulaşılamadı (zaman aşımı).") from None
        except httpx.HTTPError as exc:
            raise _err(424, "telegram_unreachable", f"Telegram'a ulaşılamadı ({type(exc).__name__}).") from None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if resp.status_code in (401, 404) or data.get("error_code") in (401, 404):
            raise _err(422, "telegram_rejected", "Telegram bu token'ı reddetti. BotFather'dan yeniden kopyalayın.")
        if resp.status_code >= 400 or not data.get("ok") or not isinstance(data.get("result"), dict):
            raise _err(424, "telegram_error", f"Telegram beklenmeyen yanıt verdi (HTTP {resp.status_code}).")
        return data["result"]

    async def _save(self, values: dict, secrets: dict, user: UIUser) -> dict:
        """Validate everything first, then store, then apply once."""
        store = self.live.store
        coerced: dict[str, Any] = {}
        for name, value in values.items():
            spec = store.schema.get(name)
            if spec is None:
                raise _err(404, "not_found", f"{name} arayüzden değiştirilemez (güvenlik veya bilinmeyen ayar)")
            try:
                coerced[name] = spec.coerce(value)
                rc.validate_extra(name, coerced[name])
            except ValueError as exc:
                raise _err(422, "invalid_value", str(exc)) from None
        for name, value in secrets.items():
            if name not in rc.SECRET_NAMES:
                raise _err(404, "not_found", "unknown secret")
            if not isinstance(value, str) or not value.strip() or len(value) > 8192:
                raise _err(422, "invalid_value", f"{name}: boş olmayan metin bekleniyor")
            if name == "TELEGRAM_BOT_TOKEN" and not TELEGRAM_TOKEN_RE.match(value.strip()):
                raise _err(422, "invalid_value", "Bu bir Telegram bot token'ına benzemiyor (123456:ABC… biçimi).")
            if name != "TELEGRAM_BOT_TOKEN" and len(value.strip()) < 8:
                raise _err(422, "invalid_value", f"{name}: çok kısa")
        for name, value in coerced.items():
            store.set(name, value, actor=user.label)
        for name, value in secrets.items():
            self.live.secrets.set(name, value)
            log.info("UI secret %s updated by %s", name, user.label)
        return await self.live.apply()


# --------------------------------------------------------------------------- mount


def mount_panel(fastapi_app: FastAPI, ai: "AiBot", live: rc.LiveConfig, *, title: str = "ai-bot",
                config: UIConfig | None = None, http_transport: httpx.AsyncBaseTransport | None = None,
                telegram_api_base: str = "https://api.telegram.org") -> UIRuntime:
    """Mount ``/ui`` (pages in ``src/ai_bot/ui``) and its API on ``fastapi_app``.

    Defaults: ``default_auth="password"`` — the panel needs a password; on the very first start a one-time
    setup code is written to the log and ``/ui/setup`` asks for it plus a new password (``UI_PASSWORD`` /
    ``UI_SETUP_CODE`` env win). ``UI_AUTH=auto|komuta`` switches to Komuta identity. Login/setup/no-access
    pages are the kit's (compact cards via ``login_card_width``/``setup_card_width``).
    """
    panel = Panel(ai, live, http_transport=http_transport, telegram_api_base=telegram_api_base, app=fastapi_app)
    fastapi_app.state.ai_panel = panel
    return mount_ui(fastapi_app, ai.stack, title=title, static_dir=UI_DIR, api_router=panel.router, config=config,
                    default_auth="password", login_card_width=460, setup_card_width=460,
                    setup_hint="Kurulum ve iki kapı modeli: docs/ARAYUZ.md")
