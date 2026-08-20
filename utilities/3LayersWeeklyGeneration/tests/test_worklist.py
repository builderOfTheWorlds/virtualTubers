"""Acceptance tests for src/worklist.py — the scan-and-assign pass that runs
before any GPU time is spent.

`batch_generate._next_take_number` globs a scene directory and returns
`max(...) + 1`. Under a pool of eight workers, two of them see `002.yaml` as
the highest and both claim take 3 — one silently overwrites the other. The fix
is not a lock: it is to stop allocating lazily. Scan the output tree once,
compute every `(segment, slot, take)` that is not yet on disk, assign its
deterministic path up front, and emit an ordered list. Race-freedom, idempotent
resume (re-running the scan IS the resume), and an exact work count before a
two-day run starts all fall out of the same pass.

The condition assignment here is design decision D12: the takes-per-slot budget
was already being spent on three interchangeable variants, so spending it on
state coverage instead costs zero extra GPU time. Take 1 is ALWAYS neutral —
that invariant is what stops the runtime dead-airing when no conditioned take
matches the current state.
"""
import pathlib

import pytest

import worklist


CONFIG = {
    "dialogue": {"takes_per_slot": 3, "neutral_takes": 1},
    "state": {
        "flags": ["helen-wounded", "moonwell-tainted", "buffalo-lost-axe"],
        "moods": ["tense", "weary", "hopeful", "giddy"],
        "carry_keys": ["helen-wounded", "moonwell-tainted"],
    },
}


def ambient(slot_id, sensitivity="none", depends_on=None):
    return {"slot_id": slot_id, "kind": "ambient", "prompt": "A quiet moment.",
            "lore": [], "sensitivity": sensitivity,
            "depends_on": list(depends_on or [])}


def spine(slot_id, scene_ref="01-invitation"):
    return {"slot_id": slot_id, "kind": "spine", "scene_ref": scene_ref,
            "summary": "They arrive."}


# ── deterministic paths ───────────────────────────────────────────────────────

def test_take_path_is_zero_padded_and_lives_under_the_slot(tmp_path):
    path = worklist.take_path(tmp_path, "slot-004", 2)
    assert path == tmp_path / "slots" / "slot-004" / "002.yaml"


def test_take_path_pads_to_three_digits_so_takes_sort_lexically(tmp_path):
    """Take files are listed with glob and read in name order downstream."""
    paths = [worklist.take_path(tmp_path, "s", n).name for n in (1, 9, 10)]
    assert paths == ["001.yaml", "009.yaml", "010.yaml"]
    assert paths == sorted(paths)


def test_the_same_unit_always_resolves_to_the_same_path(tmp_path):
    assert worklist.take_path(tmp_path, "s", 3) == worklist.take_path(tmp_path, "s", 3)


# ── condition assignment (D12) ────────────────────────────────────────────────

def test_take_one_is_always_neutral_regardless_of_sensitivity():
    """The neutral-take invariant. Without it a slot whose conditions all miss
    the live state has nothing to play, and the stream dead-airs."""
    for slot in (ambient("s", "none"),
                 ambient("s", "tone"),
                 ambient("s", "flags", ["helen-wounded"])):
        assert worklist.conditions_for(slot, 1, CONFIG) == {}


def test_an_insensitive_slot_leaves_every_take_neutral():
    """`sensitivity: none` means the line reads the same in any state.
    Conditioning it would only shrink the pool the runtime can draw from."""
    slot = ambient("s", "none")
    assert [worklist.conditions_for(slot, n, CONFIG) for n in (1, 2, 3)] == [{}, {}, {}]


def test_a_tone_sensitive_slot_walks_the_declared_moods_in_order():
    slot = ambient("s", "tone")
    assert worklist.conditions_for(slot, 2, CONFIG) == {"mood": "tense"}
    assert worklist.conditions_for(slot, 3, CONFIG) == {"mood": "weary"}


def test_a_flag_sensitive_slot_covers_both_polarities_of_its_first_flag():
    """Two conditioned takes and one flag: the useful split is true/false, not
    two takes under the same condition."""
    slot = ambient("s", "flags", ["helen-wounded"])
    assert worklist.conditions_for(slot, 2, CONFIG) == {"helen-wounded": True}
    assert worklist.conditions_for(slot, 3, CONFIG) == {"helen-wounded": False}


def test_a_multi_flag_slot_exhausts_one_flag_before_starting_the_next():
    slot = ambient("s", "flags", ["helen-wounded", "moonwell-tainted"])
    got = [worklist.conditions_for(slot, n, CONFIG) for n in (2, 3, 4, 5)]
    assert got == [{"helen-wounded": True}, {"helen-wounded": False},
                   {"moonwell-tainted": True}, {"moonwell-tainted": False}]


def test_flag_sensitive_with_no_declared_dependency_stays_neutral():
    """Nothing to condition on. Neutral takes are still useful takes."""
    slot = ambient("s", "flags", [])
    assert [worklist.conditions_for(slot, n, CONFIG) for n in (2, 3)] == [{}, {}]


def test_conditions_cycle_when_the_takes_outrun_the_candidates():
    """More takes than distinct conditions is not an error — a second take
    under the same condition is ordinary variety."""
    config = {**CONFIG, "state": {**CONFIG["state"], "moods": ["tense"]}}
    slot = ambient("s", "tone")
    assert worklist.conditions_for(slot, 2, config) == {"mood": "tense"}
    assert worklist.conditions_for(slot, 3, config) == {"mood": "tense"}


def test_every_conditioned_take_validates_against_the_state_vocabulary():
    """A condition this module invents must be one the runtime can satisfy."""
    import vocabulary

    class FakePack:
        lore = {}
        scenes = {}

    vocab = vocabulary.Vocabulary.from_config_and_pack(CONFIG, FakePack())
    slot = ambient("s", "flags", ["helen-wounded"])
    for take in (1, 2, 3):
        assert vocab.validate_condition(worklist.conditions_for(slot, take, CONFIG)) == []
    tone_slot = ambient("s", "tone")
    for take in (1, 2, 3):
        assert vocab.validate_condition(worklist.conditions_for(tone_slot, take, CONFIG)) == []


def test_more_neutral_takes_than_one_are_honoured():
    config = {**CONFIG, "dialogue": {"takes_per_slot": 4, "neutral_takes": 2}}
    slot = ambient("s", "tone")
    got = [worklist.conditions_for(slot, n, config) for n in (1, 2, 3, 4)]
    assert got == [{}, {}, {"mood": "tense"}, {"mood": "weary"}]


# ── units for one segment ─────────────────────────────────────────────────────

def test_a_clean_segment_yields_takes_per_slot_units_for_each_ambient_slot(tmp_path):
    brief = {"slots": [ambient("s-001"), ambient("s-002")]}
    units = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    assert len(units) == 6
    assert {unit.slot_id for unit in units} == {"s-001", "s-002"}
    assert all(unit.segment_id == "seg-001" for unit in units)


def test_spine_slots_produce_no_units(tmp_path):
    """Spine dialogue is authored in the pack; Layer 3 only fills ambient."""
    brief = {"slots": [spine("s-001"), ambient("s-002")]}
    units = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    assert {unit.slot_id for unit in units} == {"s-002"}


def test_units_carry_the_slot_so_a_worker_needs_no_second_lookup(tmp_path):
    brief = {"slots": [ambient("s-001", "tone")]}
    unit = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)[0]
    assert unit.slot["prompt"] == "A quiet moment."


def test_units_are_ordered_by_slot_then_take(tmp_path):
    """Draining in arc order leaves a contiguous airable prefix if the run stops."""
    brief = {"slots": [ambient("s-001"), ambient("s-002")]}
    units = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    assert [(unit.slot_id, unit.take) for unit in units] == [
        ("s-001", 1), ("s-001", 2), ("s-001", 3),
        ("s-002", 1), ("s-002", 2), ("s-002", 3)]


def test_no_two_units_claim_the_same_path(tmp_path):
    """The whole point: lazy allocation let two workers claim take 3."""
    brief = {"slots": [ambient(f"s-{n:03d}") for n in range(1, 21)]}
    units = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    paths = [unit.path for unit in units]
    assert len(paths) == len(set(paths)) == 60


def test_a_missing_slots_key_raises_rather_than_silently_planning_nothing(tmp_path):
    """An empty worklist and a malformed brief look identical from the outside;
    a two-day run must not exit in three seconds reporting success."""
    with pytest.raises(worklist.WorklistError):
        worklist.units_for_segment("seg-001", {}, tmp_path, CONFIG)


def test_a_slot_without_an_id_raises_naming_the_segment(tmp_path):
    brief = {"slots": [{"kind": "ambient", "prompt": "x"}]}
    with pytest.raises(worklist.WorklistError) as excinfo:
        worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    assert "seg-001" in str(excinfo.value)


# ── resume: the scan IS the resume ────────────────────────────────────────────

def test_existing_takes_are_not_replanned(tmp_path):
    brief = {"slots": [ambient("s-001")]}
    done = worklist.take_path(tmp_path, "s-001", 1)
    done.parent.mkdir(parents=True)
    done.write_text("beats: []\n")

    units = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    assert [unit.take for unit in units] == [2, 3]


def test_a_hole_in_the_middle_is_replanned_not_appended(tmp_path):
    """Lazy allocation would see 003 as the max and plan take 4, leaving the
    missing 002 unfilled forever."""
    brief = {"slots": [ambient("s-001")]}
    for take in (1, 3):
        path = worklist.take_path(tmp_path, "s-001", take)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("beats: []\n")

    units = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    assert [unit.take for unit in units] == [2]


def test_a_fully_generated_slot_yields_nothing(tmp_path):
    brief = {"slots": [ambient("s-001")]}
    for take in (1, 2, 3):
        path = worklist.take_path(tmp_path, "s-001", take)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("beats: []\n")
    assert worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG) == []


def test_a_replanned_take_keeps_the_condition_it_would_have_had(tmp_path):
    """Take 2 is the true-polarity take whether or not take 1 exists yet.
    Conditions must key off the take number, not off position in the worklist."""
    brief = {"slots": [ambient("s-001", "flags", ["helen-wounded"])]}
    done = worklist.take_path(tmp_path, "s-001", 1)
    done.parent.mkdir(parents=True)
    done.write_text("beats: []\n")

    units = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    assert units[0].conditions == {"helen-wounded": True}


def test_scanning_twice_gives_the_same_answer(tmp_path):
    brief = {"slots": [ambient("s-001"), ambient("s-002")]}
    first = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    second = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    assert [u.path for u in first] == [u.path for u in second]


def test_draining_a_unit_removes_it_from_the_next_scan(tmp_path):
    """Simulates a worker finishing one unit, then a resume."""
    brief = {"slots": [ambient("s-001")]}
    units = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    units[0].path.parent.mkdir(parents=True, exist_ok=True)
    units[0].path.write_text("beats: []\n")

    remaining = worklist.units_for_segment("seg-001", brief, tmp_path, CONFIG)
    assert [unit.take for unit in remaining] == [2, 3]


# ── the whole-run pass ────────────────────────────────────────────────────────

def _write_brief(root, segment_id, slots):
    import yaml
    segment_root = root / "segments" / segment_id
    segment_root.mkdir(parents=True)
    (segment_root / "brief.yaml").write_text(yaml.safe_dump({"slots": slots}))
    return segment_root


def test_build_worklist_reads_each_segments_brief_in_the_given_order(tmp_path):
    _write_brief(tmp_path, "seg-001", [ambient("s-001")])
    _write_brief(tmp_path, "seg-002", [ambient("s-001")])

    units = worklist.build_worklist(tmp_path, ["seg-001", "seg-002"], CONFIG)
    assert [unit.segment_id for unit in units] == ["seg-001"] * 3 + ["seg-002"] * 3


def test_build_worklist_raises_when_a_segments_brief_is_missing(tmp_path):
    _write_brief(tmp_path, "seg-001", [ambient("s-001")])
    with pytest.raises(worklist.WorklistError) as excinfo:
        worklist.build_worklist(tmp_path, ["seg-001", "seg-002"], CONFIG)
    assert "seg-002" in str(excinfo.value)


def test_build_worklist_logs_at_error_before_raising(tmp_path, caplog):
    caplog.set_level("ERROR")
    with pytest.raises(worklist.WorklistError):
        worklist.build_worklist(tmp_path, ["seg-404"], CONFIG)
    assert caplog.records


def test_build_worklist_paths_land_under_the_right_segment(tmp_path):
    _write_brief(tmp_path, "seg-002", [ambient("s-001")])
    units = worklist.build_worklist(tmp_path, ["seg-002"], CONFIG)
    assert units[0].path == (
        tmp_path / "segments" / "seg-002" / "slots" / "s-001" / "001.yaml")


# ── the number the operator sees before committing two days ───────────────────

def test_estimate_seconds_divides_the_work_across_the_pool():
    units = [object()] * 100
    assert worklist.estimate_seconds(units, seconds_per_take=60, concurrency=8) == \
        pytest.approx(100 * 60 / 8)


def test_estimate_seconds_of_an_empty_worklist_is_zero():
    assert worklist.estimate_seconds([], seconds_per_take=60, concurrency=8) == 0


def test_estimate_seconds_rejects_a_nonsensical_concurrency():
    with pytest.raises(worklist.WorklistError):
        worklist.estimate_seconds([object()], seconds_per_take=60, concurrency=0)


# ── every failure path is visible in an unattended run ────────────────────────

@pytest.mark.parametrize("call", [
    pytest.param(lambda root: worklist.units_for_segment("seg-001", {}, root, CONFIG),
                 id="brief-without-slots"),
    pytest.param(lambda root: worklist.units_for_segment(
        "seg-001", {"slots": [{"kind": "ambient"}]}, root, CONFIG),
        id="slot-without-id"),
    pytest.param(lambda root: worklist.build_worklist(root, ["seg-404"], CONFIG),
                 id="missing-brief"),
    pytest.param(lambda root: worklist.estimate_seconds([], 60, 0),
                 id="zero-concurrency"),
])
def test_every_raise_is_preceded_by_an_error_log(tmp_path, caplog, call):
    """Nobody is watching a two-day run's stdout. The log is the only record,
    and a traceback that never reached a log file never happened."""
    caplog.set_level("ERROR")
    with pytest.raises(worklist.WorklistError):
        call(tmp_path)
    assert caplog.records, "raised without logging at ERROR first"


def test_an_unreadable_brief_keeps_the_underlying_cause(tmp_path):
    """A YAML syntax error and a missing file are different problems; losing
    the cause makes them look the same in the traceback."""
    segment_root = tmp_path / "segments" / "seg-001"
    segment_root.mkdir(parents=True)
    (segment_root / "brief.yaml").write_text("slots: [oops: :\n")
    with pytest.raises(worklist.WorklistError) as excinfo:
        worklist.build_worklist(tmp_path, ["seg-001"], CONFIG)
    assert excinfo.value.__cause__ is not None


def test_neutral_takes_defaults_to_one_when_unconfigured():
    """The neutral invariant must hold on a config that never spelled it out."""
    config = {"dialogue": {"takes_per_slot": 3}, "state": CONFIG["state"]}
    slot = ambient("s", "tone")
    assert worklist.conditions_for(slot, 1, config) == {}
    assert worklist.conditions_for(slot, 2, config) == {"mood": "tense"}


def test_the_resume_log_reports_the_real_planned_and_skipped_counts(tmp_path, caplog):
    """This line is how an operator confirms a resume saw the existing work.
    A count derived from anything other than the actual scan will mislead them
    at exactly the moment they are deciding whether to trust the run."""
    slots = [ambient("s-001"), ambient("s-002")]
    _write_brief(tmp_path, "seg-001", slots)
    segment_root = tmp_path / "segments" / "seg-001"
    for slot_id, take in (("s-001", 1), ("s-001", 2), ("s-001", 3), ("s-002", 1)):
        path = worklist.take_path(segment_root, slot_id, take)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("beats: []\n")

    caplog.set_level("DEBUG")
    units = worklist.build_worklist(tmp_path, ["seg-001"], CONFIG)

    assert len(units) == 2
    messages = [record.getMessage() for record in caplog.records]
    assert any("planned 2" in m and "skipped 4" in m for m in messages), messages
