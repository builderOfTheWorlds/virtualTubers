"""Ambient (plot-free) office scenes: loading, contract conversion and selection."""
import random
import textwrap

import pytest

from pending import require

amb = require("table.ambient_source", "app/table/ambient_source.py", wp="golive-ws2")
from table import contract as contract_mod, gm_context, protocol, story_log  # noqa: E402

SEAT_OF = {"tech_lead": "tuber_1", "analyst": "tuber_2", "engineer": "tuber_3",
           "ceo": "tuber_0", "party_member": "tuber_7", "Tech Lead": "tuber_1"}
ACTIVE = ["tuber_1", "tuber_2", "tuber_3"]
H = 3600.0


def scene_doc(sid, phase="evening", seats=("engineer", "analyst"), **over):
    doc = {"id": sid, "title": sid.upper(), "ambient": True, "prompt": f"Prompt for {sid}.",
           "lore": ["office"], "source": {"by": "test"}, "day_phase": phase, "seats": list(seats)}
    doc.update(over)
    return doc


def scene(sid, phase="evening", seats=("engineer", "analyst"), **over):
    return amb.parse_scene(scene_doc(sid, phase, seats, **over))


def pick(pool, phase="evening", **kw):
    kw.setdefault("now_ts", 1_000_000.0)
    kw.setdefault("history", [])
    kw.setdefault("seat_of", SEAT_OF)
    kw.setdefault("active_seats", ACTIVE)
    kw.setdefault("rng", random.Random(1))
    return pool.pick(phase, **kw)


# ------------------------------------------------------------------ parsing / loading
def test_parse_valid_scene_defaults():
    s = scene("night-1", phase="off_hours", seats=("engineer",))
    assert s.day_phases == ("off_hours",) and s.intensity == "quiet"      # off_hours-only -> quiet
    s = scene("noon-1", phase="workday")
    assert s.intensity == "normal" and s.lore == ("office",) and s.seats == ("engineer", "analyst")
    assert scene("x", phase=["morning", "evening"]).day_phases == ("morning", "evening")
    assert scene("x", phase="any").day_phases == ("off_hours", "morning", "workday", "evening")
    assert scene("x", intensity="quiet", phase="evening").intensity == "quiet"


def test_non_ambient_scene_is_ignored_and_bad_ones_raise():
    assert amb.parse_scene({"id": "spine-1", "prompt": "x"}) is None
    assert amb.parse_scene({"id": "spine-1", "ambient": False, "prompt": "x"}) is None
    for bad in (scene_doc("a", phase="midnight"), scene_doc("a", phase=None),
                scene_doc("a", intensity="loud"), {**scene_doc("a"), "seats": "engineer"},
                {**scene_doc("a"), "prompt": ""}, {**scene_doc("a"), "id": ""}):
        with pytest.raises(ValueError):
            amb.parse_scene(bad)


def test_load_dir_reads_single_multi_and_list_files_and_skips_bad(tmp_path):
    import yaml
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(scene_doc("one")))
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.yml").write_text(
        yaml.safe_dump(scene_doc("two")) + "---\n" + yaml.safe_dump(scene_doc("three", "workday")))
    (tmp_path / "c.yaml").write_text(yaml.safe_dump([scene_doc("four"), scene_doc("bad", phase="x"),
                                                     {"id": "spine", "prompt": "p"}]))
    (tmp_path / "broken.yaml").write_text("a: [unclosed")
    (tmp_path / "notes.txt").write_text("ignored")
    assert sorted(s.id for s in amb.load_dir(tmp_path)) == ["four", "one", "three", "two"]
    assert amb.load_dir(tmp_path / "missing") == []


class FakeConn:
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.closed = rows, fail, False
        self.sql = None

    def cursor(self):
        return self

    def execute(self, sql, params):
        if self.fail:
            raise RuntimeError("db down")
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True

    def rollback(self):
        pass


def test_load_postgres_keeps_only_ambient_rows():
    import yaml
    conn = FakeConn([("s1", yaml.safe_dump(scene_doc("pg-1"))),
                     ("spine", "id: spine\nprompt: x\n"), ("bad", "a: [")])
    got = amb.load_postgres("ashiorid_office", connect=lambda: conn)
    assert [s.id for s in got] == ["pg-1"] and got[0].source.startswith("postgres:ashiorid_office/")
    assert "pack_scenes" in conn.sql and conn.params == ("ashiorid_office",) and conn.closed


def test_load_pool_merges_sources_and_survives_a_db_outage(tmp_path):
    import yaml
    (tmp_path / "x.yaml").write_text(yaml.safe_dump(scene_doc("dir-1")))
    ok = lambda: FakeConn([("s", yaml.safe_dump(scene_doc("pg-1")))])        # noqa: E731
    down = lambda: FakeConn([], fail=True)                                    # noqa: E731
    pool = amb.load_pool({"dir": str(tmp_path), "pack": "p"}, connect=ok)
    assert sorted(s.id for s in pool.scenes) == ["dir-1", "pg-1"]
    pool = amb.load_pool({"dir": str(tmp_path), "pack": "p"}, connect=down)    # DB down: dir still plays
    assert [s.id for s in pool.scenes] == ["dir-1"]
    with pytest.raises(RuntimeError):
        amb.load_pool({"pack": "p"}, connect=down)                             # nothing to fall back on
    assert len(amb.load_pool({"postgres": False, "dir": str(tmp_path)})) == 1


# ------------------------------------------------------------------ contract conversion
def test_participants_are_the_scene_seats_intersected_with_active_seats():
    s = scene("t", seats=("ceo", "tech_lead", "party_member", "analyst", "nobody", "Tech Lead"))
    assert amb.participants_for(s, SEAT_OF, ACTIVE) == ["tuber_1", "tuber_2"]
    assert amb.participants_for(s, SEAT_OF, ["tuber_2"]) == ["tuber_2"]
    assert amb.participants_for(scene("t", seats=("ceo", "party_member")), SEAT_OF, ACTIVE) == []


def test_ambient_contract_is_plot_free_and_flagged():
    s = scene("kettle", seats=("engineer", "analyst"))
    c = amb.to_contract(s, ["tuber_3", "tuber_2"], phase="evening", run_id="run1",
                        scene_id="ambient.kettle.t5")
    p = c.to_payload()
    assert p["ambient"] is True and p["ambient_id"] == "kettle" and p["day_phase"] == "evening"
    assert p["canon_goal"] == "Prompt for kettle." and p["participants"] == ["tuber_3", "tuber_2"]
    assert p["must_resolve"] == [] and p["expected_beats"] == [] and p["continuity_out"] == ""
    assert p["max_rounds"] == 2 and p["run_id"] == "run1"
    protocol.validate("scene_start", {"scene_id": p["scene_id"], "contract": p, "round": 1,
                                      "max_rounds": p["max_rounds"], "seats": ["tuber_3"]})
    assert contract_mod.SceneContract.from_payload(p) == c               # round trips


def test_quiet_scenes_and_the_dead_of_night_use_fewer_rounds():
    normal = scene("n", phase=["off_hours", "evening"])                   # intensity normal
    quiet = scene("q", phase="evening", intensity="quiet")
    mk = lambda s, phase: amb.to_contract(s, ["tuber_1"], phase=phase, run_id="r",  # noqa: E731
                                          scene_id="x", rounds={"normal": 3, "quiet": 1})
    assert mk(normal, "evening").max_rounds == 3
    assert mk(normal, "off_hours").max_rounds == 1 and mk(normal, "off_hours").intensity == "quiet"
    assert mk(quiet, "evening").max_rounds == 1


def test_a_spine_contract_payload_carries_no_ambient_keys():
    c = contract_mod.SceneContract(
        scene_id="s", run_id="r", segment_id="g", leaf_id="l", slot_id="o", order=0, canon_goal="g",
        participants=("tuber_1",), lore=(), continuity_in="", continuity_out="", must_resolve=(),
        expected_beats=())
    assert set(c.to_payload()) == set(contract_mod.CONTRACT_FIELDS)


def test_gm_context_tells_the_gm_an_ambient_scene_is_plot_free():
    ambient_c = {"scene_id": "a", "canon_goal": "Tea.", "ambient": True}
    text = gm_context.build_gm_context(gm_blocks={}, gm_identity="GM", player_sheets={"tuber_1": "s"},
                                       segment=None, contract=ambient_c, blocks_order=[])
    assert gm_context.AMBIENT_HEADING in text and "Do not advance" in text
    plain = gm_context.build_gm_context(gm_blocks={}, gm_identity="GM", player_sheets={"tuber_1": "s"},
                                        segment=None, contract={"scene_id": "a", "canon_goal": "x"},
                                        blocks_order=[])
    assert gm_context.AMBIENT_HEADING not in plain


# ------------------------------------------------------------------ selection
def test_pick_matches_the_phase_and_needs_an_active_seat():
    pool = amb.AmbientPool([scene("ev", "evening"), scene("wd", "workday"), scene("nt", "off_hours"),
                            scene("ghost", "evening", seats=("ceo", "party_member"))])
    assert pick(pool, "workday").scene.id == "wd"
    assert pick(pool, "off_hours").scene.id == "nt"
    got = {pick(pool, "evening", rng=random.Random(i)).scene.id for i in range(20)}
    assert got == {"ev"}                       # "ghost" has nobody who can speak
    assert pick(pool, "evening").participants == ("tuber_3", "tuber_2")
    assert pick(amb.AmbientPool(), "evening") is None
    assert pick(pool, "evening", active_seats=[]) is None


def test_pick_never_repeats_inside_the_window_while_the_pool_allows():
    pool = amb.AmbientPool([scene(f"e{i}", "evening") for i in range(4)])
    history, now, seen = [], 1_000_000.0, []
    for _ in range(4):
        p = pick(pool, now_ts=now, history=history, no_repeat_s=48 * H)
        assert not p.relaxed
        seen.append(p.scene.id)
        history.append({"id": p.scene.id, "at": now})
        now += 600
    assert sorted(seen) == ["e0", "e1", "e2", "e3"]          # four picks, four distinct scenes


def test_pick_relaxes_to_least_recently_used_when_the_pool_is_spent():
    pool = amb.AmbientPool([scene(f"e{i}", "evening") for i in range(3)])
    now = 1_000_000.0
    history = [{"id": "e1", "at": now - 10 * H}, {"id": "e0", "at": now - 5 * H},
               {"id": "e2", "at": now - 1 * H}]
    p = pick(pool, now_ts=now, history=history, no_repeat_s=48 * H)
    assert p.relaxed and p.scene.id == "e1"                  # oldest airing goes first


def test_pick_allows_a_scene_again_after_the_window():
    pool = amb.AmbientPool([scene("only", "evening")])
    now = 1_000_000.0
    inside = pick(pool, now_ts=now, history=[{"id": "only", "at": now - 47 * H}], no_repeat_s=48 * H)
    after = pick(pool, now_ts=now, history=[{"id": "only", "at": now - 49 * H}], no_repeat_s=48 * H)
    assert inside.relaxed and not after.relaxed


def test_pick_prefers_scenes_whose_seats_did_not_just_speak():
    pool = amb.AmbientPool([scene("eng", "evening", seats=("engineer",)),
                            scene("ana", "evening", seats=("analyst",)),
                            scene("both", "evening", seats=("engineer", "analyst"))])
    for i in range(10):
        p = pick(pool, rng=random.Random(i), last_speakers=["tuber_3"])       # the engineer just spoke
        assert p.scene.id == "ana"
        p = pick(pool, rng=random.Random(i), last_speakers=["tuber_2", "tuber_3"])
        assert p.scene.id in {"eng", "ana"}                  # least overlap: a single-seat scene
        assert len(p.participants) == 1


def test_lru_beats_random_among_equally_fresh_scenes():
    pool = amb.AmbientPool([scene(f"e{i}", "evening", seats=("tech_lead",)) for i in range(3)])
    now = 1_000_000.0
    history = [{"id": "e0", "at": now - 100 * H}, {"id": "e1", "at": now - 60 * H}]    # e2 never aired
    assert pick(pool, now_ts=now, history=history, no_repeat_s=48 * H).scene.id == "e2"
    history.append({"id": "e2", "at": now - 70 * H})
    assert pick(pool, now_ts=now, history=history, no_repeat_s=48 * H).scene.id == "e0"


def test_empty_phase_borrows_but_never_puts_a_daytime_scene_in_the_night():
    pool = amb.AmbientPool([scene("wd", "workday"), scene("nt", "off_hours")])
    p = pick(pool, "morning")
    assert p.fell_back and p.scene.id == "wd"
    only_day = amb.AmbientPool([scene("wd", "workday")])
    assert pick(only_day, "off_hours") is None               # nothing quiet to borrow: idle, not noon chatter
    quiet_day = amb.AmbientPool([scene("wd", "workday", intensity="quiet")])
    assert pick(quiet_day, "off_hours").scene.id == "wd"


def test_story_log_keeps_ambient_in_its_own_small_budget():
    log = story_log.StoryLog(keep=3, ambient_keep=2)
    for i in range(3):
        log.append(story_log.StoryEntry(scene_id=f"spine{i}", goal=f"Goal {i}.", notes="happened"))
    for i in range(10):
        log.append(story_log.StoryEntry(scene_id=f"amb{i}", goal="Tea.", notes="small talk", ambient=True))
    ids = [e.scene_id for e in log.entries]
    assert ids == ["spine0", "spine1", "spine2", "amb8", "amb9"]       # a night of ambient evicts no spine
    text = log.render(max_entries=3, ambient_entries=1)
    assert "[spine0]" in text and "[amb9]" in text and "[amb8]" not in text
    assert "no plot" in text.split("[amb9]")[1]
    # round trip through the position file keeps the flag
    again = story_log.StoryLog.from_list(log.to_list(), keep=3, ambient_keep=2)
    assert [e.ambient for e in again.entries] == [False, False, False, True, True]
    assert "ambient" not in log.entries[0].to_dict()


def test_ambient_story_entry_is_short_and_carries_no_state():
    long_goal = "Somebody argues about the " + "kettle " * 60
    state = {"scene_id": "a", "resolved": False, "state_delta": {"mood": "angry"},
             "notes": "x " * 200, "transcript": [{"speaker": "tuber_1", "text": "Hello.", "kind": "reply"}]}
    e = story_log.entry_from_state(state, {"canon_goal": long_goal}, ambient=True)
    assert e.ambient and e.state_delta == {} and e.resolved is True
    assert len(e.goal) <= 100 and len(e.notes) <= 120


@pytest.mark.parametrize("text", [textwrap.dedent("""
    id: stage-1
    title: Stage one
    ambient: true
    prompt: The kettle clicks off.
    lore: []
    source: {generator: test}
    day_phase: morning
    intensity: normal
    seats: [engineer]
""")])
def test_the_seam_yaml_shape_parses(text):
    (s,) = amb.parse_yaml_text(text)
    assert (s.id, s.day_phases, s.intensity, s.seats) == ("stage-1", ("morning",), "normal", ("engineer",))
