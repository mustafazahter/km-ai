"""Persistent scheduler: one asyncio loop, tasks in SQLite, times in ``TZ``.

* ``kind="message"``: the text is sent as is.
* ``kind="prompt"``: the text is given to the AI (with tools) and the answer is sent.
* Cron tasks are re-armed after each run; one-shot tasks become inactive.
* After a restart: overdue one-shot tasks run once (late, marked as such); overdue cron tasks
  run once and then continue from "now" (missed runs are not replayed one by one).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from stack_kit import ToolError

from .timeparse import Cron, TimeParseError, parse_when

if TYPE_CHECKING:
    from .core import AiBot

log = logging.getLogger("ai_bot.scheduler")

KINDS = ("message", "prompt")
LATE_NOTE_AFTER = 120  # seconds
MIN_ONE_SHOT_SECONDS = 60  # "in 5 seconds" loops are refused


class Scheduler:
    def __init__(self, app: "AiBot"):
        self.app = app
        self._wake = asyncio.Event()
        self._running = False
        self._tasks: set[asyncio.Task] = set()

    def now(self) -> datetime:
        return datetime.now(self.app.settings.zone)

    # ------------------------------------------------------------------ API used by tools/commands
    def add(self, chat_id: int, when: str, text: str, kind: str = "message", description: str = "") -> dict:
        s = self.app.settings
        kind = (kind or "message").lower()
        if kind not in KINDS:
            raise ToolError("kind must be 'message' or 'prompt'", code="invalid_args")
        text = (text or "").strip()
        if not text:
            raise ToolError("text is empty", code="invalid_args")
        if len(text) > 2000:
            raise ToolError("text is too long (max 2000 characters)", code="invalid_args")
        # active = repeating tasks + one-time tasks that have not run yet
        if self.app.db.count_active_schedules(chat_id) >= s.schedule_max_per_chat:
            raise ToolError(f"too many active tasks (max {s.schedule_max_per_chat}); cancel one first", code="limit")
        now = self.now()
        try:
            w = parse_when(when, now)
        except TimeParseError as exc:
            raise ToolError(str(exc), code="invalid_time") from None
        if w.cron is None and (w.first_run - now).total_seconds() < MIN_ONE_SHOT_SECONDS:
            raise ToolError(f"one-time tasks must be at least {MIN_ONE_SHOT_SECONDS} seconds in the future",
                            code="invalid_time")
        if w.cron is not None and w.cron.min_interval_minutes(now) < s.schedule_min_interval_minutes:
            raise ToolError(f"repeating tasks must be at least {s.schedule_min_interval_minutes} minutes apart",
                            code="invalid_time")
        sid = self.app.db.add_schedule(chat_id, kind, text, w.cron.expr if w.cron else None,
                                       w.first_run.timestamp(), description[:200])
        self._wake.set()
        return self._public(self.app.db.get_schedule(sid))

    def list(self, chat_id: int) -> list[dict]:
        return [self._public(r) for r in self.app.db.list_schedules(chat_id)]

    def cancel(self, chat_id: int, schedule_id: int) -> dict:
        if not self.app.db.cancel_schedule(chat_id, schedule_id):
            raise ToolError(f"no active task with id {schedule_id}", code="not_found")
        return {"id": schedule_id, "cancelled": True}

    def _public(self, row: dict | None) -> dict:
        assert row is not None
        tz = self.app.settings.zone
        nxt = datetime.fromtimestamp(row["next_run"], tz).strftime("%Y-%m-%d %H:%M") if row["next_run"] else None
        return {"id": row["id"], "kind": row["kind"], "text": row["text"], "cron": row["cron"],
                "next_run": nxt, "timezone": self.app.settings.tz, "repeating": bool(row["cron"]),
                "description": row.get("description") or ""}

    # ------------------------------------------------------------------ loop
    async def run_forever(self) -> None:
        self._running = True
        log.info("scheduler started (tick %.0fs, TZ=%s)", self.app.settings.schedule_tick, self.app.settings.tz)
        try:
            while True:
                try:
                    await self.run_due()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("scheduler tick failed")
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.app.settings.schedule_tick)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False

    async def run_due(self, now_ts: float | None = None) -> int:
        """Run every due task once. Returns how many ran."""
        now_ts = time.time() if now_ts is None else now_ts
        due = self.app.db.due_schedules(now_ts)
        for row in due:
            nxt: float | None = None
            if row["cron"]:
                try:
                    cron = Cron.parse(row["cron"])
                    base = datetime.fromtimestamp(max(now_ts, row["next_run"]), self.app.settings.zone)
                    nxt = cron.next_after(base).timestamp()
                    while nxt <= now_ts:  # defensive: never re-arm into the past (DST etc.)
                        nxt = cron.next_after(datetime.fromtimestamp(nxt, self.app.settings.zone)).timestamp()
                except TimeParseError:
                    log.error("task %s has an invalid cron; deactivating", row["id"])
            # re-arm BEFORE running so a crash or slow AI never runs the task twice
            self.app.db.mark_run(row["id"], now_ts, nxt)
            late = now_ts - row["next_run"] > LATE_NOTE_AFTER
            task = asyncio.create_task(self._execute(row, late))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return len(due)

    async def drain(self) -> None:
        """Wait for running task executions (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _execute(self, row: dict, late: bool) -> None:
        chat_id = row["chat_id"]
        prefix = "(gecikmeli / late) " if late else ""
        self.app.stack.emit("schedule.fired", {"id": row["id"], "chat_id": chat_id, "kind": row["kind"],
                                               "late": late, "repeating": bool(row["cron"])})
        try:
            if row["kind"] == "prompt":
                answer = await self.app.run_ai(chat_id, row["text"], scheduled=True)
                await self.app.deliver(chat_id, f"{prefix}{row.get('description') or 'Zamanlanmış görev'}\n\n{answer}")
            else:
                await self.app.deliver(chat_id, f"{prefix}{row['text']}")
        except Exception:
            log.exception("scheduled task %s failed", row["id"])
            try:
                await self.app.deliver(chat_id, f"Zamanlanmış görev #{row['id']} çalışırken hata oluştu.")
            except Exception:
                pass
