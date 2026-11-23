"""Bridge gate: switch the machine surfaces (``/bridge/*``, ``/mcp``, ``/hook/*``) on and off at runtime.

* A closed surface answers **404** (its existence is not revealed).
* Start value per surface: env (``BRIDGE_ENABLED``, ``MCP_ENABLED``, ``HOOKS_ENABLED``) if set, else the
  persisted value (``DATA_DIR/bridge-gate.json``), else ``default`` (all ``True``: backward compatible).
* Changes written to the state file by another process (``python -m stack_kit gate ...``) apply live
  (the file's mtime is checked on each request).
* A panel-generated bridge token is shown once; only ``sha256(salt + token)`` is stored. The env
  ``BRIDGE_TOKEN`` stays valid alongside it.
* A panel-generated hook secret is shown once. HMAC verification needs the key itself, so it is
  stored as-is in the same file (mode 600), never returned again.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .redact import register_secret
from .settings import env_bool, env_str

SURFACES = ("bridge", "mcp", "hook")
ENV_NAMES = {"bridge": "BRIDGE_ENABLED", "mcp": "MCP_ENABLED", "hook": "HOOKS_ENABLED"}
AUDIT_KEEP = 200


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically with permission 600 (directory 700)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        raise RuntimeError(f"{path.name} is not valid JSON; fix or remove it") from None
    return data if isinstance(data, dict) else {}


def _hash_token(salt: str, token: str) -> str:
    return hashlib.sha256(f"{salt}:{token}".encode()).hexdigest()


class BridgeGate:
    """Runtime on/off switches and panel-managed credentials for the machine surfaces.

    Args:
        default: ``{"bridge": bool, "mcp": bool, "hook": bool}`` (missing keys = ``True``).
        path: state file (default ``DATA_DIR/bridge-gate.json``; written only when something changes).
        use_env: read ``BRIDGE_ENABLED`` / ``MCP_ENABLED`` / ``HOOKS_ENABLED`` for the start values.
    """

    def __init__(self, default: dict[str, bool] | None = None, *, path: str | os.PathLike[str] | None = None,
                 use_env: bool = True):
        unknown = set(default or {}) - set(SURFACES)
        if unknown:
            raise ValueError(f"unknown surface(s): {', '.join(sorted(unknown))}")
        self.path = Path(path) if path else Path(env_str("DATA_DIR", "data") or "data") / "bridge-gate.json"
        self._lock = threading.Lock()
        self._state = read_json(self.path)
        self._mtime = self._file_mtime()
        stored = self._state.get("surfaces") if isinstance(self._state.get("surfaces"), dict) else {}
        self._file_surfaces = {k: v for k, v in stored.items() if k in SURFACES and isinstance(v, bool)}
        self._surfaces: dict[str, bool] = {}
        self.sources: dict[str, str] = {}
        for name in SURFACES:
            env_name = ENV_NAMES[name]
            if use_env and env_str(env_name) is not None:
                self._surfaces[name] = env_bool(env_name, True)
                self.sources[name] = "env"
            elif isinstance(stored.get(name), bool):
                self._surfaces[name] = stored[name]
                self.sources[name] = "saved"
            else:
                self._surfaces[name] = bool((default or {}).get(name, True))
                self.sources[name] = "default"
        if isinstance(self._state.get("hook_secret"), str):
            register_secret(self._state["hook_secret"])

    # -- live reload (changes made by the CLI or another process apply without restart)
    def _file_mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def _maybe_reload(self) -> None:
        mtime = self._file_mtime()
        if mtime == self._mtime:
            return
        with self._lock:
            self._mtime = mtime
            try:
                state = read_json(self.path)
            except RuntimeError:
                return  # keep the last good state
            stored = state.get("surfaces") if isinstance(state.get("surfaces"), dict) else {}
            for name in SURFACES:
                value = stored.get(name)
                # only values that changed in the file override (env start values otherwise stay)
                if isinstance(value, bool) and value != self._file_surfaces.get(name):
                    self._surfaces[name] = value
                    self.sources[name] = "saved"
            self._file_surfaces = {k: v for k, v in stored.items() if k in SURFACES and isinstance(v, bool)}
            self._state = state
            if isinstance(state.get("hook_secret"), str):
                register_secret(state["hook_secret"])

    # -- surfaces
    def enabled(self, surface: str) -> bool:
        self._maybe_reload()
        return self._surfaces.get(surface, False)

    def any_enabled(self) -> bool:
        self._maybe_reload()
        return any(self._surfaces.values())

    def set(self, surface: str, enabled: bool, *, actor: str | None = None) -> None:
        if surface not in SURFACES:
            raise ValueError(f"unknown surface: {surface}")
        with self._lock:
            old = self._surfaces[surface]
            self._surfaces[surface] = bool(enabled)
            self.sources[surface] = "saved"
            self._state.setdefault("surfaces", {})[surface] = bool(enabled)
            self._audit(actor, f"{surface}.{'enable' if enabled else 'disable'}", {"old": old})
            self._save()

    # -- panel bridge token (hash only)
    @property
    def has_panel_token(self) -> bool:
        self._maybe_reload()
        tok = self._state.get("token")
        return isinstance(tok, dict) and isinstance(tok.get("hash"), str)

    def rotate_token(self, *, actor: str | None = None) -> str:
        """Create a new 48-character token, store only its salted hash, return it ONCE."""
        token = secrets.token_urlsafe(36)
        salt = secrets.token_hex(16)
        with self._lock:
            self._state["token"] = {"salt": salt, "hash": _hash_token(salt, token), "created_at": int(time.time())}
            self._audit(actor, "token.rotate", None)
            self._save()
        register_secret(token)
        return token

    def revoke_token(self, *, actor: str | None = None) -> None:
        with self._lock:
            self._state.pop("token", None)
            self._audit(actor, "token.revoke", None)
            self._save()

    def verify_token(self, supplied: str | None) -> bool:
        self._maybe_reload()
        tok = self._state.get("token")
        if not supplied or not isinstance(tok, dict):
            return False
        expected = tok.get("hash", "")
        actual = _hash_token(str(tok.get("salt", "")), supplied)
        return hmac.compare_digest(actual.encode(), str(expected).encode())

    # -- panel hook secret (needed as-is for HMAC)
    @property
    def hook_secret(self) -> str | None:
        self._maybe_reload()
        value = self._state.get("hook_secret")
        return value if isinstance(value, str) and value else None

    def rotate_hook_secret(self, *, actor: str | None = None) -> str:
        secret = secrets.token_urlsafe(36)
        register_secret(secret)
        with self._lock:
            self._state["hook_secret"] = secret
            self._state["hook_secret_created_at"] = int(time.time())
            self._audit(actor, "hook_secret.rotate", None)
            self._save()
        return secret

    def revoke_hook_secret(self, *, actor: str | None = None) -> None:
        with self._lock:
            self._state.pop("hook_secret", None)
            self._state.pop("hook_secret_created_at", None)
            self._audit(actor, "hook_secret.revoke", None)
            self._save()

    # -- status / audit
    def status(self) -> dict:
        self._maybe_reload()
        tok = self._state.get("token") if self.has_panel_token else None
        return {
            "surfaces": dict(self._surfaces),
            "sources": dict(self.sources),
            "panel_token": {"set": tok is not None, "created_at": tok.get("created_at") if tok else None},
            "panel_hook_secret": {"set": self.hook_secret is not None,
                                  "created_at": self._state.get("hook_secret_created_at")},
        }

    def audit(self, limit: int = 50) -> list[dict]:
        entries = self._state.get("audit") if isinstance(self._state.get("audit"), list) else []
        return list(reversed(entries[-limit:]))

    def _audit(self, actor: str | None, action: str, detail: Any) -> None:
        entries = self._state.setdefault("audit", [])
        entries.append({"ts": int(time.time()), "actor": actor, "action": action, "detail": detail})
        del entries[:-AUDIT_KEEP]

    def _save(self) -> None:
        atomic_write_json(self.path, self._state)
        self._mtime = self._file_mtime()
        surfaces = self._state.get("surfaces") if isinstance(self._state.get("surfaces"), dict) else {}
        self._file_surfaces = {k: v for k, v in surfaces.items() if k in SURFACES and isinstance(v, bool)}
