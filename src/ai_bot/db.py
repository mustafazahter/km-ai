"""SQLite storage: chat history, memories (FTS5), scheduled tasks, per-chat settings.

One connection shared by threads (sync tools run in a thread pool), guarded by a lock.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS history_chat ON history(chat_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_chat ON memories(chat_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, tags, content='memories', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags) VALUES ('delete', old.id, old.content, old.tags);
END;

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL,            -- 'message' | 'prompt'
    text TEXT NOT NULL,
    cron TEXT,                     -- NULL for one-shot
    next_run REAL,                 -- unix time; NULL when finished
    active INTEGER NOT NULL DEFAULT 1,
    created REAL NOT NULL,
    last_run REAL,
    runs INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS schedules_due ON schedules(active, next_run);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (chat_id, key)
);
"""

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def fts_query(text: str, max_terms: int = 12) -> str | None:
    """Turn free text into a safe FTS5 query: quoted terms joined with OR (no FTS syntax injection)."""
    terms: list[str] = []
    for w in _WORD_RE.findall(text.lower()):
        if len(w) < 2 or w in terms:
            continue
        terms.append(w)
        if len(terms) >= max_terms:
            break
    if not terms:
        return None
    return " OR ".join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in terms)


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # During a rolling update the old and the new instance can hold the same file for a few seconds:
        # wait for the other writer (busy timeout) instead of failing with "database is locked".
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout=30000")
            if self.path != ":memory:":
                self._retry_locked(lambda: self._conn.execute("PRAGMA journal_mode=WAL"), tolerate=True)
                self._conn.execute("PRAGMA synchronous=NORMAL")
            self._retry_locked(lambda: self._conn.executescript(SCHEMA))

    @staticmethod
    def _retry_locked(fn, attempts: int = 10, tolerate: bool = False):
        """Run ``fn``; a locked/busy database is retried (the other instance is finishing its write)."""
        for i in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if i == attempts - 1:
                    if tolerate:  # e.g. journal_mode switch: an existing WAL database does not need it
                        return None
                    raise
                time.sleep(min(0.25 * (i + 1), 2.0))

    def close(self) -> None:
        with self._lock:
            try:
                if self.path != ":memory:":
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # hand over a clean file to the next instance
            except sqlite3.Error:
                pass
            self._conn.close()

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    # ------------------------------------------------------------------ history
    def add_history(self, chat_id: int, role: str, content: str, keep: int) -> None:
        with self._lock:
            self._exec("INSERT INTO history(chat_id, role, content, ts) VALUES (?,?,?,?)",
                       (chat_id, role, content, time.time()))
            self._exec(
                "DELETE FROM history WHERE chat_id=? AND id NOT IN "
                "(SELECT id FROM history WHERE chat_id=? ORDER BY id DESC LIMIT ?)",
                (chat_id, chat_id, max(keep, 0)),
            )

    def get_history(self, chat_id: int, limit: int) -> list[dict[str, str]]:
        rows = self._all("SELECT role, content FROM history WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                         (chat_id, max(limit, 0)))
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def clear_history(self, chat_id: int) -> int:
        return self._exec("DELETE FROM history WHERE chat_id=?", (chat_id,)).rowcount

    # ------------------------------------------------------------------ memories
    def add_memory(self, chat_id: int, content: str, tags: str = "") -> int:
        cur = self._exec("INSERT INTO memories(chat_id, content, tags, created) VALUES (?,?,?,?)",
                         (chat_id, content, tags, time.time()))
        return int(cur.lastrowid)

    def count_memories(self, chat_id: int) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM memories WHERE chat_id=?", (chat_id,))
        return int(row["n"]) if row else 0

    def find_duplicate_memory(self, chat_id: int, content: str) -> dict | None:
        return self._one("SELECT id, content, tags, created FROM memories WHERE chat_id=? AND lower(content)=lower(?)",
                         (chat_id, content))

    def search_memories(self, chat_id: int, query: str, limit: int) -> list[dict]:
        q = fts_query(query)
        if not q:
            return []
        try:
            return self._all(
                "SELECT m.id, m.content, m.tags, m.created FROM memories_fts f JOIN memories m ON m.id = f.rowid "
                "WHERE memories_fts MATCH ? AND m.chat_id=? ORDER BY bm25(memories_fts) LIMIT ?",
                (q, chat_id, limit),
            )
        except sqlite3.OperationalError:
            return []

    def recent_memories(self, chat_id: int, limit: int, offset: int = 0) -> list[dict]:
        return self._all("SELECT id, content, tags, created FROM memories WHERE chat_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                         (chat_id, limit, offset))

    def get_memory(self, chat_id: int, memory_id: int) -> dict | None:
        return self._one("SELECT id, content, tags, created FROM memories WHERE chat_id=? AND id=?", (chat_id, memory_id))

    def delete_memory(self, chat_id: int, memory_id: int) -> bool:
        return self._exec("DELETE FROM memories WHERE chat_id=? AND id=?", (chat_id, memory_id)).rowcount > 0

    def clear_memories(self, chat_id: int) -> int:
        return self._exec("DELETE FROM memories WHERE chat_id=?", (chat_id,)).rowcount

    def memory_chats(self) -> list[dict]:
        """``[{chat_id, count, last}]`` for the admin UI (each chat's memories stay separate)."""
        return self._all("SELECT chat_id, COUNT(*) AS count, MAX(created) AS last FROM memories "
                         "GROUP BY chat_id ORDER BY last DESC")

    def count_all_memories(self) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM memories")
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------ schedules
    def add_schedule(self, chat_id: int, kind: str, text: str, cron: str | None, next_run: float,
                     description: str = "") -> int:
        cur = self._exec(
            "INSERT INTO schedules(chat_id, kind, text, cron, next_run, active, created, description) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (chat_id, kind, text, cron, next_run, time.time(), description),
        )
        return int(cur.lastrowid)

    def list_schedules(self, chat_id: int, include_inactive: bool = False) -> list[dict]:
        sql = "SELECT * FROM schedules WHERE chat_id=?" + ("" if include_inactive else " AND active=1")
        return self._all(sql + " ORDER BY next_run", (chat_id,))

    def all_schedules(self, include_inactive: bool = False, limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM schedules" + ("" if include_inactive else " WHERE active=1")
        order = " ORDER BY active DESC, next_run IS NULL, next_run, id DESC LIMIT ?"
        return self._all(sql + order, (max(1, limit),))

    def count_all_active_schedules(self) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM schedules WHERE active=1")
        return int(row["n"]) if row else 0

    def count_active_schedules(self, chat_id: int) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM schedules WHERE chat_id=? AND active=1", (chat_id,))
        return int(row["n"]) if row else 0

    def get_schedule(self, schedule_id: int) -> dict | None:
        return self._one("SELECT * FROM schedules WHERE id=?", (schedule_id,))

    def cancel_schedule(self, chat_id: int, schedule_id: int) -> bool:
        return self._exec("UPDATE schedules SET active=0, next_run=NULL WHERE chat_id=? AND id=? AND active=1",
                          (chat_id, schedule_id)).rowcount > 0

    def due_schedules(self, now: float) -> list[dict]:
        return self._all("SELECT * FROM schedules WHERE active=1 AND next_run IS NOT NULL AND next_run <= ? "
                         "ORDER BY next_run", (now,))

    def mark_run(self, schedule_id: int, ran_at: float, next_run: float | None) -> None:
        self._exec(
            "UPDATE schedules SET last_run=?, runs=runs+1, next_run=?, active=? WHERE id=?",
            (ran_at, next_run, 1 if next_run is not None else 0, schedule_id),
        )

    # ------------------------------------------------------------------ chat settings
    def get_setting(self, chat_id: int, key: str) -> Any:
        row = self._one("SELECT value FROM chat_settings WHERE chat_id=? AND key=?", (chat_id, key))
        return json.loads(row["value"]) if row else None

    def set_setting(self, chat_id: int, key: str, value: Any) -> None:
        if value is None:
            self._exec("DELETE FROM chat_settings WHERE chat_id=? AND key=?", (chat_id, key))
            return
        self._exec("INSERT INTO chat_settings(chat_id, key, value) VALUES (?,?,?) "
                   "ON CONFLICT(chat_id, key) DO UPDATE SET value=excluded.value",
                   (chat_id, key, json.dumps(value)))
