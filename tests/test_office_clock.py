"""Tests for office.clock (OB-06): segment edges, week rollover, DST, validation."""
from datetime import date, datetime, timedelta, timezone

import pytest

try:
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - environment guard (Windows without tzdata)
    pytest.skip("IANA tz database unavailable: install the 'tzdata' package", allow_module_level=True)

from office.clock import OfficeTime, office_time, segment_start

EPOCH = date(2026, 9, 27)  # a Sunday
DST_EPOCH = date(2026, 3, 1)  # a Sunday; covers 2026-03-08 spring-forward
FALL_EPOCH = date(2026, 11, 1)  # a Sunday and the 2026 fall-back day


def ny(*args, fold=0):
    return datetime(*args, tzinfo=NY).replace(fold=fold)


# --- segment edges ---------------------------------------------------------

@pytest.mark.parametrize(
    "hh, mm, ss, segment, phase, next_hour",
    [
        (0, 0, 0, 0, "off", 6),
        (5, 59, 59, 0, "off", 6),
        (6, 0, 0, 1, "morning", 12),
        (11, 59, 59, 1, "morning", 12),
        (12, 0, 0, 2, "build", 18),
        (17, 59, 59, 2, "build", 18),
        (18, 0, 0, 3, "ship", None),
        (23, 59, 59, 3, "ship", None),
    ],
)
def test_office_time_segment_edges_map_to_expected_segment(hh, mm, ss, segment, phase, next_hour):
    ot = office_time(ny(2026, 9, 29, hh, mm, ss), EPOCH)
    assert (ot.loop_week, ot.day_index, ot.segment, ot.phase) == (0, 2, segment, phase)
    assert ot.is_work_hours is (segment >= 1)
    expected_next = ny(2026, 9, 29, next_hour) if next_hour is not None else ny(2026, 9, 30, 0)
    assert ot.next_boundary == expected_next
    assert ot.next_boundary.tzinfo is not None


def test_office_time_returns_frozen_dataclass():
    ot = office_time(ny(2026, 9, 27, 7), EPOCH)
    assert isinstance(ot, OfficeTime)
    with pytest.raises(Exception):
        ot.segment = 2  # type: ignore[misc]


def test_office_time_accepts_other_timezones_by_instant():
    # 2026-09-27 10:00 UTC == 06:00 EDT -> morning segment starts exactly.
    ot = office_time(datetime(2026, 9, 27, 10, tzinfo=timezone.utc), EPOCH)
    assert (ot.loop_week, ot.day_index, ot.segment) == (0, 0, 1)
    ot = office_time(datetime(2026, 9, 27, 9, 59, 59, tzinfo=timezone.utc), EPOCH)
    assert ot.segment == 0


# --- week rollover ---------------------------------------------------------

@pytest.mark.parametrize(
    "now, week, day, segment",
    [
        (ny(2026, 9, 27, 0), 0, 0, 0),  # epoch itself
        (ny(2026, 10, 3, 23, 59, 59), 0, 6, 3),  # last second of week 0
        (ny(2026, 10, 4, 0), 1, 0, 0),  # Sunday 00:00 -> week 1
        (ny(2026, 10, 10, 23, 59, 59), 1, 6, 3),
        (ny(2026, 10, 11, 0), 2, 0, 0),
        (ny(2027, 9, 26, 12), 52, 0, 2),  # a year later
    ],
)
def test_office_time_week_rollover_at_sunday_midnight(now, week, day, segment):
    ot = office_time(now, EPOCH)
    assert (ot.loop_week, ot.day_index, ot.segment) == (week, day, segment)


def test_office_time_saturday_ship_next_boundary_is_next_week_start():
    ot = office_time(ny(2026, 10, 3, 20), EPOCH)
    assert ot.next_boundary == ny(2026, 10, 4, 0)
    assert office_time(ot.next_boundary, EPOCH).loop_week == 1


# --- DST -------------------------------------------------------------------

@pytest.mark.parametrize(
    "now, segment, next_boundary",
    [
        # Spring forward 2026-03-08: 02:00 EST -> 03:00 EDT (23 h day).
        (ny(2026, 3, 8, 0), 0, ny(2026, 3, 8, 6)),
        (ny(2026, 3, 8, 1, 59, 59), 0, ny(2026, 3, 8, 6)),
        (ny(2026, 3, 8, 3), 0, ny(2026, 3, 8, 6)),
        (ny(2026, 3, 8, 6), 1, ny(2026, 3, 8, 12)),
        (ny(2026, 3, 8, 18), 3, ny(2026, 3, 9, 0)),
        (ny(2026, 3, 8, 23, 59, 59), 3, ny(2026, 3, 9, 0)),
    ],
)
def test_office_time_spring_forward_day_uses_wall_clock(now, segment, next_boundary):
    ot = office_time(now, DST_EPOCH)
    assert (ot.loop_week, ot.day_index, ot.segment) == (1, 0, segment)
    assert ot.next_boundary == next_boundary


def test_office_time_spring_forward_segment0_is_five_real_hours():
    start = segment_start(1, 0, 0, DST_EPOCH)
    end = segment_start(1, 0, 1, DST_EPOCH)
    assert end.astimezone(timezone.utc) - start.astimezone(timezone.utc) == timedelta(hours=5)
    assert start.utcoffset() == timedelta(hours=-5)
    assert end.utcoffset() == timedelta(hours=-4)


@pytest.mark.parametrize(
    "now, segment, next_boundary",
    [
        # Fall back 2026-11-01: 02:00 EDT -> 01:00 EST (25 h day); 01:xx occurs twice.
        (ny(2026, 11, 1, 0), 0, ny(2026, 11, 1, 6)),
        (ny(2026, 11, 1, 1, 30, fold=0), 0, ny(2026, 11, 1, 6)),
        (ny(2026, 11, 1, 1, 30, fold=1), 0, ny(2026, 11, 1, 6)),
        (ny(2026, 11, 1, 5, 59, 59), 0, ny(2026, 11, 1, 6)),
        (ny(2026, 11, 1, 6), 1, ny(2026, 11, 1, 12)),
        (ny(2026, 11, 1, 23, 59, 59), 3, ny(2026, 11, 2, 0)),
    ],
)
def test_office_time_fall_back_day_uses_wall_clock(now, segment, next_boundary):
    ot = office_time(now, FALL_EPOCH)
    assert (ot.loop_week, ot.day_index, ot.segment) == (0, 0, segment)
    assert ot.next_boundary == next_boundary


def test_office_time_fall_back_segment0_is_seven_real_hours():
    start = segment_start(0, 0, 0, FALL_EPOCH)
    end = segment_start(0, 0, 1, FALL_EPOCH)
    assert end.astimezone(timezone.utc) - start.astimezone(timezone.utc) == timedelta(hours=7)


def test_office_time_week_count_is_calendar_based_across_dst():
    # From an epoch in EDT, a Sunday 00:00 in EST must still be an exact week start.
    ot = office_time(ny(2026, 11, 8, 0), EPOCH)
    assert (ot.loop_week, ot.day_index, ot.segment) == (6, 0, 0)
    ot = office_time(ny(2026, 11, 7, 23, 59, 59), EPOCH)
    assert (ot.loop_week, ot.day_index, ot.segment) == (5, 6, 3)


# --- validation ------------------------------------------------------------

def test_office_time_before_epoch_raises_value_error():
    with pytest.raises(ValueError, match="before the epoch"):
        office_time(ny(2026, 9, 26, 23, 59, 59), EPOCH)


@pytest.mark.parametrize("epoch", [date(2026, 9, 28), date(2026, 9, 26), date(2026, 3, 8) + timedelta(days=3)])
def test_office_time_non_sunday_epoch_raises_value_error(epoch):
    with pytest.raises(ValueError, match="Sunday"):
        office_time(ny(2026, 10, 1, 12), epoch)


def test_office_time_datetime_epoch_raises_value_error():
    with pytest.raises(ValueError, match="date"):
        office_time(ny(2026, 10, 1, 12), datetime(2026, 9, 27))


def test_office_time_naive_datetime_raises_value_error():
    with pytest.raises(ValueError, match="timezone-aware"):
        office_time(datetime(2026, 10, 1, 12), EPOCH)


def test_office_time_unknown_zone_raises_value_error():
    with pytest.raises(ValueError, match="time zone"):
        office_time(ny(2026, 10, 1, 12), EPOCH, tz="Not/AZone")


# --- segment_start inverse -------------------------------------------------

@pytest.mark.parametrize("epoch", [EPOCH, DST_EPOCH, FALL_EPOCH])
@pytest.mark.parametrize("week", [0, 1, 3])
def test_segment_start_roundtrips_through_office_time(epoch, week):
    for day in range(7):
        for seg in range(4):
            start = segment_start(week, day, seg, epoch)
            ot = office_time(start, epoch)
            assert (ot.loop_week, ot.day_index, ot.segment) == (week, day, seg)
            before = office_time(start - timedelta(seconds=1), epoch) if (week, day, seg) != (0, 0, 0) else None
            if before is not None:
                assert before.next_boundary == start


@pytest.mark.parametrize("epoch", [EPOCH, DST_EPOCH, FALL_EPOCH])
def test_office_time_next_boundary_roundtrips_to_segment_start(epoch):
    now = segment_start(0, 0, 0, epoch)
    for _ in range(4 * 7 * 2):
        ot = office_time(now, epoch)
        assert segment_start(ot.loop_week, ot.day_index, ot.segment, epoch) == now
        now = ot.next_boundary


def test_segment_start_returns_local_wall_clock_times():
    assert segment_start(0, 0, 0, EPOCH) == ny(2026, 9, 27, 0)
    assert segment_start(1, 3, 2, EPOCH) == ny(2026, 10, 7, 12)
    assert segment_start(1, 0, 3, DST_EPOCH).hour == 18


@pytest.mark.parametrize(
    "args",
    [(-1, 0, 0), (0, 7, 0), (0, -1, 0), (0, 0, 4), (0, 0, -1), (0, 1.0, 0), (True, 0, 0)],
)
def test_segment_start_out_of_range_raises_value_error(args):
    with pytest.raises(ValueError):
        segment_start(*args, EPOCH)


def test_segment_start_non_sunday_epoch_raises_value_error():
    with pytest.raises(ValueError, match="Sunday"):
        segment_start(0, 0, 0, date(2026, 9, 28))
