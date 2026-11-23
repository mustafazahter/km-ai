"""Tiny environment helpers and an optional ``.env`` loader (no extra dependency)."""

from __future__ import annotations

import os
from pathlib import Path

from .redact import register_secret

_TRUE = {"1", "true", "yes", "on", "y", "evet"}
_FALSE = {"0", "false", "no", "off", "n", "hayir", "hayır", ""}


def load_dotenv(path: str | os.PathLike[str] = ".env", *, override: bool = False) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines from ``path`` into ``os.environ``.

    * Missing file is not an error (returns ``{}``).
    * Blank lines and ``#`` comments are skipped; an optional ``export`` prefix is allowed.
    * Values may be wrapped in single or double quotes. Unquoted values lose a trailing `` # comment``.
    * Existing environment variables win unless ``override=True``.

    Returns the parsed key/value pairs (also the ones that were not applied).
    """
    p = Path(path)
    if not p.is_file():
        return {}
    parsed: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace("\\n", "\n")
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


def env_str(name: str, default: str | None = None) -> str | None:
    """Return the variable, or ``default`` when it is unset or empty."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_int(name: str, default: int) -> int:
    """Return the variable as ``int``; raise ``ValueError`` with the variable name on bad input."""
    value = env_str(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def env_float(name: str, default: float) -> float:
    """Return the variable as ``float``; raise ``ValueError`` with the variable name on bad input."""
    value = env_str(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


def env_bool(name: str, default: bool = False) -> bool:
    """Accept 1/true/yes/on/evet and 0/false/no/off/hayır (case-insensitive)."""
    value = os.environ.get(name)
    if value is None:
        return default
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return default if v == "" else False
    raise ValueError(f"{name} must be a boolean (true/false)")


def env_list(name: str, default: list[str] | None = None, *, sep: str = ",") -> list[str]:
    """Split the variable by ``sep``, trimming items and dropping empty ones."""
    value = env_str(name)
    if value is None:
        return list(default or [])
    return [item.strip() for item in value.split(sep) if item.strip()]


def secret(name: str, default: str | None = None) -> str | None:
    """Like :func:`env_str` but registers the value for log redaction."""
    value = env_str(name, default)
    register_secret(value)
    return value
