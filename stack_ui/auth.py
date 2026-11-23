"""Admin UI credentials: scrypt password hash, session version, protection switch, setup code.

State file ``DATA_DIR/ui-auth.json`` (mode 600)::

    {"password": {"algo": "scrypt", "n": 16384, "r": 8, "p": 1, "salt": "<b64>", "hash": "<b64>", "set_at": 0},
     "session_version": 1, "protection": true, "audit": [{"ts", "actor", "action"}]}

The plain-text password is never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from stack_kit.gate import atomic_write_json, read_json
from stack_kit.settings import env_str

SCRYPT_N = 2 ** 14  # CPU/memory cost (16 MiB with r=8)
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD = 10
AUDIT_KEEP = 200
DISABLE_PHRASE = "KORUMAYI KAPAT"
SETUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1

COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "1234567890", "12345678901", "123456789012",
    "qwertyuiop", "qwerty1234", "qwerty12345", "1q2w3e4r5t", "1qaz2wsx3edc", "asdfghjkl", "zxcvbnm123",
    "iloveyou12", "letmein123", "welcome123", "admin12345", "administrator", "changeme123", "change-me-please",
    "abc1234567", "abcdefghij", "0987654321", "1111111111", "football12", "monkey1234", "dragon1234",
    "sifre12345", "parola1234", "parola12345", "sifresifre", "galatasaray", "fenerbahce", "besiktas1903",
    "trustno1234", "baseball12", "superman12", "sunshine12", "princess12", "komuta1234", "komutakomuta",
}


def password_problem(password: Any) -> str | None:
    """``None`` if acceptable, else a user-facing reason (TR / EN)."""
    if not isinstance(password, str):
        return "Parola metin olmalı. / Password must be text."
    if len(password) < MIN_PASSWORD:
        return f"Parola en az {MIN_PASSWORD} karakter olmalı. / At least {MIN_PASSWORD} characters."
    if len(password) > 256:
        return "Parola en çok 256 karakter olabilir. / At most 256 characters."
    if password.lower() in COMMON_PASSWORDS:
        return "Bu parola çok yaygın. / This password is too common."
    if len(set(password)) < 4:
        return "Parola çok tekdüze. / Password is too repetitive."
    return None


def hash_password(password: str) -> dict:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            dklen=SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM)
    return {"algo": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
            "salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode(),
            "set_at": int(time.time())}


def verify_password_hash(record: dict, password: str) -> bool:
    try:
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(record["n"]), r=int(record["r"]),
                                p=int(record["p"]), dklen=len(expected), maxmem=SCRYPT_MAXMEM)
    except (KeyError, ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def new_setup_code() -> str:
    raw = "".join(secrets.choice(SETUP_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_code(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


class AuthStore:
    """Persistent admin credentials and protection switch (``DATA_DIR/ui-auth.json``)."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path else Path(env_str("DATA_DIR", "data") or "data") / "ui-auth.json"
        self._lock = threading.Lock()
        self._state = read_json(self.path)

    # -- password
    @property
    def has_password(self) -> bool:
        rec = self._state.get("password")
        return isinstance(rec, dict) and isinstance(rec.get("hash"), str)

    @property
    def password_fingerprint(self) -> str:
        rec = self._state.get("password") if self.has_password else {}
        return str(rec.get("hash", ""))

    def verify(self, password: str) -> bool:
        if not self.has_password or not isinstance(password, str):
            return False
        return verify_password_hash(self._state["password"], password)

    def set_password(self, password: str, *, actor: str | None) -> None:
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        record = hash_password(password)
        with self._lock:
            self._state["password"] = record
            self._state["session_version"] = self.session_version + 1
            self._audit(actor, "password.set")
            self._save()

    # -- sessions
    @property
    def session_version(self) -> int:
        value = self._state.get("session_version")
        return value if isinstance(value, int) and value > 0 else 1

    def revoke_sessions(self, *, actor: str | None) -> int:
        with self._lock:
            self._state["session_version"] = self.session_version + 1
            self._audit(actor, "sessions.revoke")
            self._save()
        return self.session_version

    # -- protection switch
    @property
    def protection(self) -> bool:
        return self._state.get("protection") is not False

    def set_protection(self, enabled: bool, *, actor: str | None) -> None:
        with self._lock:
            self._state["protection"] = bool(enabled)
            self._audit(actor, "protection.enable" if enabled else "protection.disable")
            self._save()

    # -- audit
    def audit(self, limit: int = 50) -> list[dict]:
        entries = self._state.get("audit") if isinstance(self._state.get("audit"), list) else []
        return list(reversed(entries[-limit:]))

    def _audit(self, actor: str | None, action: str) -> None:
        entries = self._state.setdefault("audit", [])
        entries.append({"ts": int(time.time()), "actor": actor, "action": action})
        del entries[:-AUDIT_KEEP]

    def _save(self) -> None:
        atomic_write_json(self.path, self._state)
