"""Wall-clock block scheduler for the unattended office table (golive plan D1/A1-A3).

The week's story is split into blocks (arc segment order 0..N-1 = block index), each tied
to its real wall-clock window of ``block_hours`` starting at ``anchor``. ``plan_next`` is a
PURE function: given the time, the spine contract ids per block and the set of spine
contracts already played, it says what to play next:

  a. any unplayed spine contract of a block whose window has STARTED, earliest block first
     (carry-over, A2: a block's leftover plays before the next block's spine);
  b. else an ambient (plot-free) scene for the current day phase;
  c. before ``anchor`` (the prelude) -> ambient, evening phase;
  d. after the last block's window -> ambient forever (never loops the spine, never stops).

Nothing is kept in memory between calls, so a restart re-derives the same answer from the
clock plus the persisted played set (table_gm keeps both in its position file).

``offset_s`` shifts the clock (``effective = now + offset_s``): a soak test on Friday pretends
it is block N now. Day phases use the LOCAL hour (``tz`` or the anchor's own UTC offset):
off_hours 00-06, morning 06-09, workday 09-17, evening 17-24.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

OFF_HOURS, MORNING, WORKDAY, EVENING = "off_hours", "morning", "workday", "evening"
PHASES = (OFF_HOURS, MORNING, WORKDAY, EVENING)

SPINE, AMBIENT = "spine", "ambient"
PRELUDE, BLOCK, AFTER = "prelude", "block", "after"

Instant = Union[_dt.datetime, int, float]


def phase_for_hour(hour: int) -> str:
    if hour < 6:
        return OFF_HOURS
    if hour < 9:
        return MORNING
    if hour < 17:
        return WORKDAY
    return EVENING


def _as_dt(value: Any, tz: Optional[_dt.tzinfo], what: str) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = _dt.datetime.fromisoformat(text)
    else:
        raise ValueError(f"block_clock.{what} must be an ISO timestamp, got {value!r}")
    if parsed.tzinfo is None:
        if tz is None:
            raise ValueError(f"block_clock.{what} needs a UTC offset (e.g. -04:00) or a tz")
        parsed = parsed.replace(tzinfo=tz)
    return parsed


@dataclasses.dataclass(frozen=True)
class BlockClock:
    anchor: _dt.datetime
    block_hours: float = 6.0
    prelude_from: Optional[_dt.datetime] = None
    offset_s: float = 0.0
    tz: Optional[_dt.tzinfo] = None
    end_after_last_block_s: Optional[float] = None   # None = the run never ends

    @classmethod
    def from_config(cls, cfg: Optional[Mapping[str, Any]]) -> Optional["BlockClock"]:
        """The clock for a ``block_clock:`` config block (None/empty -> None = disabled)."""
        if not cfg:
            return None
        tz = None
        if cfg.get("tz"):
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(str(cfg["tz"]))
        if cfg.get("anchor") is None:
            raise ValueError("block_clock.anchor is required")
        anchor = _as_dt(cfg["anchor"], tz, "anchor")
        prelude = cfg.get("prelude_from")
        block_hours = float(cfg.get("block_hours", 6))
        if block_hours <= 0:
            raise ValueError("block_clock.block_hours must be > 0")
        end_after = cfg.get("end_after_last_block_s")
        return cls(
            anchor=anchor, block_hours=block_hours,
            prelude_from=_as_dt(prelude, tz, "prelude_from") if prelude else None,
            offset_s=float(cfg.get("offset_s", 0) or 0), tz=tz or anchor.tzinfo,
            end_after_last_block_s=None if end_after is None else float(end_after))

    # ------------------------------------------------------------------ time
    @property
    def block_len(self) -> _dt.timedelta:
        return _dt.timedelta(hours=self.block_hours)

    def effective(self, now: Instant) -> _dt.datetime:
        """`now` (aware datetime or epoch seconds) shifted by offset_s, in the clock's tz."""
        if isinstance(now, (int, float)):
            base = _dt.datetime.fromtimestamp(float(now), tz=_dt.timezone.utc)
        else:
            base = now if now.tzinfo is not None else now.replace(tzinfo=self.tz)
        out = base + _dt.timedelta(seconds=self.offset_s)
        return out.astimezone(self.tz) if self.tz is not None else out

    def block_index(self, now: Instant) -> int:
        """-1 before the anchor (prelude); may be >= the number of blocks (after the week)."""
        delta = (self.effective(now) - self.anchor) / self.block_len
        return int(delta // 1) if delta >= 0 else -1

    def window(self, index: int) -> Tuple[_dt.datetime, _dt.datetime]:
        start = self.anchor + index * self.block_len
        return start, start + self.block_len

    def phase(self, now: Instant) -> str:
        return phase_for_hour(self.effective(now).hour)

    def run_end(self, n_blocks: int) -> Optional[_dt.datetime]:
        """When the run ends (last block end + end_after_last_block_s); None = never."""
        if self.end_after_last_block_s is None:
            return None
        return (self.anchor + n_blocks * self.block_len
                + _dt.timedelta(seconds=self.end_after_last_block_s))

    def finished(self, now: Instant, n_blocks: int) -> bool:
        end = self.run_end(n_blocks)
        return end is not None and self.effective(now) >= end


@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str                       # "spine" | "ambient"
    scene_id: Optional[str]         # the spine contract id (None for ambient)
    block: int                      # current block index (-1 prelude, >= n after the week)
    window: str                     # "prelude" | "block" | "after"
    phase: str                      # day phase the ambient scene must match
    carry_over: bool = False        # the spine contract belongs to an earlier, ended block
    spine_remaining: int = 0        # unplayed spine left in the CURRENT block
    carry_over_count: int = 0       # unplayed spine of earlier blocks still owed
    dropped_after_end: int = 0      # unplayed spine given up because the week is over
    effective: str = ""             # the shifted clock, ISO


def group_spine(contracts: Iterable[Mapping[str, Any]],
                block_of_segment: Mapping[str, int]) -> List[List[str]]:
    """Spine contract ids grouped by block index, in play order.

    block_of_segment maps a contract's segment_id to its block (segment order). A contract
    whose segment is unknown is ignored (never guessed into a block)."""
    groups: dict = {}
    for c in contracts:
        block = block_of_segment.get(str(c.get("segment_id")))
        if block is None or c.get("ambient"):
            continue
        groups.setdefault(int(block), []).append(str(c.get("scene_id")))
    if not groups:
        return []
    return [groups.get(i, []) for i in range(max(groups) + 1)]


def block_map_from_segments(segment_ids: Sequence[str]) -> dict:
    """segment id -> block index by position in `segment_ids` (already in segment order)."""
    return {str(seg): i for i, seg in enumerate(segment_ids)}


def plan_next(clock: BlockClock, spine_blocks: Sequence[Sequence[str]], played: Set[str],
              now: Instant, *, defer: Iterable[str] = (), n_blocks: Optional[int] = None) -> Decision:
    """What to play next (module doc). `spine_blocks[i]` = ordered contract ids of block i.

    played: spine ids to treat as done (finished, or given up after repeated failures).
    defer:  ids not to pick right now (a scene that just got stuck); they still count as owed.
    n_blocks: how many blocks the week has when that is more than len(spine_blocks)."""
    eff = clock.effective(now)
    k = clock.block_index(now)
    n = max(len(spine_blocks), n_blocks or 0)
    deferred = set(defer)
    phase = clock.phase(now)
    stamp = eff.isoformat()

    def owed(block: int) -> List[str]:
        ids: Sequence[str] = spine_blocks[block] if 0 <= block < len(spine_blocks) else ()
        return [s for s in ids if s not in played]

    if k < 0:                       # prelude: nothing of the story yet, always evening
        return Decision(AMBIENT, None, -1, PRELUDE, EVENING, effective=stamp)
    if k >= n:                      # the week is over: ambient forever, spine never loops
        dropped = sum(len(owed(b)) for b in range(len(spine_blocks)))
        return Decision(AMBIENT, None, k, AFTER, phase, dropped_after_end=dropped, effective=stamp)

    carry = sum(len(owed(b)) for b in range(k))
    remaining = len(owed(k))
    for b in range(k + 1):          # earliest started block first
        for sid in owed(b):
            if sid not in deferred:
                return Decision(SPINE, sid, k, BLOCK, phase, carry_over=b < k,
                                spine_remaining=remaining, carry_over_count=carry, effective=stamp)
    return Decision(AMBIENT, None, k, BLOCK, phase, spine_remaining=remaining,
                    carry_over_count=carry, effective=stamp)
