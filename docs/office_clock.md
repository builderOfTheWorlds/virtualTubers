# office.clock — Office world clock

## Overview

`app/office/clock.py` maps a real instant onto the ashiorid_office week (OB-06; design in
`.claude/prompts/office_campaign_plan.md` §4). The weekly loop starts at the **epoch Sunday
00:00 local** (America/New_York by default). Every day has four 6 h segments on **local
wall-clock** boundaries:

| Segment | Local hours | Phase | Work hours |
|---|---|---|---|
| 0 | 00:00–06:00 | `off` | no |
| 1 | 06:00–12:00 | `morning` | yes |
| 2 | 12:00–18:00 | `build` | yes |
| 3 | 18:00–24:00 | `ship` | yes |

Weeks and days are counted from the epoch by **local calendar date**, not by dividing elapsed
seconds, so DST days never shift the grid. On the spring-forward day (2026-03-08) segment 0 lasts
5 real hours; on the fall-back day (2026-11-01) it lasts 7. All other segments are 6 h.

Pure module: no Kafka, no I/O, no global state.

## Signature

```python
@dataclass(frozen=True)
class OfficeTime:
    loop_week: int        # 0 = the week starting at the epoch Sunday
    day_index: int        # 0 = Sunday .. 6 = Saturday
    segment: int          # 0..3
    phase: str            # "off" | "morning" | "build" | "ship"
    is_work_hours: bool   # segment >= 1
    next_boundary: datetime  # tz-aware start of the next segment

def office_time(now: datetime, epoch: date, tz: str = "America/New_York") -> OfficeTime
def segment_start(loop_week: int, day_index: int, segment: int,
                  epoch: date, tz: str = "America/New_York") -> datetime
```

## Parameters

| Name | Type | Required | Constraints |
|---|---|---|---|
| `now` | `datetime` | yes | must be tz-aware (any zone; it is converted to `tz`) and not before the epoch Sunday 00:00 local |
| `epoch` | `date` | yes | a plain `date` (not a `datetime`) that falls on a Sunday (v4 D-01) |
| `tz` | `str` | no, default `"America/New_York"` | IANA zone key |
| `loop_week` | `int` | yes (`segment_start`) | `>= 0` |
| `day_index` | `int` | yes (`segment_start`) | `0..6` |
| `segment` | `int` | yes (`segment_start`) | `0..3` |

## Return Value

- `office_time` → `OfficeTime`. `next_boundary` is a `datetime` in `tz`. For segment 3 it is the next
  day's 00:00, and for Saturday segment 3 that is the start of the next loop week.
- `segment_start` → a tz-aware `datetime` in `tz` for the segment's local start time. It is the
  exact inverse of `office_time`: `office_time(segment_start(w, d, s, e), e)` gives `(w, d, s)`, and
  `office_time(x).next_boundary == segment_start(<next segment>)`.

DST resolution: an ambiguous boundary resolves to its first occurrence. A nonexistent one resolves
to the instant the clock jumps. Neither can happen on an America/New_York boundary, since
transitions occur at 02:00. They matter only for zones whose DST shift lands on 00:00/06:00/12:00/18:00.

**Scheduler pitfall:** Python subtracts two datetimes that share a tzinfo by *wall clock*. Compute
sleep durations in UTC:
`delay = ot.next_boundary.astimezone(timezone.utc) - datetime.now(timezone.utc)`.

## Dependencies

- stdlib only: `dataclasses`, `datetime`, `zoneinfo`, `logging`.
- **`tzdata` on Windows**: Windows has no system IANA database, so `zoneinfo` needs the `tzdata`
  PyPI package. It is not in `requirements.txt` and not in the project `.venv` at the time of
  writing. The `ubuntu:22.04` Docker base also doesn't guarantee `/usr/share/zoneinfo`. Without
  it, both functions raise `ValueError` for the zone, and `tests/test_office_clock.py` skips itself
  with that reason.

## Usage Examples

```python
from datetime import date, datetime, timezone
from office.clock import office_time

ot = office_time(datetime.now(timezone.utc), epoch=date(2026, 9, 27))
if ot.is_work_hours:
    print(f"week {ot.loop_week}, day {ot.day_index}, phase {ot.phase}")
```

```python
# Scheduler: sleep until the next segment, then act.
import time
from datetime import date, datetime, timezone
from office.clock import office_time, segment_start

EPOCH = date(2026, 9, 27)
ot = office_time(datetime.now(timezone.utc), EPOCH)
delay = (ot.next_boundary.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
time.sleep(max(0.0, delay))

# When does Wednesday's build segment of week 2 start?
start = segment_start(2, 3, 2, EPOCH)   # 2026-10-14 12:00-04:00
```

## Error Handling

All errors are `ValueError`, logged at ERROR before raising:

- `now` is naive or not a `datetime`.
- `now` is before the epoch Sunday 00:00 local (negative weeks are rejected, not wrapped).
- `epoch` is not a `date`, is a `datetime`, or is not a Sunday.
- `tz` is unknown or the tz database is missing (the message mentions `tzdata`).
- `segment_start` gets an index that is out of range or not an `int` (bools are rejected).

## Changelog

- **v1.0.0** (2026-09-27): initial version (OB-06). `office_time`, `OfficeTime`, and the
  `segment_start` inverse. DST-safe calendar-date week math.
