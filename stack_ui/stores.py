"""Persistent stores for the admin UI.

* :class:`SecretStore` — write-only secrets entered in the UI (``DATA_DIR/ui-secrets.json``, mode 600).
  Values are only readable server-side (:meth:`SecretStore.get`); APIs expose ``has`` + a masked preview.
* :class:`SettingsStore` — schema-validated settings that override environment variables
  (SQLite ``DATA_DIR/ui-settings.db``), every change written to an audit log.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stack_kit.redact import register_secret
from stack_kit.settings import env_str

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_SECRET_LEN = 8192


def data_dir(path: str | os.PathLike[str] | None = None) -> Path:
    """``path`` or ``DATA_DIR`` env or ``./data`` (created with mode 700 if missing)."""
    p = Path(path or env_str("DATA_DIR", "data") or "data")
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def mask(value: str | None) -> str | None:
    """Masked preview: ``…`` plus the last 4 characters (only ``…`` for values shorter than 12)."""
    if not value:
        return None
    return "…" + value[-4:] if len(value) >= 12 else "…"


# --------------------------------------------------------------------------- secrets


class SecretStore:
    """Write-only secret storage in one JSON file with permission 600 (atomic writes)."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path else data_dir() / "ui-secrets.json"
        self._lock = threading.Lock()
        for value in self._read().values():
            register_secret(value)

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError:
            # never silently overwrite a damaged file (that would lose every stored secret)
            raise RuntimeError(f"{self.path.name} is not valid JSON; fix or remove it") from None
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(data, dict) else {}

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix=".ui-secrets.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _check_name(name: str) -> None:
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ValueError("secret name must match [A-Z][A-Z0-9_]*")

    def set(self, name: str, value: str) -> None:
        self._check_name(name)
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_SECRET_LEN:
            raise ValueError(f"secret value must be a non-empty string up to {MAX_SECRET_LEN} characters")
        value = value.strip()
        register_secret(value)
        with self._lock:
            data = self._read()
            data[name] = value
            self._write(data)

    def delete(self, name: str) -> bool:
        self._check_name(name)
        with self._lock:
            data = self._read()
            existed = data.pop(name, None) is not None
            if existed:
                self._write(data)
            return existed

    def get(self, name: str) -> str | None:
        """Server-side read. Never return this value from an API."""
        return self._read().get(name)

    def get_or_env(self, name: str) -> str | None:
        """Stored value, else the environment variable of the same name (registered for redaction)."""
        value = self.get(name) or env_str(name)
        register_secret(value)
        return value

    def has(self, name: str) -> bool:
        return name in self._read()

    has_secret = has

    def preview(self, name: str) -> str | None:
        return mask(self.get(name))

    def names(self) -> list[str]:
        return sorted(self._read())

    def describe(self, names: list[str] | None = None) -> list[dict]:
        """Safe listing for APIs: ``[{name, set, preview, source}]`` (``source``: ui | env | none)."""
        data = self._read()
        out = []
        for name in names if names is not None else sorted(data):
            if name in data:
                out.append({"name": name, "set": True, "preview": mask(data[name]), "source": "ui"})
            elif env_str(name):
                out.append({"name": name, "set": True, "preview": mask(env_str(name)), "source": "env"})
            else:
                out.append({"name": name, "set": False, "preview": None, "source": "none"})
        return out


# --------------------------------------------------------------------------- settings


@dataclass
class Setting:
    """Schema of one setting. ``type``: ``str`` | ``int`` | ``float`` | ``bool`` | ``choice``."""

    name: str
    type: str = "str"
    default: Any = None
    description: str = ""
    choices: list[str] = field(default_factory=list)
    min: float | None = None
    max: float | None = None
    pattern: str | None = None
    env: str | None = None  # environment variable it overrides (default: same name)
    max_length: int = 1000

    def __post_init__(self) -> None:
        if not NAME_RE.match(self.name):
            raise ValueError(f"invalid setting name {self.name!r}")
        if self.type not in ("str", "int", "float", "bool", "choice"):
            raise ValueError(f"invalid setting type {self.type!r}")
        if self.type == "choice" and not self.choices:
            raise ValueError(f"setting {self.name}: choices required")

    def coerce(self, value: Any) -> Any:
        """Validate and convert. Raises ``ValueError`` with a user-facing message."""
        t = self.type
        if t == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "on", "evet"):
                return True
            if isinstance(value, str) and value.strip().lower() in ("0", "false", "no", "off", "hayır", "hayir"):
                return False
            raise ValueError(f"{self.name}: true/false expected")
        if t in ("int", "float"):
            if isinstance(value, bool):
                raise ValueError(f"{self.name}: number expected")
            try:
                num = int(value) if t == "int" else float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{self.name}: {'integer' if t == 'int' else 'number'} expected") from None
            if t == "float" and num != num or num in (float("inf"), float("-inf")):
                raise ValueError(f"{self.name}: finite number expected")
            if self.min is not None and num < self.min:
                raise ValueError(f"{self.name}: must be ≥ {self.min:g}")
            if self.max is not None and num > self.max:
                raise ValueError(f"{self.name}: must be ≤ {self.max:g}")
            return num
        if not isinstance(value, str):
            raise ValueError(f"{self.name}: text expected")
        value = value.strip()
        if len(value) > self.max_length:
            raise ValueError(f"{self.name}: at most {self.max_length} characters")
        if t == "choice" and value not in self.choices:
            raise ValueError(f"{self.name}: one of {', '.join(self.choices)}")
        if self.pattern and not re.fullmatch(self.pattern, value):
            raise ValueError(f"{self.name}: invalid format")
        return value

    def schema(self) -> dict:
        return {"name": self.name, "type": self.type, "default": self.default, "description": self.description,
                "choices": list(self.choices), "min": self.min, "max": self.max}


class SettingsStore:
    """Settings that override env vars. Lookup order: stored value → env var → default."""

    def __init__(self, schema: list[Setting], path: str | os.PathLike[str] | None = None):
        self.schema = {s.name: s for s in schema}
        self.path = Path(path) if path else data_dir() / "ui-settings.db"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock, self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL,"
                             " updated_at REAL NOT NULL, updated_by TEXT)")
            self._db.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
                             " actor TEXT, name TEXT NOT NULL, action TEXT NOT NULL, old TEXT, new TEXT)")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _spec(self, name: str) -> Setting:
        spec = self.schema.get(name)
        if spec is None:
            raise KeyError(f"unknown setting: {name}")
        return spec

    def _stored(self, name: str) -> tuple[bool, Any]:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
        return (True, json.loads(row[0])) if row else (False, None)

    def get(self, name: str) -> Any:
        return self.resolve(name)["value"]

    def resolve(self, name: str) -> dict:
        """``{name, value, source}`` with ``source`` = ``ui`` | ``env`` | ``default``."""
        spec = self._spec(name)
        found, value = self._stored(name)
        if found:
            return {"name": name, "value": value, "source": "ui"}
        env_value = env_str(spec.env or name)
        if env_value is not None:
            try:
                return {"name": name, "value": spec.coerce(env_value), "source": "env"}
            except ValueError:
                pass  # an invalid env value falls back to the default
        return {"name": name, "value": spec.default, "source": "default"}

    def all(self) -> list[dict]:
        return [{**self.resolve(n), **{"schema": s.schema()}} for n, s in self.schema.items()]

    def set(self, name: str, value: Any, *, actor: str | None = None) -> Any:
        spec = self._spec(name)
        coerced = spec.coerce(value)
        old = self.resolve(name)["value"]
        with self._lock, self._db:
            self._db.execute("INSERT INTO settings (name, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
                             " ON CONFLICT(name) DO UPDATE SET value = excluded.value,"
                             " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                             (name, json.dumps(coerced), time.time(), actor))
            self._audit(actor, name, "set", old, coerced)
        return coerced

    def reset(self, name: str, *, actor: str | None = None) -> None:
        self._spec(name)
        old = self.resolve(name)["value"]
        with self._lock, self._db:
            self._db.execute("DELETE FROM settings WHERE name = ?", (name,))
            self._audit(actor, name, "reset", old, None)

    def _audit(self, actor: str | None, name: str, action: str, old: Any, new: Any) -> None:
        self._db.execute("INSERT INTO audit (ts, actor, name, action, old, new) VALUES (?, ?, ?, ?, ?, ?)",
                         (time.time(), actor, name, action, json.dumps(old), json.dumps(new)))

    def audit(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, ts, actor, name, action, old, new FROM audit ORDER BY id DESC LIMIT ?",
                                    (max(1, min(limit, 1000)),)).fetchall()
        return [{"id": r[0], "ts": r[1], "actor": r[2], "name": r[3], "action": r[4],
                 "old": json.loads(r[5]) if r[5] else None, "new": json.loads(r[6]) if r[6] else None} for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


_secret_stores: dict[str, SecretStore] = {}


def secret_store(path: str | os.PathLike[str] | None = None) -> SecretStore:
    """Shared :class:`SecretStore` for ``path`` (default ``DATA_DIR/ui-secrets.json``)."""
    key = str(Path(path) if path else data_dir() / "ui-secrets.json")
    if key not in _secret_stores:
        _secret_stores[key] = SecretStore(key)
    return _secret_stores[key]


def settings_store(schema: list[Setting], path: str | os.PathLike[str] | None = None) -> SettingsStore:
    """Create a :class:`SettingsStore` (default file ``DATA_DIR/ui-settings.db``)."""
    return SettingsStore(schema, path)
