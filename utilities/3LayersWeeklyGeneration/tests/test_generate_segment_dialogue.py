"""Acceptance tests for src/generate_segment_dialogue.py — Layer 3.

Layer 3 turns a segment's brief into actual voiced takes. It owns almost no
logic of its own: `worklist` decides what to generate and under which
conditions, `pool` runs the workers, and `app/campaign/batch_generate` writes
the files. This module is the wiring plus one small take function.

Three things here are easy to get wrong and expensive to get wrong.

  - THE TAKE FUNCTION IS LOCAL, NOT `batch_generate._generate_take`. That one
    calls `update_context(scene=..., loop=take, carry={})` — it overwrites
    `loop` with the TAKE NUMBER and blanks `carry` unconditionally. `carry` is
    the only channel the arc's state reaches the model through, so reusing it
    would silently produce takes with no conditions at all, identical to their
    neutral siblings, with nothing in any log to say so.

  - `loop` COMES FROM THE BRIEF. It is the campaign's time-loop iteration, not
    a counter of retries.

  - THE NEUTRAL-TAKE GUARD IS HARD. Every ambient slot must end with exactly
    one take whose conditions are `{}`. A library missing one can dead-air at
    runtime, which is the single failure the whole `neutral_takes` design
    exists to prevent. It raises; it does not warn.
"""
import pathlib

import pytest
import yaml

import generate_segment_dialogue as gsd
import worklist
from arc_helpers import FakeCastMember, FakePack, pack, vocab
from segment_helpers import ambient_slot, segment_config, spine_slot


@pytest.fixture
def config(segment_config):
    segment_config["dialogue"].update({
        "concurrency": 2,
        "max_attempts": 2,
        "seconds_per_take": 30,
        "breaker": {"window": 50, "failure_rate": 0.5, "min_samples": 20},
        "models": {"heavy": {"model": "hermes3:70b"}},
        "active_model": "heavy",
    })
    return segment_config


class FakeBeat:
    """Matches what _beat_to_dict reads: kind, speaker, text."""

    def __init__(self, speaker="helen", text="The fire is low.", kind="line"):
        self.kind = kind
        self.speaker = speaker
        self.text = text
        self.speaker_id = speaker


class RecordingImproviser:
    """Captures the context it was given and returns canned beats."""

    def __init__(self, beats=None):
        self.pack = None
        self.scene = None
        self.carry = {}
        self.loop = 1
        self.recent = []
        self.contexts = []
        self.scenes_generated = []
        self._beats = beats if beats is not None else [FakeBeat()]

    def update_context(self, scene=None, carry=None, loop=None):
        if scene is not None:
            self.scene = scene
        if carry is not None:
            self.carry = carry
        if loop is not None:
            self.loop = loop
        self.contexts.append({"scene": self.scene, "carry": dict(self.carry),
                              "loop": self.loop})

    def generate_scene(self, scene):
        self.scenes_generated.append(scene)
        return list(self._beats)


def unit(slot=None, conditions=None, take=1, tmp_path=None, loop=3):
    slot = slot or ambient_slot(1, loop=loop)
    return worklist.WorkUnit(
        segment_id="seg-001", slot_id=slot["slot_id"], take=take,
        conditions=conditions if conditions is not None else {},
        path=(tmp_path or pathlib.Path("/tmp/nonexistent")) / "001.yaml",
        slot=slot)


# ---------------------------------------------------------------------------
# generate_take — the local take function
# ---------------------------------------------------------------------------

def test_generate_take_returns_beat_dicts(config):
    improviser = RecordingImproviser()
    beats = gsd.generate_take(improviser, unit(), config)

    assert beats
    assert isinstance(beats[0], dict)
    assert beats[0]["text"] == "The fire is low."


def test_generate_take_calls_generate_scene(config):
    improviser = RecordingImproviser()
    gsd.generate_take(improviser, unit(), config)
    assert len(improviser.scenes_generated) == 1


def test_the_scene_carries_the_slot_prompt_and_lore(config):
    slot = ambient_slot(1, prompt="Buffalo needles Helen.", lore=["the-loop"])
    improviser = RecordingImproviser()

    gsd.generate_take(improviser, unit(slot=slot), config)

    scene = improviser.scenes_generated[0]
    assert scene.prompt == "Buffalo needles Helen."
    assert scene.lore == ["the-loop"]


def test_the_loop_comes_from_the_brief_not_the_take_number(config):
    """Issue #6. batch_generate._generate_take passes the TAKE NUMBER as
    `loop`, which would tell the model it is on iteration 3 of the time loop
    when it is really on take 3 of iteration 1."""
    improviser = RecordingImproviser()

    gsd.generate_take(improviser, unit(take=3, loop=7), config)

    assert improviser.loop == 7


def test_conditions_reach_the_model_through_carry(config):
    """`update_context(scene, carry, loop)` is the ONLY context channel, and
    generate_scene renders each carry key/value into the prompt. A condition
    that never reaches carry leaves the take silently identical to its
    neutral sibling."""
    improviser = RecordingImproviser()

    gsd.generate_take(improviser, unit(conditions={"mood": "tense"}), config)

    assert improviser.carry == {"mood": "tense"}


def test_the_neutral_take_passes_empty_conditions(config):
    improviser = RecordingImproviser()
    gsd.generate_take(improviser, unit(conditions={}), config)
    assert improviser.carry == {}


def test_the_transcript_window_is_reset_between_takes(config):
    """Two takes of the SAME slot are alternates, not a conversation. Leaving
    `recent` populated makes take 2 read as a reply to take 1."""
    improviser = RecordingImproviser()
    improviser.recent = ["helen: something from a previous slot"]

    gsd.generate_take(improviser, unit(), config)

    assert improviser.recent == []


def test_generate_take_never_raises(config):
    """An exception is the pool's signal for a failed attempt, but this
    function returning [] is cleaner and is what the pool expects."""
    class Exploding(RecordingImproviser):
        def generate_scene(self, scene):
            raise RuntimeError("ollama fell over")

    assert gsd.generate_take(Exploding(), unit(), config) == []


def test_generate_take_does_not_use_batch_generates_take_function(config, monkeypatch):
    """Guard against the reuse the plan explicitly rejects."""
    from campaign import batch_generate

    def forbidden(*args, **kwargs):
        raise AssertionError("_generate_take must not be called")

    monkeypatch.setattr(batch_generate, "_generate_take", forbidden,
                        raising=False)
    gsd.generate_take(RecordingImproviser(), unit(), config)


# ---------------------------------------------------------------------------
# The neutral-take guard (issue #14) — hard failure
# ---------------------------------------------------------------------------

def _library(tmp_path, conditions_per_slot):
    """Write a fake take library: {slot_id: [conditions, ...]}."""
    root = tmp_path / "seg-001"
    for slot_id, conditions_list in conditions_per_slot.items():
        for take, conditions in enumerate(conditions_list, start=1):
            path = worklist.take_path(root, slot_id, take)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump({"slot_id": slot_id, "take": take,
                                            "conditions": conditions,
                                            "beats": [{"speaker": "helen",
                                                       "text": "hi"}]}),
                            encoding="utf-8")
    return root


def test_the_guard_passes_a_library_with_a_neutral_take(tmp_path):
    root = _library(tmp_path, {"s-001": [{}, {"mood": "tense"}]})
    gsd.assert_neutral_take_coverage(root, ["s-001"])


def test_the_guard_raises_when_a_slot_has_no_neutral_take(tmp_path):
    root = _library(tmp_path, {"s-001": [{"mood": "tense"}, {"mood": "weary"}]})

    with pytest.raises(gsd.NeutralTakeError) as excinfo:
        gsd.assert_neutral_take_coverage(root, ["s-001"])

    assert "s-001" in str(excinfo.value)


def test_the_guard_raises_when_a_slot_has_no_takes_at_all(tmp_path):
    root = _library(tmp_path, {"s-001": [{}]})

    with pytest.raises(gsd.NeutralTakeError):
        gsd.assert_neutral_take_coverage(root, ["s-001", "s-002"])


def test_the_guard_names_every_offending_slot_not_just_the_first(tmp_path):
    """An operator fixing these wants the whole list, not one per run."""
    root = _library(tmp_path, {"s-001": [{"mood": "tense"}],
                               "s-002": [{}],
                               "s-003": [{"mood": "weary"}]})

    with pytest.raises(gsd.NeutralTakeError) as excinfo:
        gsd.assert_neutral_take_coverage(root, ["s-001", "s-002", "s-003"])

    message = str(excinfo.value)
    assert "s-001" in message and "s-003" in message


# ---------------------------------------------------------------------------
# Wiring — what reaches the pool
# ---------------------------------------------------------------------------

@pytest.fixture
def no_guard(monkeypatch):
    """The wiring tests fake `run_pool`, so no takes are ever written and the
    neutral-take guard would correctly fire on an empty library. The guard has
    its own tests above; here it is out of scope."""
    monkeypatch.setattr(gsd, "assert_neutral_take_coverage", lambda *a, **k: None)


def _brief(tmp_path, slots, segment_id="seg-001"):
    root = tmp_path / "segments" / segment_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "brief.yaml").write_text(yaml.safe_dump({
        "segment_id": segment_id, "order": 0, "loop": 3, "hours": 6,
        "synopsis": "x", "continuity_in": "a", "continuity_out": "b",
        "carry_in": {}, "carry_out": {}, "slots": slots}), encoding="utf-8")
    return tmp_path


def test_spine_slots_never_reach_the_pool(tmp_path, pack, config, monkeypatch, no_guard):
    """`kind: spine` is authored canon. A spine slot reaching the pool would
    overwrite it with generated dialogue."""
    out_root = _brief(tmp_path, [ambient_slot(1), spine_slot(2)])
    seen = {}

    def fake_run_pool(units, **kwargs):
        seen["units"] = list(units)
        import pool
        return pool.PoolStats(planned=len(seen["units"]), written=0, failed=0)

    monkeypatch.setattr(gsd.pool, "run_pool", fake_run_pool)
    gsd.generate_segment_dialogue(pack, ["seg-001"], config, object(), out_root)

    assert all(u.slot["kind"] == "ambient" for u in seen["units"])


def test_one_improviser_is_built_per_worker_thread(tmp_path, pack, config,
                                                   monkeypatch, no_guard):
    """D8's contract: LLMImproviser is not thread-safe."""
    captured = {}

    def fake_run_pool(units, *, worker_factory, **kwargs):
        captured["a"] = worker_factory()
        captured["b"] = worker_factory()
        import pool
        return pool.PoolStats(planned=0, written=0, failed=0)

    out_root = _brief(tmp_path, [ambient_slot(1)])
    monkeypatch.setattr(gsd.pool, "run_pool", fake_run_pool)
    gsd.generate_segment_dialogue(pack, ["seg-001"], config, object(), out_root)

    assert captured["a"] is not captured["b"]


def test_the_breaker_is_built_from_config(tmp_path, pack, config, monkeypatch, no_guard):
    captured = {}

    def fake_run_pool(units, *, breaker=None, **kwargs):
        captured["breaker"] = breaker
        import pool
        return pool.PoolStats(planned=0, written=0, failed=0)

    out_root = _brief(tmp_path, [ambient_slot(1)])
    monkeypatch.setattr(gsd.pool, "run_pool", fake_run_pool)
    gsd.generate_segment_dialogue(pack, ["seg-001"], config, object(), out_root)

    assert captured["breaker"].window == 50
    assert captured["breaker"].min_samples == 20


def test_concurrency_comes_from_config(tmp_path, pack, config, monkeypatch, no_guard):
    captured = {}

    def fake_run_pool(units, *, concurrency=None, **kwargs):
        captured["concurrency"] = concurrency
        import pool
        return pool.PoolStats(planned=0, written=0, failed=0)

    out_root = _brief(tmp_path, [ambient_slot(1)])
    monkeypatch.setattr(gsd.pool, "run_pool", fake_run_pool)
    gsd.generate_segment_dialogue(pack, ["seg-001"], config, object(), out_root)

    assert captured["concurrency"] == 2


def test_cancel_check_is_passed_through_to_the_pool(tmp_path, pack, config,
                                                    monkeypatch, no_guard):
    """v3/V11 — the pool owns the polling policy; this module only forwards."""
    captured = {}

    def fake_run_pool(units, *, cancel_check=None, **kwargs):
        captured["cancel_check"] = cancel_check
        import pool
        return pool.PoolStats(planned=0, written=0, failed=0)

    out_root = _brief(tmp_path, [ambient_slot(1)])
    monkeypatch.setattr(gsd.pool, "run_pool", fake_run_pool)
    sentinel = lambda: False
    gsd.generate_segment_dialogue(pack, ["seg-001"], config, object(), out_root,
                                  cancel_check=sentinel)

    assert captured["cancel_check"] is sentinel


def test_progress_is_reported_per_segment(tmp_path, pack, config, monkeypatch,
                                          no_guard):
    seen = []

    def fake_run_pool(units, **kwargs):
        import pool
        return pool.PoolStats(planned=1, written=1, failed=0)

    out_root = _brief(tmp_path, [ambient_slot(1)])
    _brief(tmp_path, [ambient_slot(1)], segment_id="seg-002")
    monkeypatch.setattr(gsd.pool, "run_pool", fake_run_pool)

    gsd.generate_segment_dialogue(pack, ["seg-001", "seg-002"], config, object(),
                                  out_root, progress=lambda *a: seen.append(a))

    assert len(seen) == 2
    assert seen[-1][0] == 2


def test_the_defaults_keep_the_old_signature_working(tmp_path, pack, config,
                                                     monkeypatch, no_guard):
    def fake_run_pool(units, **kwargs):
        import pool
        return pool.PoolStats(planned=0, written=0, failed=0)

    out_root = _brief(tmp_path, [ambient_slot(1)])
    monkeypatch.setattr(gsd.pool, "run_pool", fake_run_pool)

    assert gsd.generate_segment_dialogue(pack, ["seg-001"], config, object(),
                                         out_root) is not None


def test_no_network_call_is_made_anywhere(tmp_path, pack, config, monkeypatch, no_guard):
    """Every test in this file runs with a fake client; assert the module
    never reaches for a real one on its own."""
    def fake_run_pool(units, **kwargs):
        import pool
        return pool.PoolStats(planned=0, written=0, failed=0)

    out_root = _brief(tmp_path, [ambient_slot(1)])
    monkeypatch.setattr(gsd.pool, "run_pool", fake_run_pool)
    import concurrent_llm
    monkeypatch.setattr(concurrent_llm, "from_profile",
                        lambda *a, **k: pytest.fail("built its own client"))

    gsd.generate_segment_dialogue(pack, ["seg-001"], config, object(), out_root)
