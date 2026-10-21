"""Block clock (golive plan D1/A1-A3): which scene plays next, from the wall clock alone."""
import datetime as dt

import pytest

from pending import require

bc = require("table.block_clock", "app/table/block_clock.py", wp="golive-ws2")

EDT = dt.timezone(dt.timedelta(hours=-4))
CFG = {"anchor": "2026-10-25T00:00:00-04:00", "block_hours": 6,
       "prelude_from": "2026-10-24T20:00:00-04:00", "offset_s": 0}
# 4 blocks: two spine scenes, one, none, one
SPINE = [["a1", "a2"], ["b1"], [], ["d1"]]


def at(day, hh, mm=0, month=10):
    return dt.datetime(2026, month, day, hh, mm, tzinfo=EDT)


@pytest.fixture
def clock():
    return bc.BlockClock.from_config(CFG)


def plan(clock, now, played=(), **kw):
    return bc.plan_next(clock, SPINE, set(played), now, **kw)


def test_day_phase_boundaries_by_local_hour(clock):
    expect = [((5, 59), "off_hours"), ((6, 0), "morning"), ((8, 59), "morning"),
              ((9, 0), "workday"), ((16, 59), "workday"), ((17, 0), "evening"),
              ((23, 59), "evening"), ((0, 0), "off_hours")]
    for (h, m), phase in expect:
        assert clock.phase(at(26, h, m)) == phase, (h, m)


def test_phase_uses_the_local_timezone_not_utc():
    c = bc.BlockClock.from_config({**CFG, "tz": "America/New_York"})
    utc_1400 = dt.datetime(2026, 10, 26, 14, 0, tzinfo=dt.timezone.utc)      # 10:00 EDT
    assert c.phase(utc_1400) == "workday"
    assert c.phase(dt.datetime(2026, 10, 26, 3, 0, tzinfo=dt.timezone.utc)) == "evening"  # 23:00 EDT


def test_prelude_is_ambient_evening_whatever_the_hour(clock):
    for now in (at(24, 21), at(24, 23, 59), at(24, 10), at(20, 3)):   # even a 10:00 / 03:00 "now"
        d = plan(clock, now)
        assert (d.kind, d.window, d.phase, d.block) == ("ambient", "prelude", "evening", -1), now
        assert d.scene_id is None


def test_block_window_plays_spine_first_then_ambient(clock):
    played = set()
    d = plan(clock, at(25, 0, 5), played)
    assert (d.kind, d.scene_id, d.block, d.window) == ("spine", "a1", 0, "block")
    assert d.spine_remaining == 2 and d.carry_over is False
    played.add("a1")
    d = plan(clock, at(25, 0, 40), played)
    assert (d.kind, d.scene_id, d.spine_remaining) == ("spine", "a2", 1)
    played.add("a2")
    d = plan(clock, at(25, 1, 0), played)
    assert (d.kind, d.phase, d.block, d.spine_remaining) == ("ambient", "off_hours", 0, 0)
    # ... and stays ambient until the window ends, with the phase following the clock
    assert plan(clock, at(25, 5, 59), played).kind == "ambient"
    d = plan(clock, at(25, 6, 0), played)           # block 1 opens
    assert (d.kind, d.scene_id, d.block, d.phase) == ("spine", "b1", 1, "morning")


def test_unplayed_spine_carries_over_before_the_next_blocks_spine(clock):
    d = plan(clock, at(25, 6, 30), played={"a1"})   # a2 never got played in block 0
    assert (d.kind, d.scene_id, d.carry_over, d.block) == ("spine", "a2", True, 1)
    assert d.carry_over_count == 1 and d.spine_remaining == 1
    d = plan(clock, at(25, 6, 31), played={"a1", "a2"})
    assert (d.scene_id, d.carry_over, d.carry_over_count) == ("b1", False, 0)


def test_carry_over_spans_empty_blocks_and_goes_earliest_block_first(clock):
    d = plan(clock, at(25, 13, 0), played=set())    # block 2 (no spine of its own)
    assert (d.scene_id, d.carry_over, d.carry_over_count) == ("a1", True, 3)
    d = plan(clock, at(25, 13, 0), played={"a1", "a2"})
    assert d.scene_id == "b1" and d.carry_over is True
    d = plan(clock, at(25, 13, 0), played={"a1", "a2", "b1"})
    assert d.kind == "ambient" and d.block == 2 and d.phase == "workday"


def test_a_later_block_is_not_started_early(clock):
    d = plan(clock, at(25, 5, 59), played={"a1", "a2"})
    assert d.kind == "ambient"                      # d1 / b1 wait for their windows
    assert plan(clock, at(25, 18, 0), played={"a1", "a2", "b1"}).scene_id == "d1"


def test_after_the_last_block_it_is_ambient_forever(clock):
    end = at(26, 0, 0)                              # 4 blocks of 6 h end Mon 00:00
    for now in (end, at(26, 9, 30), at(30, 15), at(27, 0, 0, month=11)):
        d = plan(clock, now, played=set())          # even with all the spine still unplayed
        assert (d.kind, d.window) == ("ambient", "after"), now
        assert d.scene_id is None
    assert plan(clock, at(26, 9, 30)).phase == "workday"
    assert plan(clock, at(26, 9, 30)).dropped_after_end == 4      # never looped back to play it


def test_spine_is_never_replayed_once_played(clock):
    everything = {"a1", "a2", "b1", "d1"}
    for h in range(0, 24):
        assert plan(clock, at(25, h, 30), everything).kind == "ambient"


def test_offset_s_pretends_it_is_block_n_now(clock):
    friday = at(23, 10, 0)
    assert plan(clock, friday).window == "prelude"
    shift = (at(25, 7, 30) - friday).total_seconds()          # pretend: Sun 07:30 = block 1
    shifted = bc.BlockClock.from_config({**CFG, "offset_s": shift})
    d = plan(shifted, friday)
    assert (d.block, d.kind, d.scene_id, d.phase) == (1, "spine", "a1", "morning")
    d = plan(shifted, friday, played={"a1", "a2"})
    assert (d.scene_id, d.block) == ("b1", 1)
    # epoch seconds work too
    assert plan(shifted, friday.timestamp(), played={"a1", "a2"}).scene_id == "b1"
    assert shifted.effective(friday) == at(25, 7, 30)


def test_negative_offset_moves_back_into_the_prelude():
    c = bc.BlockClock.from_config({**CFG, "offset_s": -3600})
    assert plan(c, at(25, 0, 30)).window == "prelude"


def test_plan_is_pure_and_restart_safe(clock):
    a = plan(clock, at(25, 6, 45), {"a1"}, defer=())
    b = plan(bc.BlockClock.from_config(CFG), at(25, 6, 45), {"a1"})
    assert a == b


def test_deferred_scene_is_skipped_but_still_owed(clock):
    d = plan(clock, at(25, 0, 10), played=set(), defer={"a1"})
    assert d.scene_id == "a2" and d.spine_remaining == 2
    d = plan(clock, at(25, 0, 10), played=set(), defer={"a1", "a2"})
    assert d.kind == "ambient" and d.spine_remaining == 2


def test_group_spine_by_segment_order():
    contracts = [{"scene_id": "x1", "segment_id": "segB"}, {"scene_id": "x0", "segment_id": "segA"},
                 {"scene_id": "x2", "segment_id": "segB"}, {"scene_id": "z", "segment_id": "unknown"},
                 {"scene_id": "amb", "segment_id": "segA", "ambient": True}]
    order = bc.block_map_from_segments(["segA", "segB", "segC"])
    assert bc.group_spine(contracts, order) == [["x0"], ["x1", "x2"]]
    assert bc.group_spine([], order) == []


def test_run_end_only_when_configured(clock):
    assert clock.run_end(4) is None and clock.finished(at(30, 0), 4) is False
    c = bc.BlockClock.from_config({**CFG, "end_after_last_block_s": 3600})
    assert c.run_end(4) == at(26, 1, 0)
    assert not c.finished(at(26, 0, 59), 4) and c.finished(at(26, 1, 0), 4)


def test_from_config_validation():
    assert bc.BlockClock.from_config(None) is None and bc.BlockClock.from_config({}) is None
    with pytest.raises(ValueError):
        bc.BlockClock.from_config({"block_hours": 6})                      # no anchor
    with pytest.raises(ValueError):
        bc.BlockClock.from_config({"anchor": "2026-10-25T00:00:00"})       # naive, no tz
    with pytest.raises(ValueError):
        bc.BlockClock.from_config({**CFG, "block_hours": 0})
    c = bc.BlockClock.from_config({"anchor": "2026-10-25T00:00:00", "tz": "America/New_York"})
    assert c.anchor.utcoffset() == dt.timedelta(hours=-4)
    c = bc.BlockClock.from_config({**CFG, "anchor": at(25, 0)})          # YAML hands datetimes over
    assert c.block_index(at(25, 6)) == 1
