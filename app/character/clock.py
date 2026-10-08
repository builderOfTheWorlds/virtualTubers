"""Loop clock of the character v4 memory loop (plan §2, D-01, D-24).

Week N starts at 00:00 local wall clock (America/New_York for the office) on
`epoch + 7*(N-1)` days; `loop_day` is the local calendar date. This is THE ONLY
code that turns a timestamp into a loop week or day. Bounds are always built
with `datetime.combine(day, time(0), tzinfo=zone)` so DST weeks are 167/169
real hours, never by adding hours to an aware datetime. Naive datetimes raise
ValueError. Never calls datetime.now(): callers pass `now`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_SUNDAY = 6  # date.weekday() value for Sunday


def _require_aware(ts) -> None:
    if not isinstance(ts, datetime):
        log.debug("loop clock: not a datetime value=%r", ts)
        raise ValueError(f"expected a datetime, got {type(ts).__name__}")
    if ts.tzinfo is None or ts.utcoffset() is None:
        log.debug("loop clock: naive datetime rejected ts=%s", ts)
        raise ValueError("datetime must be timezone-aware")


class LoopClock:
    """Maps aware instants onto (loop week, local day) for one epoch and zone."""

    def __init__(self, epoch: date, tz: str):
        if isinstance(epoch, datetime) or not isinstance(epoch, date):
            log.debug("loop clock: epoch must be a date epoch=%r", epoch)
            raise ValueError(f"epoch must be a datetime.date, got {type(epoch).__name__}")
        if epoch.weekday() != _SUNDAY:
            log.debug("loop clock: epoch is not a Sunday epoch=%s", epoch)
            raise ValueError(f"epoch must be a Sunday, got {epoch} ({epoch:%A})")
        try:
            zone = ZoneInfo(tz)
        except Exception as exc:  # ZoneInfoNotFoundError, ValueError, TypeError
            log.debug("loop clock: unknown time zone tz=%r", tz)
            raise ValueError(f"unknown time zone {tz!r}") from exc
        self.epoch = epoch
        self.tz = tz
        self.zone = zone
        log.debug("loop clock epoch=%s tz=%s", epoch, tz)

    def __repr__(self) -> str:
        return f"LoopClock(epoch={self.epoch!r}, tz={self.tz!r})"

    def _local_date(self, ts: datetime) -> date:
        _require_aware(ts)
        return ts.astimezone(self.zone).date()

    def position(self, ts: datetime) -> tuple[int, date]:
        """(week, local day). Before the epoch gives week <= 0 (no raise)."""
        day = self._local_date(ts)
        return (day - self.epoch).days // 7 + 1, day

    def week_bounds(self, week: int) -> tuple[datetime, datetime]:
        """[start, end) of loop week `week` as aware datetimes in the zone."""
        start_day = self.epoch + timedelta(days=7 * (week - 1))
        start = datetime.combine(start_day, time(0), tzinfo=self.zone)
        end = datetime.combine(start_day + timedelta(days=7), time(0), tzinfo=self.zone)
        return start, end

    def day_bounds(self, day: date) -> tuple[datetime, datetime]:
        """Local midnight of `day` and of the next day (23/24/25 real hours)."""
        start = datetime.combine(day, time(0), tzinfo=self.zone)
        end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=self.zone)
        return start, end

    def previous_day(self, now: datetime) -> date:
        """The local calendar date before now's local date (what the nightly job summarises)."""
        return self._local_date(now) - timedelta(days=1)

    def is_week_start(self, now: datetime) -> bool:
        """True when now's local date is a Sunday (the first day of a loop week)."""
        return self._local_date(now).weekday() == _SUNDAY
