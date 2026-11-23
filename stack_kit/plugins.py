"""Plugin loader: imports ``PLUGINS_DIR/*.py`` (files starting with ``_`` are skipped)."""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import sys
from pathlib import Path

from .redact import redact
from .registry import Stack, get_stack, use_stack
from .settings import env_str

log = logging.getLogger("stack_kit.plugins")


def load_plugins(stack: Stack | None = None, plugins_dir: str | Path | None = None) -> list[str]:
    """Import every plugin file into ``stack`` (default: current stack).

    * Directory: ``plugins_dir`` or ``PLUGINS_DIR`` env or ``plugins``. A missing directory is fine.
    * Files are imported in name order. ``@tool``/``@hook``/``@command``/``emit`` used at import
      time register into ``stack``.
    * A plugin that raises (syntax error, import error, duplicate tool name...) does not stop the
      others: the error is logged and appended to ``stack.plugin_errors`` as
      ``{"plugin": "<file>", "error": "<Type>: <message>"}``. Tools it registered before failing
      are removed again so a half-loaded plugin never exposes tools.

    Returns the names of plugins that loaded successfully.
    """
    stack = stack or get_stack()
    directory = Path(plugins_dir or env_str("PLUGINS_DIR", "plugins") or "plugins")
    if not directory.is_dir():
        log.info("plugins directory %s not found; no plugins loaded", directory)
        return []
    dir_tag = hashlib.sha1(str(directory.resolve()).encode(), usedforsecurity=False).hexdigest()[:8]
    loaded: list[str] = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        before = (set(stack.tools), set(stack.hooks), set(stack.commands))
        module_name = f"stack_plugin_{dir_tag}_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot create module spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            with use_stack(stack):
                spec.loader.exec_module(module)
        except BaseException as exc:  # noqa: BLE001 - SystemExit in a plugin must not kill the app
            if isinstance(exc, KeyboardInterrupt):
                raise
            sys.modules.pop(module_name, None)
            for name in set(stack.tools) - before[0]:
                del stack.tools[name]
            for name in set(stack.hooks) - before[1]:
                del stack.hooks[name]
            for name in set(stack.commands) - before[2]:
                del stack.commands[name]
            message = redact(f"{type(exc).__name__}: {exc}")[:300]
            stack.plugin_errors.append({"plugin": path.name, "error": message})
            log.error("plugin %s failed to load: %s", path.name, message)
            continue
        loaded.append(path.stem)
        stack.loaded_plugins.append(path.stem)
        log.info("plugin %s loaded", path.name)
    return loaded
