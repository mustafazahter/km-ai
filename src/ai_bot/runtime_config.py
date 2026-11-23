"""Settings saved in the admin UI, layered over the environment, applied while the bot runs.

Precedence (documented in docs/ARAYUZ.md)::

    value saved in /ui  >  environment variable  >  built-in default

* :data:`SCHEMA` lists the only settings the UI may override. Security locks (the confirmation rule,
  SSRF protection, web.read ports/redirects/size, "empty ALLOWED_CHAT_IDS disables chat tools", the
  prompt-injection lock) are deliberately **not** in it, so no UI call can turn them off.
* :data:`SECRET_NAMES` are write-only values kept by ``stack_kit.ui.SecretStore`` (file mode 600);
  they are never sent back to the browser (only ``…last4``).
* :class:`LiveConfig` rebuilds :class:`~ai_bot.config.Settings` from both layers and applies the
  difference to the running :class:`~ai_bot.core.AiBot` (no restart for anything in the schema).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stack_kit import register_secret
from stack_ui import SecretStore, Setting, SettingsStore

from .config import Settings
from .providers import PROVIDER_IDS

if TYPE_CHECKING:
    from .core import AiBot

log = logging.getLogger("ai_bot.config")

SEARCH_PROVIDERS = ["auto", "parallel", "duckduckgo", "tavily", "brave", "serper", "searxng"]
CHAT_IDS_PATTERN = r"(-?\d{1,20}(\s*,\s*-?\d{1,20})*)?"
URL_PATTERN = r"https?://[^\s]{3,500}"

# group, label (TR) — the UI renders them in this order
GROUPS = {
    "llm": "Model",
    "agent": "Yanıt ve araç döngüsü",
    "search": "Arama",
    "memory": "Hafıza ve görevler",
    "telegram": "Telegram",
}

SCHEMA: list[Setting] = [
    Setting("LLM_PROVIDER", "choice", "auto", "Yapay zekâ sağlayıcısı (auto = adresten anlaşılır)",
            choices=["auto"] + PROVIDER_IDS),
    Setting("LLM_BASE_URL", "str", "https://openrouter.ai/api/v1", "OpenAI uyumlu /chat/completions adresi",
            pattern=URL_PATTERN, max_length=500),
    Setting("LLM_ALLOW_PRIVATE_NETWORK", "bool", False,
            "Özel sağlayıcı yerel/özel ağ adreslerine gidebilir (link-local, metadata ve bu servis yine engelli)"),
    Setting("LLM_MODEL", "str", "openai/gpt-4o-mini", "Varsayılan model adı", pattern=r"[^\s]{1,200}", max_length=200),
    Setting("LLM_MODEL_CHOICES", "str", "", "/model komutunda izinli modeller (virgüllü; boş = serbest)",
            pattern=r"[^\s,]+(\s*,\s*[^\s,]+)*|", max_length=1000),
    Setting("LLM_TEMPERATURE", "float", 0.3, "Yaratıcılık (0 = kararlı)", min=0, max=2),
    Setting("LLM_MAX_TOKENS", "int", 1024, "Bir yanıtın en fazla token sayısı", min=64, max=16384),
    Setting("LLM_TIMEOUT", "float", 60.0, "Model isteği zaman aşımı (sn)", min=5, max=600),
    Setting("MAX_TOOL_STEPS", "int", 6, "Bir turda en fazla araç adımı", min=1, max=20),
    Setting("TURN_TIMEOUT", "float", 180.0, "Bir turun toplam süre sınırı (sn)", min=10, max=900),
    Setting("HISTORY_MESSAGES", "int", 12, "Bağlama eklenen son mesaj sayısı", min=0, max=100),
    Setting("SYSTEM_PROMPT_EXTRA", "str", "", "Genel sistem istemi (tüm kanallarda ve panel sohbetinde)", max_length=4000),
    Setting("SYSTEM_PROMPT_TELEGRAM", "str", "", "Telegram'a özel sistem istemi", max_length=4000),
    Setting("SYSTEM_PROMPT_WHATSAPP", "str", "", "WhatsApp'a özel sistem istemi", max_length=4000),
    Setting("SYSTEM_PROMPT_TEAMS", "str", "", "Microsoft Teams'e özel sistem istemi", max_length=4000),
    Setting("SYSTEM_PROMPT_SLACK", "str", "", "Slack'e özel sistem istemi", max_length=4000),
    Setting("SYSTEM_PROMPT_EMAIL", "str", "", "E-postaya özel sistem istemi", max_length=4000),
    Setting("TZ", "str", "UTC", "Saat dilimi (IANA, örn. Europe/Istanbul)", pattern=r"[A-Za-z0-9_+\-/]{1,64}",
            max_length=64),
    Setting("SEARCH_PROVIDER", "choice", "auto", "Web arama sağlayıcısı", choices=SEARCH_PROVIDERS),
    Setting("SEARCH_MAX_RESULTS", "int", 5, "Aramada en fazla sonuç", min=1, max=10),
    Setting("SEARXNG_URL", "str", "", "Kendi SearXNG sunucunuzun adresi", pattern=URL_PATTERN + "|", max_length=500),
    Setting("WEB_READ_MAX_CHARS", "int", 8000, "web.read'in modele verdiği en fazla karakter", min=500, max=50000),
    Setting("MEMORY_CONTEXT_LIMIT", "int", 6, "Her mesaja eklenen anı sayısı", min=0, max=30),
    Setting("MEMORY_MAX_PER_CHAT", "int", 500, "Sohbet başına en fazla anı", min=10, max=5000),
    Setting("SCHEDULE_MAX_PER_CHAT", "int", 50, "Sohbet başına en fazla aktif görev", min=1, max=500),
    Setting("SCHEDULE_MIN_INTERVAL_MINUTES", "int", 5, "Tekrarlı görevler arası en kısa süre (dk)", min=5, max=1440),
    Setting("TELEGRAM_ENABLED", "bool", True, "Telegram kanalı açık (kapalıyken bot mesaj dinlemez)"),
    Setting("CONFIRM_TIMEOUT", "float", 120.0, "Telegram onay düğmesinin bekleme süresi (sn); dolunca işlem yapılmaz",
            min=15, max=600),
    Setting("ALLOWED_CHAT_IDS", "str", "", "Botu kullanabilen sohbet kimlikleri (virgüllü)",
            pattern=CHAT_IDS_PATTERN, max_length=2000),
]

GROUP_OF = {
    "LLM_PROVIDER": "llm", "LLM_BASE_URL": "llm", "LLM_ALLOW_PRIVATE_NETWORK": "llm", "LLM_MODEL": "llm", "LLM_MODEL_CHOICES": "llm", "LLM_TEMPERATURE": "llm",
    "LLM_MAX_TOKENS": "llm", "LLM_TIMEOUT": "llm",
    "MAX_TOOL_STEPS": "agent", "TURN_TIMEOUT": "agent", "HISTORY_MESSAGES": "agent", "SYSTEM_PROMPT_EXTRA": "agent",
    "SYSTEM_PROMPT_TELEGRAM": "agent", "SYSTEM_PROMPT_WHATSAPP": "agent", "SYSTEM_PROMPT_TEAMS": "agent",
    "SYSTEM_PROMPT_SLACK": "agent", "SYSTEM_PROMPT_EMAIL": "agent",
    "TZ": "agent",
    "SEARCH_PROVIDER": "search", "SEARCH_MAX_RESULTS": "search", "SEARXNG_URL": "search", "WEB_READ_MAX_CHARS": "search",
    "MEMORY_CONTEXT_LIMIT": "memory", "MEMORY_MAX_PER_CHAT": "memory", "SCHEDULE_MAX_PER_CHAT": "memory",
    "SCHEDULE_MIN_INTERVAL_MINUTES": "memory",
    "CONFIRM_TIMEOUT": "telegram", "TELEGRAM_ENABLED": "telegram", "ALLOWED_CHAT_IDS": "telegram",
}

SECRET_NAMES = ["TELEGRAM_BOT_TOKEN", "LLM_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY", "SERPER_API_KEY"]
SECRET_FIELDS = {"TELEGRAM_BOT_TOKEN": "telegram_bot_token", "LLM_API_KEY": "llm_api_key",
                 "TAVILY_API_KEY": "tavily_api_key", "BRAVE_API_KEY": "brave_api_key",
                 "SERPER_API_KEY": "serper_api_key"}


def _chat_ids(value: str) -> list[int]:
    return [int(x) for x in (p.strip() for p in (value or "").split(",")) if x]


def _model_choices(value: str) -> list[str]:
    return [x for x in (p.strip() for p in (value or "").split(",")) if x]


# setting name -> (Settings field, converter)
FIELD_MAP: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "LLM_PROVIDER": ("llm_provider", lambda v: str(v).lower()),
    "LLM_ALLOW_PRIVATE_NETWORK": ("llm_allow_private", bool),
    "LLM_BASE_URL": ("llm_base_url", lambda v: str(v).rstrip("/")),
    "LLM_MODEL": ("llm_model", str),
    "LLM_MODEL_CHOICES": ("llm_model_choices", _model_choices),
    "LLM_TEMPERATURE": ("llm_temperature", float),
    "LLM_MAX_TOKENS": ("llm_max_tokens", int),
    "LLM_TIMEOUT": ("llm_timeout", float),
    "MAX_TOOL_STEPS": ("max_tool_steps", int),
    "TURN_TIMEOUT": ("turn_timeout", float),
    "HISTORY_MESSAGES": ("history_messages", int),
    "SYSTEM_PROMPT_EXTRA": ("system_prompt_extra", str),
    "TELEGRAM_ENABLED": ("telegram_enabled", bool),
    "SYSTEM_PROMPT_TELEGRAM": ("system_prompt_telegram", str),
    "SYSTEM_PROMPT_WHATSAPP": ("system_prompt_whatsapp", str),
    "SYSTEM_PROMPT_TEAMS": ("system_prompt_teams", str),
    "SYSTEM_PROMPT_SLACK": ("system_prompt_slack", str),
    "SYSTEM_PROMPT_EMAIL": ("system_prompt_email", str),
    "TZ": ("tz", str),
    "SEARCH_PROVIDER": ("search_provider", lambda v: str(v).lower()),
    "SEARCH_MAX_RESULTS": ("search_max_results", int),
    "SEARXNG_URL": ("searxng_url", lambda v: (str(v).rstrip("/") or None)),
    "WEB_READ_MAX_CHARS": ("web_read_max_chars", int),
    "MEMORY_CONTEXT_LIMIT": ("memory_context_limit", int),
    "MEMORY_MAX_PER_CHAT": ("memory_max_per_chat", int),
    "SCHEDULE_MAX_PER_CHAT": ("schedule_max_per_chat", int),
    "SCHEDULE_MIN_INTERVAL_MINUTES": ("schedule_min_interval_minutes", int),
    "CONFIRM_TIMEOUT": ("confirm_timeout", float),
    "ALLOWED_CHAT_IDS": ("allowed_chat_ids", _chat_ids),
}

# Messaging channels (WhatsApp, Teams, Slack, e-mail): names live in channels/schema.py. They are NOT
# Settings dataclass fields: ChannelManager reads them from the store/secret store itself (same
# precedence: panel > env > default), so the values stay live without touching Settings.
from .channels import schema as _channels  # noqa: E402

GROUPS[_channels.GROUP] = _channels.GROUP_LABEL
SCHEMA.extend(_channels.SCHEMA)
GROUP_OF.update({n: _channels.GROUP for n in _channels.NAMES})
SECRET_NAMES.extend(_channels.SECRET_NAMES)
SECRET_FIELDS.update({n: "ch_" + n.lower() for n in _channels.SECRET_NAMES})
FIELD_MAP.update({n: ("ch_" + n.lower(), lambda v: v) for n in _channels.NAMES})
assert set(FIELD_MAP) == {s.name for s in SCHEMA} == set(GROUP_OF)

# Read-only facts shown in the UI. They are code, not settings: nothing here can be switched off.
SECURITY_LOCKS = [
    {"id": "confirm", "title": "İnsan onayı",
     "text": "Para/işlem (trade, admin) ve confirm=True araçlar Telegram'da düğmeyle onaylanmadan çalışmaz; "
             "model confirm_id göremez, onay veremez."},
    {"id": "ssrf", "title": "SSRF koruması",
     "text": "web.read özel, loopback ve link-local adreslere gidemez; yönlendirmeler, port, boyut ve süre sınırlı."},
    {"id": "allowlist", "title": "İzin listesi",
     "text": "ALLOWED_CHAT_IDS boşken sohbet araçları (hafıza, görev, ai.ask) kapalıdır; izinsiz sohbetler yalnız "
             "/id ve /start yanıtı alır."},
    {"id": "injection", "title": "İstem enjeksiyonu kilidi",
     "text": "Web içeriği okunan turda hafızaya yazma ve görev ekleme onay ister; zamanlanmış turlar yeni görev kuramaz."},
    {"id": "secrets", "title": "Gizli değerler",
     "text": "Token ve anahtarlar yalnız yazılır, geri gösterilmez (yalnız …son4), log'da maskelenir."},
]


def validate_extra(name: str, value: Any) -> None:
    """Checks the generic :class:`Setting` cannot express. Raises ``ValueError``."""
    if name in ("LLM_BASE_URL", "SEARXNG_URL") and value:
        from .netpolicy import NetBlocked, NetPolicy
        try:
            NetPolicy(allow_private=True).check_static(str(value))  # full IP check happens on every request
        except NetBlocked as exc:
            raise ValueError(f"{name}: {exc}") from None
    if name == "TZ":
        try:
            ZoneInfo(str(value))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("TZ: unknown time zone (use an IANA name such as Europe/Istanbul)") from None


def fingerprint(*parts: str | None) -> str:
    """Short one-way tag of values (used to know whether a test result still matches the config)."""
    h = hashlib.sha256()
    for p in parts:
        h.update(b"\0" + (p or "").encode())
    return h.hexdigest()[:16]


def load_settings(settings_store: SettingsStore | None = None, secrets: SecretStore | None = None,
                  base: Settings | None = None) -> Settings:
    """Environment settings with the UI layer on top (UI value > env > default)."""
    s = base if base is not None else Settings.from_env()
    if settings_store is not None:
        for name, (attr, conv) in FIELD_MAP.items():
            info = settings_store.resolve(name)
            if info["source"] != "ui":
                continue
            try:
                setattr(s, attr, conv(info["value"]))
            except (TypeError, ValueError):
                log.warning("ignoring invalid UI value for %s", name)
    if secrets is not None:
        for name, attr in SECRET_FIELDS.items():
            value = secrets.get(name)
            if value:
                register_secret(value)
                setattr(s, attr, value)
    s.max_tool_steps = max(1, s.max_tool_steps)
    s.history_messages = max(0, s.history_messages)
    return s


class SetupState:
    """Wizard progress in ``DATA_DIR/ui-setup.json`` (no secrets: timestamps, names, fingerprints)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def update(self, **values: Any) -> dict:
        with self._lock:
            data = self.read()
            for k, v in values.items():
                if v is None:
                    data.pop(k, None)
                else:
                    data[k] = v
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".ui-setup.", dir=self.path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return data


class LiveConfig:
    """Applies saved settings/secrets to the running bot."""

    def __init__(self, app: "AiBot", settings_store: SettingsStore, secrets: SecretStore,
                 setup_state: SetupState | None = None, base: Callable[[], Settings] | None = None):
        self.app = app
        self.base = base or Settings.from_env  # the environment layer (tests pass their own)
        self.store = settings_store
        self.secrets = secrets
        self.setup = setup_state or SetupState(Path(app.settings.data_dir) / "ui-setup.json")
        self.applied_at: float | None = None

    def effective(self) -> Settings:
        return load_settings(self.store, self.secrets, base=self.base())

    async def apply(self) -> dict:
        """Copy every changed field onto the live ``Settings`` object and refresh the clients that
        cache values. Returns ``{changed, restart_required, notes}``."""
        app = self.app
        new = self.effective()
        live = app.settings
        changed: list[str] = []
        for f in dataclasses.fields(Settings):
            if f.name in ("data_dir", "web_read_ports", "web_read_timeout", "web_read_max_bytes",
                          "web_read_max_redirects", "schedule_tick", "telegram_api_base"):
                continue  # not UI-managed (security / storage); never touched at runtime
            old_v, new_v = getattr(live, f.name), getattr(new, f.name)
            if old_v != new_v:
                setattr(live, f.name, new_v)
                changed.append(f.name)
        notes: list[str] = []
        restart = False
        if {"llm_provider", "llm_allow_private", "llm_base_url", "llm_api_key", "llm_timeout", "llm_retries"} & set(changed):
            app.rebuild_llm()
            notes.append("LLM istemcisi yeni ayarlarla yenilendi.")
        bot = app.bot
        if bot is not None:
            if "allowed_chat_ids" in changed:
                bot.allowed_chat_ids = set(live.allowed_chat_ids)
            if "confirm_timeout" in changed:
                bot.confirm_timeout = live.confirm_timeout
            if {"telegram_bot_token", "telegram_enabled"} & set(changed):
                if app.telegram is not None:
                    if not live.telegram_enabled:
                        await app.telegram.stop()
                        notes.append("Telegram kapatıldı; bot mesaj dinlemiyor.")
                    else:
                        await app.telegram.restart(live.telegram_bot_token or "")
                        notes.append("Telegram bağlantısı yeniden başlatıldı."
                                     if live.telegram_bot_token else "Telegram token'ı yok; bot durduruldu.")
                else:
                    restart = True
                    notes.append("Telegram botu bu serviste başka bir modüle ait: token için servisi yeniden başlatın.")
        if "tz" in changed:
            app.scheduler._wake.set()
        self.applied_at = time.time()
        if changed:
            log.info("admin UI settings applied: %s", ", ".join(sorted(c for c in changed)))
        return {"changed": sorted(changed), "restart_required": restart, "notes": notes}
