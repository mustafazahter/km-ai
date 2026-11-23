"""Long-term memory on SQLite FTS5.

Policy (what gets remembered) lives in the system prompt (agent.py) and in :data:`BLOCK_PATTERNS`:
the AI decides, but obvious secrets are refused here so a prompt cannot store them.
See ``.claude/skills/add-memory-policy``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from stack_kit import ToolError

from .config import Settings
from .db import Database

MAX_MEMORY_CHARS = 500

# Refuse to store things that look like secrets (tokens, keys, card numbers, passwords).
BLOCK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),                       # telegram bot token
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),                               # openai/openrouter style keys
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),  # github
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                  # aws access key id
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),                        # slack
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                                 # card-like numbers
    # "şifresi hunter2", "password is hunter2", "parola: x"
    re.compile(r"(?i)\b(?:şifre|sifre|parola|password|passwd|pwd)\w*\s*(?:\s(?:is|was|nedir|şu|su)\b|[:=])?\s*\S+"),
    # short forms: "pw: x", "pass: x", "pwd=x", "pin: 1234"
    re.compile(r"(?i)\b(?:pw|pass|pwd|pin)\s*[:=]\s*\S+"),
    # "api key = ...", "token: ...", "secret is ..."
    re.compile(r"(?i)\b(?:api[_ -]?key|secret|token|access[_ -]?key)\w*(?:\s+(?:is|was|nedir)\b|\s*[:=])\s*\S+"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),                                  # long hex keys
    # long base64-like strings with upper, lower and digits (aws secret keys, jwt parts ...)
    re.compile(r"(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])(?=[A-Za-z0-9+/]*\d)[A-Za-z0-9+/]{32,}={0,2}"),
]


class MemoryService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.on_change = None  # callable(event, data); setup() points it at stack.emit

    def remember(self, chat_id: int, content: str, tags: str = "") -> dict:
        content = " ".join((content or "").split())
        if not content:
            raise ToolError("memory content is empty", code="invalid_args")
        if len(content) > MAX_MEMORY_CHARS:
            raise ToolError(f"memory is too long (max {MAX_MEMORY_CHARS} characters); keep it to one fact",
                            code="invalid_args")
        if any(p.search(content) for p in BLOCK_PATTERNS):
            raise ToolError("this looks like a secret (password, token, card number); not stored",
                            code="refused")
        dup = self.db.find_duplicate_memory(chat_id, content)
        if dup:
            return {"id": dup["id"], "stored": False, "note": "already remembered"}
        if self.db.count_memories(chat_id) >= self.settings.memory_max_per_chat:
            raise ToolError("memory is full; forget something first", code="limit")
        mid = self.db.add_memory(chat_id, content, " ".join((tags or "").split())[:200])
        if self.on_change is not None:
            self.on_change("memory.remembered", {"id": mid, "chat_id": chat_id})
        return {"id": mid, "stored": True}

    def recall(self, chat_id: int, query: str, limit: int = 5) -> list[dict]:
        limit = max(1, min(limit, 20))
        rows = self.db.search_memories(chat_id, query, limit) if query.strip() else self.db.recent_memories(chat_id, limit)
        return [_public(r) for r in rows]

    def forget(self, chat_id: int, memory_id: int) -> dict:
        row = self.db.get_memory(chat_id, memory_id)
        if not row:
            raise ToolError(f"no memory with id {memory_id}", code="not_found")
        self.db.delete_memory(chat_id, memory_id)
        if self.on_change is not None:
            self.on_change("memory.forgotten", {"id": memory_id, "chat_id": chat_id})
        return {"id": memory_id, "forgotten": True, "content": row["content"]}

    def list(self, chat_id: int, limit: int = 20, offset: int = 0) -> dict:
        limit = max(1, min(limit, 100))
        rows = self.db.recent_memories(chat_id, limit, max(offset, 0))
        return {"total": self.db.count_memories(chat_id), "items": [_public(r) for r in rows]}

    def context(self, chat_id: int, text: str) -> list[dict]:
        """Memories added to the system prompt: FTS matches first, then the most recent ones."""
        limit = self.settings.memory_context_limit
        if limit <= 0:
            return []
        picked: dict[int, dict] = {}
        for r in self.db.search_memories(chat_id, text, limit):
            picked[r["id"]] = r
        if len(picked) < limit:
            for r in self.db.recent_memories(chat_id, limit):
                if len(picked) >= limit:
                    break
                picked.setdefault(r["id"], r)
        return [_public(r) for r in picked.values()]


def _public(row: dict) -> dict:
    created = datetime.fromtimestamp(row["created"], tz=timezone.utc).strftime("%Y-%m-%d")
    out = {"id": row["id"], "content": row["content"], "created": created}
    if row.get("tags"):
        out["tags"] = row["tags"]
    return out
