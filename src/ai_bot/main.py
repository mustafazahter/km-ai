"""Standalone entry point: ``python -m ai_bot`` (Docker CMD).

Loads ``.env`` (if present), registers ai-bot on the default stack and serves the stack_kit
HTTP bridge (``/health``, ``/bridge/*``, ``/hook/*``, ``/mcp``) with the Telegram bot,
the scheduler, ``plugins/*.py`` and the admin UI (``/ui``, see docs/ARAYUZ.md).

Safe defaults: the panel asks for a password (first start: a one-time setup code in the log, then
``/ui/setup``); ``/bridge``, ``/mcp`` and ``/hook`` are closed until opened in the panel.

Settings: a value saved in ``/ui`` wins over the environment variable, which wins over the default
(``ai_bot.runtime_config``).
"""

from __future__ import annotations

import logging

from stack_kit import BridgeGate, configure, create_app, env_int, env_str, load_dotenv
from stack_ui import SecretStore, data_dir, settings_store

from . import __version__, setup
from .panel import log_buffer, mount_panel
from .runtime_config import SCHEMA, LiveConfig, SetupState, load_settings


GATE_DEFAULT = {"bridge": False, "mcp": False, "hook": False}


def build_app():
    """Build the FastAPI app (bridge + admin UI). Separate from :func:`main` for tests/tools."""
    stack = configure(name=env_str("STACK_NAME", "ai-bot") or "ai-bot",
                      version=env_str("STACK_VERSION", __version__) or __version__)
    folder = data_dir()
    store = settings_store(SCHEMA, folder / "ui-settings.db")
    secrets = SecretStore(folder / "ui-secrets.json")
    settings = load_settings(store, secrets)
    ai = setup(stack, settings=settings)
    # /bridge, /mcp and /hook start CLOSED (404) until opened in the panel ("Köprü ve entegrasyonlar"),
    # with BRIDGE_ENABLED / MCP_ENABLED / HOOKS_ENABLED, or by `python -m stack_kit gate enable ...`
    app = create_app(stack, gates=BridgeGate(default=GATE_DEFAULT))
    live = LiveConfig(ai, store, secrets, SetupState(folder / "ui-setup.json"))
    ai.channels.bind(store, secrets)  # channel settings: panel > env > default, live
    ai.channels.mount(app)  # /channels/* webhooks (404 while a channel is disabled; own signature checks)
    mount_panel(app, ai, live, title=env_str("UI_TITLE", "km-ai") or "km-ai", telegram_api_base=settings.telegram_api_base)
    return app


def main() -> None:
    import uvicorn

    load_dotenv(".env")
    level = (env_str("LOG_LEVEL", "INFO") or "INFO").upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log_buffer()  # capture start-up messages for the UI log page
    app = build_app()
    uvicorn.run(app, host=env_str("HTTP_HOST", "0.0.0.0") or "0.0.0.0", port=env_int("HTTP_PORT", 8080),
                log_level=level.lower())


if __name__ == "__main__":
    main()
