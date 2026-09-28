"""WP-03 tests for app/character/clock.py (plan §2, D-01, D-24).

Frozen test list (playbook §4 WP-03, items 8-13). The cases are those of the
validated reference (`.claude/prompts/character_v4_plan_validation.py:69-112`)
moved to the OB-41 office epoch, Sunday 2026-09-27 (the same epoch as
app/office/weekly_reset.py). Week 1 starts at 00:00 America/New_York on it.

Office epoch landmarks: week 1 = Sun 2026-09-27 .. Sat 2026-10-03; DST ends
Sun 2026-11-01 (week 6, 169 real hours); DST starts Sun 2027-03-14 (week 25,
167 real hours).
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pending import require

clock = require("character.clock", "app/character/clock.py")

NY = ZoneInfo("America/New_York")
EPOCH = date(2026, 9, 27)


@pytest.fixture
def loop_clock():
    return clock.LoopClock(EPOCH, "America/New_York")


def _real_hours(start, end):
    return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 3600


# T03.8
def test_saturday_last_second_is_week_1_and_sunday_midnight_is_week_2(loop_clock):
    saturday = datetime(2026, 10, 3, 23, 59, 59, tzinfo=NY)
    sunday = datetime(2026, 10, 4, 0, 0, 0, tzinfo=NY)
    assert loop_clock.position(saturday) == (1, date(2026, 10, 3))
    assert loop_clock.position(sunday) == (2, date(2026, 10, 4))
    assert loop_clock.position(datetime(2026, 9, 27, 0, 0, tzinfo=NY)) == (1, EPOCH)


# T03.9
def test_0200z_sunday_is_still_saturday_in_new_york(loop_clock):
    utc_edge = datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)  # 22:00 Sat EDT
    assert loop_clock.position(utc_edge) == (1, date(2026, 10, 3))


# T03.10
def test_dst_end_week_is_169h_and_dst_start_week_is_167h(loop_clock):
    week, day = loop_clock.position(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
    assert (week, day) == (6, date(2026, 11, 1))
    start, end = loop_clock.week_bounds(week)
    assert _real_hours(start, end) == 169

    week, _ = loop_clock.position(datetime(2027, 3, 14, 12, 0, tzinfo=timezone.utc))
    assert week == 25
    start, end = loop_clock.week_bounds(week)
    assert _real_hours(start, end) == 167

    ordinary_start, ordinary_end = loop_clock.week_bounds(2)
    assert _real_hours(ordinary_start, ordinary_end) == 168


# T03.11
def test_week_bounds_and_position_round_trip_for_59_weeks(loop_clock):
    for week in range(1, 60):
        start, end = loop_clock.week_bounds(week)
        assert start.tzinfo is not None and end.tzinfo is not None
        assert start.astimezone(NY).weekday() == 6
        assert (start.astimezone(NY).hour, start.astimezone(NY).minute) == (0, 0)
        assert loop_clock.position(start)[0] == week
        assert loop_clock.position(end - timedelta(seconds=1))[0] == week
        assert loop_clock.position(end)[0] == week + 1
        if week > 1:
            assert loop_clock.week_bounds(week - 1)[1] == start
    day_start, day_end = loop_clock.day_bounds(date(2026, 11, 1))
    assert day_start == datetime(2026, 11, 1, 0, 0, tzinfo=NY)
    assert day_end == datetime(2026, 11, 2, 0, 0, tzinfo=NY)
    assert _real_hours(day_start, day_end) == 25


# T03.12
def test_naive_datetime_raises_value_error(loop_clock):
    naive = datetime(2026, 10, 4, 12, 0)
    with pytest.raises(ValueError):
        loop_clock.position(naive)
    with pytest.raises(ValueError):
        loop_clock.previous_day(naive)
    with pytest.raises(ValueError):
        loop_clock.is_week_start(naive)


# T03.13
def test_previous_day_just_after_sunday_midnight_is_saturday(loop_clock):
    now = datetime(2026, 10, 4, 0, 0, 30, tzinfo=NY)
    assert loop_clock.previous_day(now) == date(2026, 10, 3)
    assert loop_clock.previous_day(now.astimezone(timezone.utc)) == date(2026, 10, 3)
