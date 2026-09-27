"""Office world clock: maps a real instant onto the ashiorid_office week.

The weekly loop starts at the epoch Sunday 00:00 local wall clock (America/New_York
by default). Each day has four 6 h segments on local wall-clock boundaries:

    s0 00:00-06:00 off | s1 06:00-12:00 morning | s2 12:00-18:00 build | s3 18:00-24:00 ship

Weeks and days are counted by local *calendar date*, never by dividing elapsed
seconds, so DST days (23 h / 25 h) do not shift the grid. Pure module: no I/O.
See docs/office_clock.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
TRACE = 5

DEFAULT_TZ = "America/New_York"
SEGMENTS_PER_DAY = 4
SEGMENT_HOURS = 6
DAYS_PER_WEEK = 7
PHASES = ("off", "morning", "build", "ship")
_SUNDAY = 6  # date.weekday() value for Sunday


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


@dataclass(frozen=True)
class OfficeTime:
    """Position of an instant in the office week."""

    loop_week: int
    day_index: int  # 0 = Sunday .. 6 = Saturday
    segment: int  # 0..3
    phase: str  # off | morning | build | ship
    is_work_hours: bool
    next_boundary: datetime  # tz-aware, start of the next segment


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError on bad keys
        log.error("office clock: unknown time zone tz=%r error=%s", tz, exc)
        raise ValueError(
            f"unknown time zone {tz!r} (on Windows the 'tzdata' package is required)"
        ) from exc


def _check_epoch(epoch: date) -> None:
    if isinstance(epoch, datetime) or not isinstance(epoch, date):
        log.error("office clock: epoch must be a date epoch=%r", epoch)
        raise ValueError(f"epoch must be a datetime.date, got {type(epoch).__name__}")
    if epoch.weekday() != _SUNDAY:
        log.error("office clock: epoch is not a Sunday epoch=%s", epoch)
        raise ValueError(f"epoch must be a Sunday, got {epoch} ({epoch:%A})")


def _wall(day: date, hour: int, zone: ZoneInfo, after: datetime | None = None) -> datetime:
    """Resolve local wall time `day hour:00` to a real aware instant in `zone`.

    Ambiguous times (fall-back) resolve to the first occurrence; nonexistent times
    (spring-forward gap) resolve to the instant the clock jumps. If `after` is given
    and the first occurrence is not strictly later, the second occurrence is used.
    """
    naive = datetime.combine(day, time(hour))
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        resolved = candidate.astimezone(timezone.utc).astimezone(zone)
        if after is None or resolved > after:
            return resolved
    log.debug("office clock: no wall time after %s for %s %02d:00", after, day, hour)
    return resolved


def office_time(now: datetime, epoch: date, tz: str = DEFAULT_TZ) -> OfficeTime:
    """Place `now` (tz-aware) in the office week anchored at Sunday `epoch`.

    Raises ValueError for naive `now`, a non-Sunday epoch, an unknown zone, or an
    instant before the epoch Sunday 00:00 local.
    """
    _trace("office_time enter now=%s epoch=%s tz=%s", now, epoch, tz)
    if not isinstance(now, datetime):
        log.error("office clock: now must be a datetime now=%r", now)
        raise ValueError(f"now must be a datetime, got {type(now).__name__}")
    if now.tzinfo is None or now.utcoffset() is None:
        log.error("office clock: naive datetime rejected now=%s", now)
        raise ValueError("now must be timezone-aware")
    _check_epoch(epoch)
    zone = _zone(tz)

    local = now.astimezone(zone)
    days = (local.date() - epoch).days
    if days < 0:
        log.error("office clock: now before epoch now=%s epoch=%s", local, epoch)
        raise ValueError(f"{local.isoformat()} is before the epoch {epoch} 00:00 {tz}")
    log.debug("office clock: local=%s days_since_epoch=%d", local, days)

    loop_week, day_index = divmod(days, DAYS_PER_WEEK)
    segment = local.hour // SEGMENT_HOURS
    if segment + 1 < SEGMENTS_PER_DAY:
        next_day, next_hour = local.date(), (segment + 1) * SEGMENT_HOURS
    else:
        next_day, next_hour = local.date() + timedelta(days=1), 0
    next_boundary = _wall(next_day, next_hour, zone, after=local)

    result = OfficeTime(
        loop_week=loop_week,
        day_index=day_index,
        segment=segment,
        phase=PHASES[segment],
        is_work_hours=segment >= 1,
        next_boundary=next_boundary,
    )
    _trace("office_time exit result=%s", result)
    return result


def segment_start(
    loop_week: int, day_index: int, segment: int, epoch: date, tz: str = DEFAULT_TZ
) -> datetime:
    """Inverse of office_time: the tz-aware instant a segment starts (for schedulers)."""
    _trace(
        "segment_start enter week=%s day=%s segment=%s epoch=%s tz=%s",
        loop_week, day_index, segment, epoch, tz,
    )
    for name, value, hi in (
        ("loop_week", loop_week, None),
        ("day_index", day_index, DAYS_PER_WEEK - 1),
        ("segment", segment, SEGMENTS_PER_DAY - 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            log.error("segment_start: %s must be int value=%r", name, value)
            raise ValueError(f"{name} must be an int, got {value!r}")
        if value < 0 or (hi is not None and value > hi):
            log.error("segment_start: %s out of range value=%r", name, value)
            raise ValueError(f"{name} out of range: {value}")
    _check_epoch(epoch)
    zone = _zone(tz)

    day = epoch + timedelta(days=loop_week * DAYS_PER_WEEK + day_index)
    result = _wall(day, segment * SEGMENT_HOURS, zone)
    _trace("segment_start exit result=%s", result)
    return result
