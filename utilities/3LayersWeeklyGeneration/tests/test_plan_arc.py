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
        reply_for(list(range(0, 6)), continuity_out="Helen collapses at the ford."),
        reply_for(list(range(6, 12))),
        reply_for(list(range(12, 18))),
        reply_for(list(range(18, 24))),
        reply_for(list(range(24, 28))),
    ])
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert "Helen collapses at the ford." in llm.prompts[1]


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
        reply_for(list(range(0, 6)), carry_out={"helen-cursed": True}),
        reply_for(list(range(0, 6))),
        reply_for(list(range(6, 12))),
        reply_for(list(range(12, 18))),
        reply_for(list(range(18, 24))),
        reply_for(list(range(24, 28))),
    ])
    plan_arc.plan_arc(pack, config, llm, vocab, tmp_path / "arc_plan.yaml")
    assert "helen-cursed" in llm.prompts[1]


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
                             synopsis="Helen — bleeding — reaches the moonwell.")])
    config["arc"]["hours_total"] = 36
    plan_arc.plan_arc(pack, config, llm, vocab, out)
    written = out.read_text(encoding="utf-8")
    assert "—" in written
    assert "\\u2014" not in written
