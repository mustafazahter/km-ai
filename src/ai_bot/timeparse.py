"""Small, dependency-free time parsing for schedules.

* :class:`Cron` — 5-field cron (``m h dom mon dow``) with ``*``, lists, ranges, steps, names
  (``jan``..``dec``, ``sun``..``sat``) and ``@hourly/@daily/@weekly/@monthly/@yearly``.
  Day-of-month and day-of-week follow Vixie cron: if both are restricted, either may match.
* :func:`parse_relative` — "10 dakika sonra", "2 saat sonra", "in 10 minutes", "3 gün sonra".
* :func:`parse_when` — ISO time, relative phrase or cron expression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MONTHS = {n: i for i, n in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
DAYS = {n: i for i, n in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
MACROS = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *",
}


class TimeParseError(ValueError):
    pass


_UTC = ZoneInfo("UTC")
MAX_HORIZON = timedelta(days=366 * 5)  # no schedule further than ~5 years ahead


def _parse_field(spec: str, lo: int, hi: int, names: dict[str, int] | None) -> set[int]:
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip().lower()
        if not part:
            raise TimeParseError(f"empty cron field part in {spec!r}")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise TimeParseError(f"invalid step in {spec!r}")
            step = int(step_s)
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _value(a, names, lo, hi), _value(b, names, lo, hi)
            if start > end:
                raise TimeParseError(f"invalid range {part!r}")
        else:
            start = _value(part, names, lo, hi)
            end = hi if step > 1 else start
        values.update(range(start, end + 1, step))
    return values


def _value(token: str, names: dict[str, int] | None, lo: int, hi: int) -> int:
    token = token.strip().lower()
    if names and token[:3] in names and not token.isdigit():
        return names[token[:3]]
    if not token.isdigit():
        raise TimeParseError(f"invalid cron value {token!r}")
    v = int(token)
    if not lo <= v <= hi:
        raise TimeParseError(f"cron value {v} out of range {lo}-{hi}")
    return v


@dataclass(frozen=True)
class Cron:
    expr: str
    minutes: frozenset[int]
    hours: frozenset[int]
    dom: frozenset[int]
    months: frozenset[int]
    dow: frozenset[int]
    dom_any: bool
    dow_any: bool

    @classmethod
    def parse(cls, expr: str) -> "Cron":
        raw = " ".join(expr.strip().split())
        text = MACROS.get(raw.lower(), raw)
        fields = text.split(" ")
        if len(fields) != 5:
            raise TimeParseError("cron needs 5 fields: minute hour day-of-month month day-of-week")
        m, h, d, mo, w = fields
        return cls(
            expr=raw,
            minutes=frozenset(_parse_field(m, 0, 59, None)),
            hours=frozenset(_parse_field(h, 0, 23, None)),
            dom=frozenset(_parse_field(d, 1, 31, None)),
            months=frozenset(_parse_field(mo, 1, 12, MONTHS)),
            dow=frozenset(v % 7 for v in _parse_field(w, 0, 7, DAYS)),  # 0 and 7 = Sunday
            dom_any=d.strip() in ("*", "?"),
            dow_any=w.strip() in ("*", "?"),
        )

    def _day_ok(self, dt: datetime) -> bool:
        dom_ok = dt.day in self.dom
        dow_ok = (dt.isoweekday() % 7) in self.dow
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow_ok
        if self.dow_any:
            return dom_ok
        return dom_ok or dow_ok

    def next_after(self, after: datetime) -> datetime:
        """Next matching minute strictly after ``after`` (aware datetime; result in the same zone).

        DST safe: candidates are compared as real instants (UTC timestamps), never as wall times.
        Wall times that do not exist (spring forward gap) are skipped; wall times that happen
        twice (fall back) are considered for both occurrences (``fold`` 0 and 1), so an hourly
        cron keeps running once per real hour and never returns an instant in the past.
        """
        tz = after.tzinfo
        if tz is None:
            raise TimeParseError("next_after needs an aware datetime")
        after_ts = after.timestamp()
        # start one hour earlier in wall time so the repeated hour after a fall back is not skipped
        t = after.replace(tzinfo=None, second=0, microsecond=0, fold=0) - timedelta(hours=1)
        limit = t + timedelta(days=366 * 5)
        while t <= limit:
            if t.month not in self.months:
                t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
                continue
            if not self._day_ok(t):
                t = t.replace(hour=0, minute=0) + timedelta(days=1)
                continue
            if t.hour not in self.hours:
                t = t.replace(minute=0) + timedelta(hours=1)
                continue
            if t.minute not in self.minutes:
                t += timedelta(minutes=1)
                continue
            instants = []
            for fold in (0, 1):
                local = t.replace(tzinfo=tz, fold=fold)
                utc = local.astimezone(_UTC)
                if utc.astimezone(tz).replace(tzinfo=None, fold=0) != t:
                    continue  # does not exist (DST gap)
                if utc.timestamp() > after_ts:
                    instants.append(utc)
            if instants:
                return min(instants).astimezone(tz)
            t += timedelta(minutes=1)
        raise TimeParseError(f"cron {self.expr!r} never matches")

    def min_interval_minutes(self, start: datetime, samples: int = 6) -> float:
        """Smallest gap between the next few runs (to reject too-frequent schedules)."""
        runs = [self.next_after(start)]
        for _ in range(samples - 1):
            runs.append(self.next_after(runs[-1]))
        return min((b - a).total_seconds() / 60 for a, b in zip(runs, runs[1:]))


_UNITS = {
    "saniye": 1, "sn": 1, "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "dakika": 60, "dk": 60, "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "saat": 3600, "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "gün": 86400, "gun": 86400, "day": 86400, "days": 86400, "d": 86400,
    "hafta": 604800, "week": 604800, "weeks": 604800, "w": 604800,
}
_REL_TR = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zçğıöşü]+)\s+sonra\s*$", re.IGNORECASE)
_REL_EN = re.compile(r"^\s*(?:in\s+)?(\d+(?:[.,]\d+)?)\s*([a-z]+)(?:\s+from\s+now|\s+later)?\s*$", re.IGNORECASE)
_REL_PLUS = re.compile(r"^\s*\+\s*(\d+(?:[.,]\d+)?)\s*([a-z]+)\s*$", re.IGNORECASE)


def parse_relative(text: str, now: datetime) -> datetime | None:
    for rx in (_REL_TR, _REL_PLUS, _REL_EN):
        m = rx.match(text)
        if not m:
            continue
        unit = m.group(2).lower()
        if unit not in _UNITS:
            continue
        amount = float(m.group(1).replace(",", "."))
        if amount <= 0:
            raise TimeParseError("relative time must be positive")
        seconds = amount * _UNITS[unit]
        if seconds > MAX_HORIZON.total_seconds():
            raise TimeParseError("relative time is too far in the future (max ~5 years)")
        try:
            return now + timedelta(seconds=seconds)
        except (OverflowError, ValueError):
            raise TimeParseError("relative time is out of range") from None
    return None


def parse_iso(text: str, zone: ZoneInfo) -> datetime | None:
    t = text.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}", t):
        return None
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise TimeParseError(f"invalid ISO time {text!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    return dt


@dataclass
class When:
    first_run: datetime
    cron: Cron | None = None


def parse_when(text: str, now: datetime) -> When:
    """``now`` must be aware (its zone is used for naive ISO times and cron)."""
    text = (text or "").strip()
    if not text:
        raise TimeParseError("empty time")
    zone = now.tzinfo if isinstance(now.tzinfo, ZoneInfo) else ZoneInfo("UTC")
    iso = parse_iso(text, zone)
    if iso is not None:
        if iso <= now:
            raise TimeParseError("that time is in the past")
        if iso - now > MAX_HORIZON:
            raise TimeParseError("that time is too far in the future (max ~5 years)")
        return When(iso.astimezone(zone))
    rel = parse_relative(text, now)
    if rel is not None:
        return When(rel)
    try:
        cron = Cron.parse(text)
    except TimeParseError as exc:
        raise TimeParseError(
            f"could not understand {text!r}: use a cron expression ('0 9 * * *'), "
            f"a relative time ('10 dakika sonra', 'in 2 hours') or ISO time ('2026-12-01T09:00') [{exc}]"
        ) from None
    return When(cron.next_after(now), cron)
