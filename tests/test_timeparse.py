from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ai_bot.timeparse import Cron, TimeParseError, parse_relative, parse_when

IST = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 11, 19, 8, 30, tzinfo=IST)  # Thursday


def test_cron_daily_and_weekdays():
    assert Cron.parse("0 9 * * *").next_after(NOW) == datetime(2026, 11, 19, 9, 0, tzinfo=IST)
    assert Cron.parse("0 8 * * *").next_after(NOW) == datetime(2026, 11, 20, 8, 0, tzinfo=IST)
    # Saturday 10:00 -> next is Monday for weekdays
    sat = datetime(2026, 11, 21, 10, 0, tzinfo=IST)
    assert Cron.parse("0 9 * * 1-5").next_after(sat) == datetime(2026, 11, 23, 9, 0, tzinfo=IST)


def test_cron_steps_lists_names_macros():
    assert Cron.parse("*/15 * * * *").next_after(NOW) == datetime(2026, 11, 19, 8, 45, tzinfo=IST)
    assert Cron.parse("0 9,18 * * *").next_after(datetime(2026, 11, 19, 10, 0, tzinfo=IST)).hour == 18
    assert Cron.parse("0 9 * * mon").next_after(NOW) == datetime(2026, 11, 23, 9, 0, tzinfo=IST)
    assert Cron.parse("0 0 1 jan *").next_after(NOW) == datetime(2027, 1, 1, 0, 0, tzinfo=IST)
    assert Cron.parse("@daily").next_after(NOW) == datetime(2026, 11, 20, 0, 0, tzinfo=IST)
    assert Cron.parse("0 9 * * 7").next_after(NOW).weekday() == 6  # 7 = Sunday


def test_cron_dom_or_dow():
    # day 1 OR Monday (Vixie semantics)
    nxt = Cron.parse("0 9 1 * 1").next_after(NOW)
    assert nxt == datetime(2026, 11, 23, 9, 0, tzinfo=IST)


@pytest.mark.parametrize("bad", ["", "* * * *", "61 * * * *", "* 24 * * *", "5-1 * * * *", "a b c d e"])
def test_cron_invalid(bad):
    with pytest.raises(TimeParseError):
        Cron.parse(bad)


def test_relative_tr_en():
    assert parse_relative("10 dakika sonra", NOW) == datetime(2026, 11, 19, 8, 40, tzinfo=IST)
    assert parse_relative("2 saat sonra", NOW) == datetime(2026, 11, 19, 10, 30, tzinfo=IST)
    assert parse_relative("in 3 days", NOW).day == 22
    assert parse_relative("+30m", NOW).minute == 0
    assert parse_relative("yarın", NOW) is None


def test_parse_when_variants():
    w = parse_when("2026-12-01T09:00", NOW)
    assert w.cron is None and w.first_run == datetime(2026, 12, 1, 9, 0, tzinfo=IST)
    w = parse_when("2026-12-01T06:00:00Z", NOW)
    assert w.first_run.hour == 9  # converted to TZ
    w = parse_when("0 9 * * *", NOW)
    assert w.cron is not None and w.first_run.hour == 9
    with pytest.raises(TimeParseError):
        parse_when("2020-01-01T00:00", NOW)  # past
    with pytest.raises(TimeParseError):
        parse_when("sometime soon", NOW)


def test_min_interval():
    assert Cron.parse("* * * * *").min_interval_minutes(NOW) == 1
    assert Cron.parse("0 9 * * *").min_interval_minutes(NOW) == 1440


# --- DST regressions (review #1) and range limits (review #6) ---------------------------------
BERLIN = ZoneInfo("Europe/Berlin")
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _runs(cron: Cron, start: datetime, n: int) -> list[datetime]:
    out, t = [], start
    for _ in range(n):
        t = cron.next_after(t)
        out.append(t)
    return out


@pytest.mark.parametrize("tz,day", [(BERLIN, (2026, 10, 25)), (NY, (2026, 11, 1))])
def test_fall_back_hourly_is_strictly_increasing_once_per_real_hour(tz, day):
    start = datetime(*day, 0, 0, tzinfo=tz)
    runs = _runs(Cron.parse("30 * * * *"), start, 6)
    stamps = [r.timestamp() for r in runs]
    assert all(b - a == 3600 for a, b in zip(stamps, stamps[1:]))  # every real hour, incl. the repeated one


def test_fall_back_second_pass_is_not_in_the_past():
    now = datetime(2026, 10, 25, 2, 10, tzinfo=BERLIN, fold=1)  # 02:10 CET (second pass)
    nxt = Cron.parse("30 * * * *").next_after(now)
    assert nxt.timestamp() > now.timestamp()
    assert nxt.timestamp() - now.timestamp() == 20 * 60


@pytest.mark.parametrize("tz,day,gap_hour", [(BERLIN, (2026, 3, 29), 2), (NY, (2026, 3, 8), 2)])
def test_spring_forward_skips_missing_time(tz, day, gap_hour):
    start = datetime(*day, 0, 0, tzinfo=tz)
    nxt = Cron.parse(f"30 {gap_hour} * * *").next_after(start)
    assert nxt.timestamp() > start.timestamp() and nxt.day != day[2]  # 02:30 does not exist that day
    hourly = _runs(Cron.parse("30 * * * *"), start, 4)
    assert [r.timestamp() for r in hourly] == sorted({r.timestamp() for r in hourly})


def test_scheduler_style_loop_runs_once_in_fall_back_hour():
    cron = Cron.parse("30 * * * *")
    now = datetime(2026, 10, 25, 2, 10, tzinfo=BERLIN, fold=1)
    nxt = cron.next_after(now).timestamp()
    runs, ts = 0, now.timestamp()
    for tick in range(0, 50 * 60, 20):  # 50 minutes of 20 s ticks
        t = ts + tick
        if nxt <= t:
            runs += 1
            nxt = cron.next_after(datetime.fromtimestamp(max(t, nxt), BERLIN)).timestamp()
    assert runs == 1


def test_huge_relative_time_is_an_error_not_overflow():
    for text in ("99999999999 gün sonra", "in 99999999999999 weeks", "6 yıl sonra", "2000 days"):
        with pytest.raises(TimeParseError):
            parse_when(text, NOW)
    with pytest.raises(TimeParseError):
        parse_when("2040-01-01T00:00", NOW)  # beyond ~5 years
