"""Acceptance tests for src/plan_arc.py — the Layer 1 orchestrator.

`plan_arc` is the only function here. It owns resume, batching, retry, skip,
and writing — and nothing else: parsing, validation and prompt construction
all live in `arc_schema` and are tested in test_arc_schema.py.

The three properties under test are the ones a defect in would not show up
until two GPU-days later: the plan is written after EVERY batch, a batch that
will not validate is skipped rather than raised, and resume plans exactly the
orders that are missing — including a hole left by an earlier skip.
"""
import logging
import math

import pytest
import yaml

import plan_arc
from arc_helpers import (CARRY_KEYS, FakeLLM, FakePack, FakeScene, config,
                         pack, perfect_llm, reply_for, segment, vocab)

# --------------------------------------------------------------------------
# plan_arc — the orchestrator
# --------------------------------------------------------------------------

def test_a_clean_run_plans_every_segment(tmp_path, pack, config, vocab):
    out = tmp_path / "arc_plan.yaml"
    llm = perfect_llm(28, 6)
    plan = plan_arc.plan_arc(pack, config, llm, vocab, out)
    assert len(plan["segments"]) == 28
    assert [s["order"] for s in plan["segments"]] == list(range(28))


def test_a_clean_run_uses_one_call_per_batch(tmp_path, pack, config, vocab):
    llm = perfect_llm(28, 6)
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert len(llm.calls) == math.ceil(28 / 6) == 5


def test_batch_size_is_read_from_its_own_config_key_not_segment_hours(
        tmp_path, pack, config, vocab):
    """Regression: plan_arc once read config['arc']['segment_hours'] (hours
    PER segment) where it meant config['arc']['batch_size'] (segments PER
    LLM call) — invisible in every other test here because the shared
    fixture happens to set both to 6. Segment_hours does not even bound
    n_segments in a way that would make the two interchangeable; they are
    unrelated numbers that occasionally coincide."""
    config["arc"]["segment_hours"] = 6      # -> n_segments = 168/6 = 28
    config["arc"]["batch_size"] = 28        # single-shot: one call, all 28
    llm = FakeLLM([reply_for(list(range(0, 28)))])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert len(llm.calls) == 1
    assert len(plan["segments"]) == 28


def test_the_plan_on_disk_matches_what_was_returned(tmp_path, pack, config, vocab):
    out = tmp_path / "arc_plan.yaml"
    plan = plan_arc.plan_arc(pack, config, perfect_llm(28, 6), vocab, out)
    assert yaml.safe_load(out.read_text())["segments"] == plan["segments"]


def test_the_output_directory_is_created(tmp_path, pack, config, vocab):
    out = tmp_path / "deep" / "nested" / "arc_plan.yaml"
    plan_arc.plan_arc(pack, config, perfect_llm(28, 6), vocab, out)
    assert out.exists()


def test_the_plan_is_written_after_every_batch(tmp_path, pack, config, vocab):
    """A 28-segment arc pass is minutes of heavy-model time. Writing only at
    the end means a crash in batch five discards batches one through four; the
    contract is that a crash costs at most one batch."""
    out = tmp_path / "arc_plan.yaml"
    llm = perfect_llm(28, 6)
    seen = {}

    def observe(call_number):
        # Called at the START of call N, so it sees the state left by N-1.
        seen[call_number] = (len(yaml.safe_load(out.read_text())["segments"])
                             if out.exists() else 0)

    llm.on_call = observe
    plan_arc.plan_arc(pack, config, llm, vocab, out)
    assert seen[1] == 0
    assert seen[2] == 6
    assert seen[3] == 12


def test_the_first_batch_is_told_it_is_the_start_of_the_arc(tmp_path, pack, config, vocab):
    llm = perfect_llm(28, 6)
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert "start of the arc" in llm.prompts[0].lower()


def test_later_batches_carry_the_previous_continuity_out(tmp_path, pack, config, vocab):
    """This is the only thread stitching batch N to batch N+1. Without it the
    arc reads as five unrelated novellas."""
    llm = FakeLLM([
        reply_for(list(range(0, 6)), continuity_out="Leena collapses at the ford."),
        reply_for(list(range(6, 12))),
        reply_for(list(range(12, 18))),
        reply_for(list(range(18, 24))),
        reply_for(list(range(24, 28))),
    ])
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert "Leena collapses at the ford." in llm.prompts[1]


def test_the_prompt_states_the_closed_carry_vocabulary(tmp_path, pack, config, vocab):
    """Validation rejects an invented carry key, but rejection costs a retry.
    Telling the model the legal set up front is what makes the happy path the
    common one."""
    llm = perfect_llm(28, 6)
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    for key in CARRY_KEYS:
        assert key in llm.prompts[0]


def test_the_prompt_asks_for_the_orders_it_actually_wants(tmp_path, pack, config, vocab):
    llm = perfect_llm(28, 6)
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert "6" in llm.prompts[1] and "11" in llm.prompts[1]


def test_the_prompt_states_the_closed_spine_scene_vocabulary(tmp_path, pack, config, vocab):
    """Regression: validate_batch rejects any spine_scenes id not in
    vocab.scene_ids, but the prompt never used to state that closed set —
    only scene titles/narration reached the model via `context`, as loose
    prose. Every real batch then failed validation because the model wrote
    plot descriptions instead of the pack's actual (non-ambient) scene ids.
    The prompt must explicitly enumerate every legal spine_scenes id under
    its own labeled section (ambient scene ids are also listed elsewhere in
    the prompt via `context`, for `ambient_focus`, so this checks the
    spine-scenes section specifically rather than presence anywhere)."""
    llm = perfect_llm(28, 6)
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    prompt = llm.prompts[0]
    assert "Legal spine_scenes ids" in prompt
    spine_section = prompt.split("Legal spine_scenes ids", 1)[1].split("Legal carry keys", 1)[0]
    for scene_id in ("arrival", "moonwell", "portal-encounter"):
        assert scene_id in spine_section
    for ambient_id in ("camp-chatter", "road-song"):
        assert ambient_id not in spine_section


# --- retry and skip ---

def test_an_invalid_batch_is_retried(tmp_path, pack, config, vocab):
    llm = FakeLLM([
        "not yaml at all",
        reply_for(list(range(0, 6))),
        reply_for(list(range(6, 12))),
        reply_for(list(range(12, 18))),
        reply_for(list(range(18, 24))),
        reply_for(list(range(24, 28))),
    ])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert len(plan["segments"]) == 28
    assert len(llm.calls) == 6


def test_the_retry_prompt_says_what_was_wrong(tmp_path, pack, config, vocab):
    """A bare re-ask gets the same answer back at the same temperature. Naming
    the offending key is what changes the second attempt."""
    llm = FakeLLM([
        reply_for(list(range(0, 6)), carry_out={"Leena-cursed": True}),
        reply_for(list(range(0, 6))),
        reply_for(list(range(6, 12))),
        reply_for(list(range(12, 18))),
        reply_for(list(range(18, 24))),
        reply_for(list(range(24, 28))),
    ])
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert "Leena-cursed" in llm.prompts[1]


def test_a_batch_that_never_validates_is_skipped_not_raised(tmp_path, pack, config, vocab):
    """Matches `generate_scene`'s own contract. Losing six segments out of
    twenty-eight is a bad afternoon; losing the run is a bad week."""
    llm = FakeLLM([
        "garbage", "still garbage",
        reply_for(list(range(6, 12))),
        reply_for(list(range(12, 18))),
        reply_for(list(range(18, 24))),
        reply_for(list(range(24, 28))),
    ])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert [s["order"] for s in plan["segments"]] == list(range(6, 28))


def test_a_skipped_batch_logs_a_warning(tmp_path, pack, config, vocab, caplog):
    caplog.set_level(logging.DEBUG)
    llm = FakeLLM(["garbage", "still garbage",
                   reply_for(list(range(6, 12))),
                   reply_for(list(range(12, 18))),
                   reply_for(list(range(18, 24))),
                   reply_for(list(range(24, 28)))])
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_an_llm_that_raises_is_retried_and_then_skipped(tmp_path, pack, config, vocab):
    """A dead ollama raises rather than answering badly. Layer 1 must not let
    that difference change its behaviour."""
    llm = FakeLLM([
        RuntimeError("connection refused"), RuntimeError("connection refused"),
        reply_for(list(range(6, 12))),
        reply_for(list(range(12, 18))),
        reply_for(list(range(18, 24))),
        reply_for(list(range(24, 28))),
    ])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert [s["order"] for s in plan["segments"]] == list(range(6, 28))


def test_continuity_after_a_gap_is_keyed_by_order_not_append_position(
        tmp_path, pack, config, vocab):
    """Regression for ring_composition_spec.md section 0.1 bug 2. Batch 1
    (orders 6-11) is skipped entirely (never validates), so `plan_segments`
    has no entry appended for it. The next batch (12-17) must see NO
    immediate predecessor (order 11 was never planned) rather than silently
    inheriting order 5's `continuity_out` via `plan_segments[-1]` — the
    diagnosed failure mode where a gap made a later batch replay a much
    earlier state instead of admitting the chain was broken."""
    config["arc"]["batch_size"] = 6
    llm = FakeLLM([
        reply_for(list(range(0, 6)), continuity_out="They bury Leena's axe."),
        "garbage", "still garbage",                    # orders 6-11: skipped
        reply_for(list(range(12, 18))),                # orders 12-17
        reply_for(list(range(18, 24))),
        reply_for(list(range(24, 28))),
    ])
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    # prompts[0] = orders 0-5, prompts[1]/[2] = the two failed 6-11 attempts,
    # prompts[3] = orders 12-17 — must NOT carry order 5's continuity_out.
    assert "They bury Leena's axe." not in llm.prompts[3]
    assert "Start of the arc." in llm.prompts[3]


def test_a_skipped_batch_does_not_shift_later_orders(tmp_path, pack, config, vocab):
    """The hole stays a hole. Renumbering to close it would make the missing
    six hours unrecoverable — a resume compares against the orders that should
    exist, and shifted orders look complete."""
    llm = FakeLLM(["garbage", "still garbage",
                   reply_for(list(range(6, 12))),
                   reply_for(list(range(12, 18))),
                   reply_for(list(range(18, 24))),
                   reply_for(list(range(24, 28)))])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert 0 not in [s["order"] for s in plan["segments"]]
    assert plan["segments"][0]["order"] == 6


def test_the_run_reports_planned_and_skipped(tmp_path, pack, config, vocab, caplog):
    caplog.set_level(logging.DEBUG)
    llm = FakeLLM(["garbage", "still garbage",
                   reply_for(list(range(6, 12))),
                   reply_for(list(range(12, 18))),
                   reply_for(list(range(18, 24))),
                   reply_for(list(range(24, 28)))])
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    summary = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("22" in m and "6" in m for m in summary), summary


# --- resume ---

def test_resume_plans_only_what_is_missing(tmp_path, pack, config, vocab):
    out = tmp_path / "arc_plan.yaml"
    out.write_text(yaml.safe_dump(
        {"segments": [segment(o) for o in range(12)]}, sort_keys=False))
    llm = FakeLLM([reply_for(list(range(12, 18))),
                   reply_for(list(range(18, 24))),
                   reply_for(list(range(24, 28)))])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, out)
    assert len(llm.calls) == 3
    assert len(plan["segments"]) == 28


def test_resume_keeps_the_existing_segments_verbatim(tmp_path, pack, config, vocab):
    """The already-planned segments are canon — Layer 2 may already have run
    against them. Re-planning one would strand the briefs derived from it."""
    out = tmp_path / "arc_plan.yaml"
    original = segment(0, synopsis="A very specific afternoon.")
    out.write_text(yaml.safe_dump(
        {"segments": [original] + [segment(o) for o in range(1, 12)]},
        sort_keys=False))
    llm = FakeLLM([reply_for(list(range(12, 18))),
                   reply_for(list(range(18, 24))),
                   reply_for(list(range(24, 28)))])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, out)
    assert plan["segments"][0]["synopsis"] == "A very specific afternoon."


def test_resume_refills_a_hole_left_by_an_earlier_skip(tmp_path, pack, config, vocab):
    """The whole point of comparing against expected orders rather than
    appending after the last one. A run that skipped batch 1 leaves orders 0-5
    missing; the naive resume sees `max(order) == 27`, concludes the arc is
    finished, and those six hours never come back."""
    out = tmp_path / "arc_plan.yaml"
    out.write_text(yaml.safe_dump(
        {"segments": [segment(o) for o in range(6, 28)]}, sort_keys=False))
    llm = FakeLLM([reply_for(list(range(0, 6)))])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, out)
    assert len(llm.calls) == 1
    assert [s["order"] for s in plan["segments"]] == list(range(28))


def test_a_fully_planned_arc_makes_no_calls_at_all(tmp_path, pack, config, vocab):
    """Re-running a finished stage must be free, not another five heavy-model
    calls that overwrite good work."""
    out = tmp_path / "arc_plan.yaml"
    out.write_text(yaml.safe_dump(
        {"segments": [segment(o) for o in range(28)]}, sort_keys=False))
    llm = FakeLLM([])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, out)
    assert llm.calls == []
    assert len(plan["segments"]) == 28


def test_the_returned_segments_are_ordered_even_when_resume_backfilled(tmp_path, pack, config, vocab):
    """Layer 2 reads this file top to bottom. A backfilled batch appended at
    the end would put segment 1 after segment 28."""
    out = tmp_path / "arc_plan.yaml"
    out.write_text(yaml.safe_dump(
        {"segments": [segment(o) for o in range(6, 28)]}, sort_keys=False))
    llm = FakeLLM([reply_for(list(range(0, 6)))])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, out)
    orders = [s["order"] for s in plan["segments"]]
    assert orders == sorted(orders)


def test_an_unreadable_existing_plan_raises_rather_than_starting_over(tmp_path, pack, config, vocab):
    """The one place Layer 1 DOES raise. Every other failure costs a batch;
    silently treating a corrupt plan as absent would replan the whole arc and
    overwrite whatever was recoverable in it."""
    out = tmp_path / "arc_plan.yaml"
    out.write_text("segments: [unclosed\n")
    with pytest.raises(plan_arc.ArcPlanError):
        plan_arc.plan_arc(pack, config, FakeLLM([]), vocab, out)


# --------------------------------------------------------------------------
# max_attempts is honoured, not hardcoded
# --------------------------------------------------------------------------

@pytest.mark.parametrize("max_attempts", [1, 2, 3, 4])
def test_a_batch_is_attempted_exactly_max_attempts_times(tmp_path, pack, config,
                                                         vocab, max_attempts):
    """`arc.max_attempts` is a real knob: the heavy model is minutes per call,
    so an operator lowers it to fail fast and raises it when the model is
    flaky. An unrolled "try, then retry once" structure passes at the default
    of 2 and silently ignores every other value."""
    config["arc"]["max_attempts"] = max_attempts
    config["arc"]["hours_total"] = 36          # exactly one batch of 6
    llm = FakeLLM(["garbage"] * max_attempts)
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert len(llm.calls) == max_attempts
    assert plan["segments"] == []


def test_a_batch_that_succeeds_on_the_last_allowed_attempt_is_kept(tmp_path, pack,
                                                                   config, vocab):
    config["arc"]["max_attempts"] = 3
    config["arc"]["hours_total"] = 36
    llm = FakeLLM(["garbage", "still garbage", reply_for(list(range(0, 6)))])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert len(plan["segments"]) == 6


def test_the_model_is_given_a_system_prompt(tmp_path, pack, config, vocab):
    """An empty system prompt is not a neutral default. `OllamaClient.complete`
    sends it as a real `role: system` message, so the heavy model plans a
    168-hour arc with no role framing at all — and every quality problem that
    causes shows up as vague synopses two GPU-days later, where it is
    indistinguishable from the model simply being bad at the job."""
    llm = perfect_llm(28, 6)
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    system_prompt, _ = llm.calls[0]
    assert system_prompt and system_prompt.strip()


def test_the_written_plan_stays_human_readable(tmp_path, pack, config, vocab):
    """`arc_plan.yaml` is reviewed by a person before Layer 2 commits GPU-days
    to it, and campaign prose is full of em dashes and accented names. Dumping
    without `allow_unicode=True` escapes every one of them to `\\u2014`, which
    round-trips through safe_load perfectly and is miserable to actually read.
    """
    out = tmp_path / "arc_plan.yaml"
    llm = FakeLLM([reply_for(list(range(0, 6)),
                             synopsis="Leena — bleeding — reaches the moonwell.")])
    config["arc"]["hours_total"] = 36
    plan_arc.plan_arc(pack, config, llm, vocab, out)
    written = out.read_text(encoding="utf-8")
    assert "—" in written
    assert "\\u2014" not in written


# --------------------------------------------------------------------------
# ring composition (ring_composition_spec.md v3.3, plot_0 only)
# --------------------------------------------------------------------------

def _ring_config(config, parts=(4, 2, 4)):
    config["arc"]["hours_total"] = sum(parts) * 6
    config["arc"]["batch_size"] = 10          # single batch per phase
    config["ring"] = {"enabled": True, "parts": list(parts)}
    return config


def test_keystones_are_generated_before_descent_or_ascent(tmp_path, pack, config, vocab):
    """Spec section 8.3 phase 1: all keystones first, shallowest layer
    outward. With parts (4,2,4) the keystone orders are 4-5; the first LLM
    call must ask for those, not order 0."""
    config = _ring_config(config)
    llm = FakeLLM([
        reply_for([4, 5]),
        reply_for([0, 1, 2, 3]),
        reply_for([6, 7, 8, 9], mirror_transform="knowledge_gained"),
    ])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert "4" in llm.prompts[0] and "5" in llm.prompts[0]
    assert len(plan["segments"]) == 10


def test_a_skipped_plot0_keystone_is_fatal(tmp_path, pack, config, vocab):
    """Spec section 8.3: 'A skipped plot_0 keystone is fatal: raise rather
    than continue.' Every attempt at the keystone batch fails to validate."""
    config = _ring_config(config)
    config["arc"]["max_attempts"] = 1
    llm = FakeLLM(["garbage"])
    with pytest.raises(plan_arc.ArcPlanError, match="keystone"):
        plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")


def test_ascent_segments_require_a_mirror_transform(tmp_path, pack, config, vocab):
    """An ascent segment reply missing mirror_transform must fail validation
    and retry, per arc_schema.validate_batch's ring_roles check."""
    config = _ring_config(config)
    llm = FakeLLM([
        reply_for([4, 5]),
        reply_for([0, 1, 2, 3]),
        reply_for([6, 7, 8, 9]),                      # no mirror_transform: fails
        reply_for([6, 7, 8, 9], mirror_transform="bond_proven"),
    ])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    ascent = [s for s in plan["segments"] if s["order"] >= 6]
    assert all(s["mirror_transform"] == "bond_proven" for s in ascent)


def test_plot_path_and_mirror_of_are_stamped_onto_segments(tmp_path, pack, config, vocab):
    config = _ring_config(config)
    llm = FakeLLM([
        reply_for([4, 5]),
        reply_for([0, 1, 2, 3]),
        reply_for([6, 7, 8, 9], mirror_transform="knowledge_gained"),
    ])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    by_order = {s["order"]: s for s in plan["segments"]}
    assert by_order[4]["plot_path"][0]["role"] == "keystone"
    assert by_order[0]["plot_path"][0]["role"] == "descent"
    assert by_order[9]["plot_path"][0]["role"] == "ascent"
    # order 0 (descent, u in [3/4,1)) mirrors order 9 (ascent, u in [3/4,1))
    assert by_order[0]["mirror_of"] == ["seg-010"]
    assert by_order[9]["mirror_of"] == ["seg-001"]
    # a keystone has no mirror (spec section 2)
    assert by_order[4]["mirror_of"] == []


def test_mirror_brief_is_sent_for_an_ascent_segment(tmp_path, pack, config, vocab):
    """Spec section 6.4: the ascent's prompt must state what its mirror
    partner established, and instruct continuity_in to continue from the
    PRECEDING segment rather than the mirror."""
    config = _ring_config(config)
    llm = FakeLLM([
        reply_for([4, 5]),
        reply_for([0, 1, 2, 3], synopsis="They lose the axe at the ford."),
        reply_for([6, 7, 8, 9], mirror_transform="knowledge_gained"),
    ])
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    ascent_prompt = llm.prompts[2]
    assert "They lose the axe at the ford." in ascent_prompt
    assert "PRECEDING segment, not from the mirror" in ascent_prompt


def test_ring_disabled_by_default_keeps_old_single_phase_behavior(tmp_path, pack,
                                                                   config, vocab):
    """No `ring` key in config at all — the common case for every existing
    caller — must reproduce byte-identical batching: ascending order, one
    phase, no ring vocabulary in the prompt."""
    assert "ring" not in config
    llm = perfect_llm(28, 6)
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert [s["order"] for s in plan["segments"]] == list(range(28))
    assert "RING STRUCTURE" not in llm.prompts[0]


def test_a_reused_id_is_renamed_instead_of_discarding_the_whole_batch(
        tmp_path, pack, config, vocab):
    """Regression: a real run against qwen3-coder:30b showed the model
    reusing an already-planned id verbatim despite build_prompt's explicit
    'suffix a repeat visit' instruction, discarding an otherwise well-formed
    batch on every retry. The id is an arbitrary label; renaming it on
    collision recovers the batch's actual content instead of burning all
    max_attempts on a non-content problem."""
    config["arc"]["hours_total"] = 72     # two batches of 6
    llm = FakeLLM([
        reply_for(list(range(0, 6))),
        reply_for(list(range(6, 12)), id="seg-001"),   # reuses order 0's id
    ])
    plan = plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert len(plan["segments"]) == 12
    ids = [s["id"] for s in plan["segments"]]
    assert len(ids) == len(set(ids)), f"duplicate ids survived: {ids}"
