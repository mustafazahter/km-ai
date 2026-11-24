"""Settings read from the environment (12-factor). Secrets are registered for log redaction.

Order of precedence (documented in docs/ADMIN-UI.md): a value saved in the admin UI (``/ui``,
``DATA_DIR/ui-settings.db`` / ``ui-secrets.json``) wins over the environment variable, which wins
over the default here. ``ai_bot.runtime_config.load_settings`` applies the UI layer on top of
:meth:`Settings.from_env`; only the whitelisted, safe settings can be overridden there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stack_kit import env_bool, env_float, env_int, env_list, env_str, secret

DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"  # unchanged on purpose: a new default would silently switch the model of running installs


@dataclass
class Settings:
    # LLM (OpenAI compatible /chat/completions, or Anthropic's native API; see providers.py)
    llm_provider: str = "auto"  # auto = inferred from the base URL
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_allow_private: bool = False  # "custom" provider may reach private/loopback addresses (explicit opt-in)
    llm_api_key: str | None = field(default=None, repr=False)
    llm_model: str = DEFAULT_LLM_MODEL
    llm_model_choices: list[str] = field(default_factory=list)
    llm_timeout: float = 60.0
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.3
    llm_retries: int = 2
    max_tool_steps: int = 6
    turn_timeout: float = 180.0
    tool_result_max_chars: int = 6000
    history_messages: int = 12
    system_prompt_extra: str = ""
    # per-channel instruction, added after the general one on that channel's turns (never in panel chat)
    system_prompt_telegram: str = ""
    system_prompt_whatsapp: str = ""
    system_prompt_teams: str = ""
    system_prompt_slack: str = ""
    system_prompt_email: str = ""
    # storage
    data_dir: Path = Path("data")
    tz: str = "UTC"
    # search / read
    search_provider: str = "auto"
    search_max_results: int = 5
    tavily_api_key: str | None = field(default=None, repr=False)
    brave_api_key: str | None = field(default=None, repr=False)
    serper_api_key: str | None = field(default=None, repr=False)
    searxng_url: str | None = None
    web_read_timeout: float = 15.0
    web_read_max_bytes: int = 2_000_000
    web_read_max_chars: int = 8000
    web_read_max_redirects: int = 5
    web_read_ports: list[int] = field(default_factory=lambda: [80, 443, 8080, 8443])
    # memory / schedule
    memory_context_limit: int = 6
    memory_max_per_chat: int = 500
    schedule_tick: float = 20.0
    schedule_max_per_chat: int = 50
    schedule_min_interval_minutes: int = 5
    # telegram
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_enabled: bool = True  # off = the bot does not poll even with a token
    telegram_api_base: str = "https://api.telegram.org"  # env only (local Bot API server); not UI-settable
    allowed_chat_ids: list[int] = field(default_factory=list)
    confirm_timeout: float = 120.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ai-bot.sqlite3"

    @property
    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    @property
    def default_chat_id(self) -> int | None:
        return self.allowed_chat_ids[0] if self.allowed_chat_ids else None

    @classmethod
    def from_env(cls) -> "Settings":
        ports = [int(p) for p in env_list("WEB_READ_PORTS", ["80", "443", "8080", "8443"])]
        chats = []
        for raw in env_list("ALLOWED_CHAT_IDS", []):
            try:
                chats.append(int(raw))
            except ValueError:
                raise ValueError("ALLOWED_CHAT_IDS must be a comma separated list of integers") from None
        return cls(
            llm_provider=(env_str("LLM_PROVIDER", "auto") or "auto").lower(),
            llm_allow_private=env_bool("LLM_ALLOW_PRIVATE_NETWORK", False),
            llm_base_url=(env_str("LLM_BASE_URL", DEFAULT_LLM_BASE_URL) or DEFAULT_LLM_BASE_URL).rstrip("/"),
            llm_api_key=secret("LLM_API_KEY"),
            llm_model=env_str("LLM_MODEL", DEFAULT_LLM_MODEL) or DEFAULT_LLM_MODEL,
            llm_model_choices=env_list("LLM_MODEL_CHOICES", []),
            llm_timeout=env_float("LLM_TIMEOUT", 60.0),
            llm_max_tokens=env_int("LLM_MAX_TOKENS", 1024),
            llm_temperature=env_float("LLM_TEMPERATURE", 0.3),
            llm_retries=env_int("LLM_RETRIES", 2),
            max_tool_steps=max(1, env_int("MAX_TOOL_STEPS", 6)),
            turn_timeout=env_float("TURN_TIMEOUT", 180.0),
            tool_result_max_chars=env_int("TOOL_RESULT_MAX_CHARS", 6000),
            history_messages=max(0, env_int("HISTORY_MESSAGES", 12)),
            system_prompt_extra=env_str("SYSTEM_PROMPT_EXTRA", "") or "",
            system_prompt_telegram=env_str("SYSTEM_PROMPT_TELEGRAM", "") or "",
            system_prompt_whatsapp=env_str("SYSTEM_PROMPT_WHATSAPP", "") or "",
            system_prompt_teams=env_str("SYSTEM_PROMPT_TEAMS", "") or "",
            system_prompt_slack=env_str("SYSTEM_PROMPT_SLACK", "") or "",
            system_prompt_email=env_str("SYSTEM_PROMPT_EMAIL", "") or "",
            data_dir=Path(env_str("DATA_DIR", "data") or "data"),
            tz=env_str("TZ", "UTC") or os.environ.get("TZ") or "UTC",
            search_provider=(env_str("SEARCH_PROVIDER", "auto") or "auto").lower(),
            search_max_results=env_int("SEARCH_MAX_RESULTS", 5),
            tavily_api_key=secret("TAVILY_API_KEY"),
            brave_api_key=secret("BRAVE_API_KEY"),
            serper_api_key=secret("SERPER_API_KEY"),
            searxng_url=env_str("SEARXNG_URL"),
            web_read_timeout=env_float("WEB_READ_TIMEOUT", 15.0),
            web_read_max_bytes=env_int("WEB_READ_MAX_BYTES", 2_000_000),
            web_read_max_chars=env_int("WEB_READ_MAX_CHARS", 8000),
            web_read_max_redirects=env_int("WEB_READ_MAX_REDIRECTS", 5),
            web_read_ports=ports,
            memory_context_limit=env_int("MEMORY_CONTEXT_LIMIT", 6),
            memory_max_per_chat=env_int("MEMORY_MAX_PER_CHAT", 500),
            schedule_tick=env_float("SCHEDULE_TICK", 20.0),
            schedule_max_per_chat=env_int("SCHEDULE_MAX_PER_CHAT", 50),
            schedule_min_interval_minutes=env_int("SCHEDULE_MIN_INTERVAL_MINUTES", 5),
            telegram_bot_token=secret("TELEGRAM_BOT_TOKEN"),
            telegram_enabled=env_bool("TELEGRAM_ENABLED", True),
            telegram_api_base=(env_str("TELEGRAM_API_BASE", "https://api.telegram.org")
                               or "https://api.telegram.org").rstrip("/"),
            allowed_chat_ids=chats,
            confirm_timeout=env_float("CONFIRM_TIMEOUT", 120.0),
        )
