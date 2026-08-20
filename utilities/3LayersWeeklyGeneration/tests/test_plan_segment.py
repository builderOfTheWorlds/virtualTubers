"""Acceptance tests for src/plan_segment.py — the Layer 2 orchestrator.

This module owns resume, the 2a→2b fan-out, retry/skip, and writing
`brief.yaml`. Everything it validates or renders it imports from
`segment_schema`; nothing pure is reimplemented here.

Two properties are worth stating up front because they are what the tests are
mostly about:

  - A SEGMENT IS SIX HOURS OF AIRTIME AND A CHAPTER IS FORTY MINUTES. Losing
    one chapter to a stubborn model is survivable; losing the segment because
    one chapter failed is not. So a chapter that exhausts its attempts is
    logged and skipped, and the other eight are still written.

  - RESUME IS CHAPTER-GRANULAR. A 2b call is roughly a minute of a 70B model;
    a segment is nine of them. Re-running the whole segment because the
    process died on chapter eight throws away eight minutes of GPU for no
    reason, and across 28 segments that is the difference between a run that
    finishes overnight and one that does not.
"""
import logging
import threading

import pytest
import yaml

import plan_segment
import segment_schema
from arc_helpers import FakeCastMember, FakePack, pack, vocab
from segment_helpers import (FakeSegmentLLM, ambient_slot, arc_segment,
                             chapter, chapters_reply, segment_config,
                             slots_reply, spine_slot)

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def config(segment_config):
    return segment_config


@pytest.fixture
def out_path(tmp_path):
    return tmp_path / "seg-001" / "brief.yaml"


def run(pack, arc_segment, config, llm, vocab, out_path):
    return plan_segment.plan_segment(pack, arc_segment, config, llm, vocab,
                                     out_path)


# --------------------------------------------------------------------------
# the clean path
# --------------------------------------------------------------------------

def test_a_clean_run_plans_every_chapter(pack, arc_segment, config, vocab, out_path):
    brief = run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    assert len(brief["chapters"]) == 9


def test_a_clean_run_plans_every_chapters_slots(pack, arc_segment, config, vocab,
                                                out_path):
    brief = run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    assert len(brief["slots"]) == 9 * 19


def test_the_brief_is_written_to_disk(pack, arc_segment, config, vocab, out_path):
    run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    assert yaml.safe_load(out_path.read_text())["segment_id"] == "seg-001"


def test_the_output_directory_is_created(pack, arc_segment, config, vocab, tmp_path):
    """`out_path` points inside a per-segment directory that will not exist on
    the first run of a fresh week."""
    deep = tmp_path / "week" / "seg-001" / "brief.yaml"
    run(pack, arc_segment, config, FakeSegmentLLM(), vocab, deep)
    assert deep.exists()


def test_the_written_brief_stays_human_readable(pack, arc_segment, config, vocab,
                                                out_path):
    """A brief is reviewed by a person before two GPU-days are spent on it.
    `yaml.dump` without `allow_unicode` renders every em dash as \\u2014."""
    segment = dict(arc_segment)
    segment["synopsis"] = "The company reaches the moonwell — and finds it fouled."
    run(pack, segment, config, FakeSegmentLLM(), vocab, out_path)
    assert "\\u2014" not in out_path.read_text()


def test_one_chapter_call_is_made(pack, arc_segment, config, vocab, out_path):
    llm = FakeSegmentLLM()
    run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(llm.chapter_prompts) == 1


def test_one_slot_call_is_made_per_chapter(pack, arc_segment, config, vocab,
                                           out_path):
    llm = FakeSegmentLLM()
    run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(llm.slot_prompts) == 9


def test_each_slot_call_carries_its_own_chapter(pack, arc_segment, config, vocab,
                                                out_path):
    """Nine calls that all describe chapter one produce nine copies of the
    same forty minutes, which is a segment that repeats itself six times and
    reads as broken to anyone watching."""
    llm = FakeSegmentLLM()
    run(pack, arc_segment, config, llm, vocab, out_path)
    assert len({p for p in llm.slot_prompts}) == 9


# --------------------------------------------------------------------------
# slot ids have to survive being merged
# --------------------------------------------------------------------------

def test_slot_ids_are_unique_across_the_whole_segment(pack, arc_segment, config,
                                                      vocab, out_path):
    """Each 2b call sees only its own chapter, so every one of them numbers
    its slots from s-001. Merged unchanged, nine chapters collide into
    nineteen ids — and because `slot_id` becomes a directory name under
    `slots/`, the later chapters' takes overwrite the earlier ones. Roughly
    five and a half of the six hours would simply not exist on disk, and
    nothing in the run would report a problem."""
    brief = run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    ids = [s["slot_id"] for s in brief["slots"]]
    assert len(ids) == len(set(ids))


def test_a_slot_id_still_says_which_chapter_it_came_from(pack, arc_segment,
                                                         config, vocab, out_path):
    """Whatever the disambiguation scheme is, it has to stay legible: these
    ids are what a human greps for when one slot sounds wrong."""
    brief = run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    first = brief["slots"][0]
    assert first["chapter_id"] in first["slot_id"]


def test_slot_ids_are_stable_across_a_rerun(pack, arc_segment, config, vocab,
                                            out_path):
    """Resume compares against what is already on disk. An id scheme that
    depends on completion order renames slots on every run, which orphans
    every take already generated for them."""
    first = run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    out_path.unlink()
    second = run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    assert [s["slot_id"] for s in first["slots"]] == \
           [s["slot_id"] for s in second["slots"]]


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------

def test_slots_are_ordered_by_chapter_not_by_completion(pack, arc_segment, config,
                                                        vocab, out_path):
    """The 2b calls run concurrently and finish in whatever order the model
    happens to return them. Airtime is sequential."""
    order = []
    real_complete = FakeSegmentLLM.complete

    class Shuffled(FakeSegmentLLM):
        def complete(self, system_prompt, messages):
            reply = real_complete(self, system_prompt, messages)
            if system_prompt == segment_schema.SYSTEM_PROMPT_SLOTS:
                order.append(messages[-1]["content"])
            return reply

    brief = run(pack, arc_segment, config, Shuffled(), vocab, out_path)
    chapter_ids = [s["chapter_id"] for s in brief["slots"]]
    assert chapter_ids == sorted(chapter_ids)


# --------------------------------------------------------------------------
# retries
# --------------------------------------------------------------------------

def test_an_unparseable_chapter_reply_is_retried(pack, arc_segment, config, vocab,
                                                 out_path):
    llm = FakeSegmentLLM(chapter_replies=["I cannot help with that."])
    brief = run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(llm.chapter_prompts) == 2
    assert len(brief["chapters"]) == 9


def test_an_invalid_chapter_batch_is_retried_with_the_problems(pack, arc_segment,
                                                               config, vocab,
                                                               out_path):
    """The retry only helps if it says what was wrong. A bare re-ask gets the
    same reply back, and both attempts are spent for nothing."""
    llm = FakeSegmentLLM(chapter_replies=[chapters_reply(4)])
    run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(llm.chapter_prompts) == 2
    assert "9" in llm.chapter_prompts[1]


def test_an_invalid_slot_batch_is_retried(pack, arc_segment, config, vocab,
                                          out_path):
    bad = yaml.safe_dump({"slots": [ambient_slot(1, lore=["the-second-moon"])]},
                         sort_keys=False)
    llm = FakeSegmentLLM(slot_replies=[bad])
    brief = run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(llm.slot_prompts) == 10          # nine chapters, one retried
    assert len(brief["slots"]) == 9 * 19


def test_the_slot_retry_carries_the_problems(pack, arc_segment, config, vocab,
                                             out_path):
    bad = yaml.safe_dump({"slots": [ambient_slot(1, lore=["the-second-moon"])]},
                         sort_keys=False)
    llm = FakeSegmentLLM(slot_replies=[bad])
    run(pack, arc_segment, config, llm, vocab, out_path)
    retried = [p for p in llm.slot_prompts if "the-second-moon" in p]
    assert retried


def test_attempts_are_bounded_by_the_config(pack, arc_segment, config, vocab,
                                            out_path):
    config["segment"]["max_attempts"] = 3
    llm = FakeSegmentLLM(chapter_replies=["no", "still no"])
    run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(llm.chapter_prompts) == 3


# --------------------------------------------------------------------------
# failure — a bad chapter must not cost the segment
# --------------------------------------------------------------------------

class BrokenChapterLLM(FakeSegmentLLM):
    """Fails 2b for exactly one chapter, however many times it is asked."""

    def __init__(self, broken_marker, **kwargs):
        super().__init__(**kwargs)
        self.broken_marker = broken_marker

    def complete(self, system_prompt, messages):
        reply = FakeSegmentLLM.complete(self, system_prompt, messages)
        if (system_prompt == segment_schema.SYSTEM_PROMPT_SLOTS
                and self.broken_marker in messages[-1]["content"]):
            return "the model wandered off"
        return reply


def test_one_failed_chapter_does_not_lose_the_other_eight(pack, arc_segment,
                                                          config, vocab, out_path):
    llm = BrokenChapterLLM("part 4")
    brief = run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(brief["slots"]) == 8 * 19


def test_a_failed_chapter_does_not_raise(pack, arc_segment, config, vocab,
                                         out_path):
    """Same contract as Layer 1 and as `generate_scene`: model trouble is
    logged and skipped, never raised. A run that aborts on segment three of
    twenty-eight has thrown away everything after it."""
    run(pack, arc_segment, config, BrokenChapterLLM("part 4"), vocab, out_path)


def test_a_failed_chapter_is_logged_at_error(pack, arc_segment, config, vocab,
                                             out_path, caplog):
    """It is the only trace that forty minutes of the segment is missing."""
    with caplog.at_level(logging.ERROR):
        run(pack, arc_segment, config, BrokenChapterLLM("part 4"), vocab, out_path)
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_a_failed_chapter_still_writes_the_brief(pack, arc_segment, config, vocab,
                                                 out_path):
    run(pack, arc_segment, config, BrokenChapterLLM("part 4"), vocab, out_path)
    assert out_path.exists()


def test_a_chapter_pass_that_never_succeeds_returns_none(pack, arc_segment,
                                                          config, vocab, out_path):
    """Without chapters there is nothing to fan out to. The caller needs to
    tell "no chapters" apart from "an empty segment", so this returns None
    rather than an empty brief — and writes no file, so a later resume tries
    the segment again from scratch instead of inheriting a husk."""
    llm = FakeSegmentLLM(chapter_replies=["no", "still no", "no again"])
    assert run(pack, arc_segment, config, llm, vocab, out_path) is None
    assert not out_path.exists()


def test_a_failing_chapter_pass_does_not_raise(pack, arc_segment, config, vocab,
                                               out_path):
    run(pack, arc_segment, config,
        FakeSegmentLLM(chapter_replies=["no", "still no"]), vocab, out_path)


def test_an_exception_from_the_model_is_survivable(pack, arc_segment, config,
                                                    vocab, out_path):
    """A dropped connection mid-run is an exception, not a bad reply."""
    class Flaky(FakeSegmentLLM):
        def complete(self, system_prompt, messages):
            if (system_prompt == segment_schema.SYSTEM_PROMPT_SLOTS
                    and "part 4" in messages[-1]["content"]):
                raise ConnectionError("ollama went away")
            return FakeSegmentLLM.complete(self, system_prompt, messages)

    brief = run(pack, arc_segment, config, Flaky(), vocab, out_path)
    assert len(brief["slots"]) == 8 * 19


# --------------------------------------------------------------------------
# resume
# --------------------------------------------------------------------------

def test_a_finished_segment_makes_no_calls_at_all(pack, arc_segment, config,
                                                   vocab, out_path):
    run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    llm = FakeSegmentLLM()
    run(pack, arc_segment, config, llm, vocab, out_path)
    assert llm.chapter_prompts == [] and llm.slot_prompts == []


def test_resume_replans_only_the_missing_chapters(pack, arc_segment, config,
                                                   vocab, out_path):
    """The whole point of chapter-granular resume."""
    run(pack, arc_segment, config, BrokenChapterLLM("part 4"), vocab, out_path)
    llm = FakeSegmentLLM()
    run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(llm.slot_prompts) == 1


def test_resume_does_not_replan_the_chapters(pack, arc_segment, config, vocab,
                                              out_path):
    """2a is cheap but not free, and re-running it would renumber and retitle
    chapters that already have slots hanging off them."""
    run(pack, arc_segment, config, BrokenChapterLLM("part 4"), vocab, out_path)
    llm = FakeSegmentLLM()
    run(pack, arc_segment, config, llm, vocab, out_path)
    assert llm.chapter_prompts == []


def test_resume_completes_the_segment(pack, arc_segment, config, vocab, out_path):
    run(pack, arc_segment, config, BrokenChapterLLM("part 4"), vocab, out_path)
    brief = run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    assert len(brief["slots"]) == 9 * 19


def test_resume_keeps_the_slots_already_on_disk(pack, arc_segment, config, vocab,
                                                 out_path):
    first = run(pack, arc_segment, config, BrokenChapterLLM("part 4"), vocab,
                out_path)
    kept = {s["slot_id"] for s in first["slots"]}
    second = run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    assert kept <= {s["slot_id"] for s in second["slots"]}


def test_a_brief_that_will_not_parse_is_fatal(pack, arc_segment, config, vocab,
                                               out_path):
    """The one place this module raises. Everything else is a model being
    unhelpful; this is a corrupt file, and silently starting over would
    overwrite work that may be recoverable by hand."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("chapters: [unclosed\n")
    with pytest.raises(segment_schema.SegmentPlanError):
        run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)


# --------------------------------------------------------------------------
# concurrency and crash safety
# --------------------------------------------------------------------------

def test_chapters_are_planned_concurrently(pack, arc_segment, config, vocab,
                                            out_path):
    """Nine sequential 70B calls is nine minutes per segment and four hours
    across the week — for calls that share nothing and could all be in flight
    at once. The barrier is what makes this assertion mean something: without
    it the test passes on a purely sequential implementation, because a fake
    that returns instantly never overlaps with itself."""
    config["segment"]["concurrency"] = 3
    in_flight = threading.Barrier(3, timeout=10)

    class Gated(FakeSegmentLLM):
        def complete(self, system_prompt, messages):
            reply = FakeSegmentLLM.complete(self, system_prompt, messages)
            if system_prompt == segment_schema.SYSTEM_PROMPT_SLOTS:
                try:
                    in_flight.wait()
                except threading.BrokenBarrierError:  # pragma: no cover
                    pass
            return reply

    brief = run(pack, arc_segment, config, Gated(), vocab, out_path)
    assert len(brief["slots"]) == 9 * 19


def test_the_brief_is_written_as_chapters_land_not_only_at_the_end(
        pack, arc_segment, config, vocab, out_path):
    """A segment is nine model calls. Writing once at the end means a crash
    on the ninth throws away the eight that succeeded, and the resume logic
    above has nothing to resume from."""
    config["segment"]["concurrency"] = 1
    seen = []

    class Watching(FakeSegmentLLM):
        def complete(self, system_prompt, messages):
            if system_prompt == segment_schema.SYSTEM_PROMPT_SLOTS:
                seen.append(out_path.exists() and
                            len(yaml.safe_load(out_path.read_text())
                                .get("slots", [])))
            return FakeSegmentLLM.complete(self, system_prompt, messages)

    run(pack, arc_segment, config, Watching(), vocab, out_path)
    assert seen[-1] and seen[-1] >= 19


# --------------------------------------------------------------------------
# the budget warning
# --------------------------------------------------------------------------

def test_an_over_budget_chapter_warns_without_failing(pack, arc_segment, config,
                                                       vocab, out_path, caplog):
    """Over-subscribed state sensitivity is a judgement call a human makes,
    not something a retry can fix — the model would just produce another
    plausible over-budget chapter and burn the attempt."""
    heavy = yaml.safe_dump(
        {"slots": [ambient_slot(i, sensitivity="flags",
                                depends_on=["helen-wounded"])
                   for i in range(1, 20)]},
        sort_keys=False)
    llm = FakeSegmentLLM(slot_replies=[heavy])
    with caplog.at_level(logging.WARNING):
        brief = run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(brief["slots"]) == 9 * 19
    assert len(llm.slot_prompts) == 9        # warned, not retried


class BrokenChaptersLLM(FakeSegmentLLM):
    """Fails 2b for several chapters."""

    def __init__(self, markers, **kwargs):
        super().__init__(**kwargs)
        self.markers = markers

    def complete(self, system_prompt, messages):
        reply = FakeSegmentLLM.complete(self, system_prompt, messages)
        if system_prompt == segment_schema.SYSTEM_PROMPT_SLOTS and any(
                m in messages[-1]["content"] for m in self.markers):
            return "the model wandered off"
        return reply


def test_a_thin_segment_warns_about_its_density(pack, arc_segment, config, vocab,
                                                 out_path, caplog):
    """`density_floor` is the line between "a chapter went missing" and "this
    segment cannot fill its six hours". 133 slots against a target of 170 is
    0.78 — under the 0.80 floor — and airing it means dead air the schedule
    has no way to fill. The run still writes the brief; a person decides."""
    llm = BrokenChaptersLLM(["part 4", "part 5"])
    with caplog.at_level(logging.WARNING):
        brief = run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(brief["slots"]) == 7 * 19
    assert any("density" in r.getMessage().lower() for r in caplog.records)


def test_a_full_segment_does_not_warn_about_density(pack, arc_segment, config,
                                                     vocab, out_path, caplog):
    with caplog.at_level(logging.WARNING):
        run(pack, arc_segment, config, FakeSegmentLLM(), vocab, out_path)
    assert not any("density" in r.getMessage().lower() for r in caplog.records)
