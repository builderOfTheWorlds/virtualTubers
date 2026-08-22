"""Acceptance tests for src/plan_segment.py — the Layer 2 orchestrator.

This module owns the knob interlock, resume, the recursive expand/leaf drain,
retry/degrade, the checkpoints, and writing `brief.yaml`. Everything it
validates or renders it imports from `segment_schema`; nothing pure is
reimplemented here.

Four properties are what these tests are mostly about:

  - A SEGMENT IS SIX HOURS OF AIRTIME. Losing one leaf to a stubborn model is
    survivable; losing the segment because one leaf failed is not. A node that
    exhausts its attempts is logged and skipped, and every other node is still
    written.

  - THE TREE ALWAYS TERMINATES, AND NEVER BY DROPPING A NODE. Hitting
    `max_depth`, or exhausting the retries on an expand call, forces a LEAF —
    oversized, flagged `forced: True`, planned anyway. Dropping the node
    instead would leave a hole in the airtime that nothing downstream can see.

  - RESUME IS NODE-GRANULAR. A leaf call is roughly a minute of a 70B model
    and a segment is nine or more of them. Re-running the whole segment
    because the process died on the eighth throws away eight minutes of GPU,
    and across 28 segments that is the difference between a run that finishes
    overnight and one that does not.

  - THE DRAIN IS CONTINUOUS, NOT LEVEL-BATCHED. A branch task enqueues more
    work while other tasks are still running, so the executor must accept new
    futures after the first wave.

A uniform-weight run reproduces the pre-v3 shape almost exactly — the root's
170 slots split into 9 children of ~19 slots, one expand call and nine leaf
calls — so most of these tests are the old ones re-pointed at the tree.
"""
import logging
import threading

import pytest
import yaml

import plan_segment
import segment_schema
from segment_schema import SegmentPlanError
from arc_helpers import FakeCastMember, FakePack, pack, vocab
from segment_helpers import (FakeSegmentLLM, ambient_slot, arc_segment,
                             child, children_reply, node, segment_config,
                             slots_reply, spine_slot)

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def config(segment_config):
    return segment_config


@pytest.fixture
def out_path(tmp_path):
    return tmp_path / "seg-001" / "brief.yaml"


def run(pack, arc_segment, config, llm, vocab, out_path, **kwargs):
    return plan_segment.plan_segment(pack, arc_segment, config, llm, vocab,
                                     out_path, **kwargs)


def tree_path(out_path):
    """The checkpoint lives beside the brief; it is NOT a new argument."""
    return out_path.with_name("tree.yaml")


# --------------------------------------------------------------------------
# Startup — the knob interlock
# --------------------------------------------------------------------------

def test_an_infeasible_tree_config_raises_before_any_call(pack, arc_segment,
                                                          config, vocab, out_path):
    """Operator error must fail loudly at startup, not degrade into one giant
    forced leaf per segment after burning a GPU-hour discovering it."""
    config["segment"]["tree"]["min_node_words"] = 4000
    llm = FakeSegmentLLM()

    with pytest.raises(SegmentPlanError):
        run(pack, arc_segment, config, llm, vocab, out_path)

    assert llm.expand_prompts == []
    assert llm.slot_prompts == []


# --------------------------------------------------------------------------
# The clean uniform run — reproduces the pre-v3 shape
# --------------------------------------------------------------------------

def test_a_clean_run_makes_one_expand_call_and_one_leaf_call_per_child(
        pack, arc_segment, config, vocab, out_path):
    llm = FakeSegmentLLM(children=9, slots=19)

    run(pack, arc_segment, config, llm, vocab, out_path)

    assert len(llm.expand_prompts) == 1
    assert len(llm.slot_prompts) == 9


def test_a_clean_run_returns_a_brief_with_every_slot(pack, arc_segment, config,
                                                     vocab, out_path):
    brief = run(pack, arc_segment, config, llm_default(), vocab, out_path)
    assert len(brief["slots"]) == 9 * 19


def llm_default():
    return FakeSegmentLLM(children=9, slots=19)


def test_the_brief_is_written_to_disk(pack, arc_segment, config, vocab, out_path):
    run(pack, arc_segment, config, llm_default(), vocab, out_path)

    assert out_path.exists()
    written = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert written["segment_id"] == "seg-001"
    assert len(written["slots"]) == 9 * 19


def test_the_output_directory_is_created(pack, arc_segment, config, vocab, tmp_path):
    deep = tmp_path / "a" / "b" / "seg-001" / "brief.yaml"
    run(pack, arc_segment, config, llm_default(), vocab, deep)
    assert deep.exists()


def test_the_written_brief_stays_human_readable(pack, arc_segment, config, vocab,
                                                out_path):
    """Stage 2 of the workflow is a human reading this file."""
    run(pack, arc_segment, config, llm_default(), vocab, out_path)
    text = out_path.read_text(encoding="utf-8")
    assert "!!python" not in text
    assert "segment_id:" in text


def test_the_tree_checkpoint_is_written_beside_the_brief(pack, arc_segment, config,
                                                         vocab, out_path):
    run(pack, arc_segment, config, llm_default(), vocab, out_path)

    checkpoint = tree_path(out_path)
    assert checkpoint.exists()
    tree = yaml.safe_load(checkpoint.read_text(encoding="utf-8"))
    assert "seg-001" in tree
    assert tree["seg-001"]["kind"] == "branch"
    assert len(tree["seg-001"]["children"]) == 9


def test_the_root_is_seeded_from_the_arc_segment(pack, arc_segment, config, vocab,
                                                 out_path):
    run(pack, arc_segment, config, llm_default(), vocab, out_path)

    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    root = tree["seg-001"]
    assert root["depth"] == 0
    assert root["parent_id"] is None
    assert root["target_words"] == config["segment"]["target_words"]


# --------------------------------------------------------------------------
# Node ids and slot ids
# --------------------------------------------------------------------------

def test_child_node_ids_use_the_dashed_scheme(pack, arc_segment, config, vocab,
                                              out_path):
    """`{parent}-n{order}`, never dotted — worklist.take_path uses the id as a
    literal directory name and a dot reads as a file extension to everything
    that later globs that tree."""
    run(pack, arc_segment, config, llm_default(), vocab, out_path)

    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    assert "seg-001-n0" in tree
    assert all("." not in nid for nid in tree)


def test_slot_ids_are_unique_across_the_whole_segment(pack, arc_segment, config,
                                                      vocab, out_path):
    brief = run(pack, arc_segment, config, llm_default(), vocab, out_path)
    ids = [s["slot_id"] for s in brief["slots"]]
    assert len(ids) == len(set(ids))


def test_a_slot_id_says_which_node_it_came_from(pack, arc_segment, config, vocab,
                                                out_path):
    brief = run(pack, arc_segment, config, llm_default(), vocab, out_path)
    first = brief["slots"][0]
    assert first["slot_id"].startswith(first["node_id"])


def test_slot_ids_are_stable_across_a_rerun(pack, arc_segment, config, vocab,
                                            tmp_path):
    first = run(pack, arc_segment, config, llm_default(), vocab,
                tmp_path / "a" / "brief.yaml")
    second = run(pack, arc_segment, config, llm_default(), vocab,
                 tmp_path / "b" / "brief.yaml")
    assert [s["slot_id"] for s in first["slots"]] == \
           [s["slot_id"] for s in second["slots"]]


# --------------------------------------------------------------------------
# Recursion — depth, forced leaves, termination
# --------------------------------------------------------------------------

def lopsided_llm(slots=19):
    """Two children, 10:1. The heavy child is still far above max_leaf_slots
    and must recurse again; the light one is leaf-eligible immediately."""
    return FakeSegmentLLM(children=2, slots=slots, weights=[10.0, 1.0])


def test_an_oversized_child_recurses_again(pack, arc_segment, config, vocab,
                                           out_path):
    run(pack, arc_segment, config, lopsided_llm(), vocab, out_path)

    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    assert "seg-001-n0-n0" in tree, "the heavy child should have expanded"
    assert max(n["depth"] for n in tree.values()) >= 2


def test_a_leaf_eligible_child_is_not_expanded(pack, arc_segment, config, vocab,
                                               out_path):
    run(pack, arc_segment, config, lopsided_llm(), vocab, out_path)

    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    light = tree["seg-001-n1"]
    assert light["kind"] == "leaf"
    assert light["children"] == []


def test_max_depth_forces_a_leaf_rather_than_recursing_forever(pack, arc_segment,
                                                               config, vocab,
                                                               out_path):
    config["segment"]["tree"]["max_depth"] = 1
    run(pack, arc_segment, config, lopsided_llm(), vocab, out_path)

    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    assert max(n["depth"] for n in tree.values()) <= 1
    forced = [n for n in tree.values() if n.get("forced")]
    assert forced, "an oversized node at max_depth must be a forced leaf"
    assert all(n["kind"] == "leaf" for n in forced)


def test_a_forced_leaf_is_logged_at_warning(pack, arc_segment, config, vocab,
                                            out_path, caplog):
    config["segment"]["tree"]["max_depth"] = 1
    with caplog.at_level(logging.WARNING):
        run(pack, arc_segment, config, lopsided_llm(), vocab, out_path)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_a_forced_leaf_still_gets_its_slots_planned(pack, arc_segment, config,
                                                    vocab, out_path):
    """Degrade, do not drop. A forced leaf is oversized for one call, but it
    produces content rather than a hole in the airtime."""
    config["segment"]["tree"]["max_depth"] = 1
    brief = run(pack, arc_segment, config, lopsided_llm(), vocab, out_path)

    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    forced_ids = [nid for nid, n in tree.items() if n.get("forced")]
    planned = {s["node_id"] for s in brief["slots"]}
    assert set(forced_ids) & planned


def test_an_expand_that_never_validates_degrades_to_a_forced_leaf(
        pack, arc_segment, config, vocab, out_path):
    """Retry, then degrade. The node still gets a leaf call for its full
    target_slots."""
    junk = "not yaml at all, sorry"
    # The forced leaf covers the whole segment, so it must return the whole
    # segment's slots — a 19-slot reply would fail its own density check and
    # the node would be skipped rather than degraded.
    llm = FakeSegmentLLM(children=9, slots=170,
                         chapter_replies=[junk, junk, junk, junk, junk, junk])

    brief = run(pack, arc_segment, config, llm, vocab, out_path)

    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    assert tree["seg-001"]["kind"] == "leaf"
    assert tree["seg-001"]["forced"] is True
    assert brief["slots"], "a degraded root must still be planned"


# --------------------------------------------------------------------------
# Retry and skip
# --------------------------------------------------------------------------

def test_an_unparseable_expand_reply_is_retried(pack, arc_segment, config, vocab,
                                                out_path):
    llm = FakeSegmentLLM(children=9, slots=19, chapter_replies=["nonsense"])
    run(pack, arc_segment, config, llm, vocab, out_path)
    assert len(llm.expand_prompts) == 2


def test_an_invalid_expand_batch_is_retried_with_the_problems(pack, arc_segment,
                                                              config, vocab,
                                                              out_path):
    bad = yaml.safe_dump({"children": [child(0, weight=-1), child(1)]},
                         sort_keys=False)
    llm = FakeSegmentLLM(children=9, slots=19, chapter_replies=[bad])

    run(pack, arc_segment, config, llm, vocab, out_path)

    assert len(llm.expand_prompts) == 2
    assert "weight" in llm.expand_prompts[1]


def test_an_invalid_leaf_batch_is_retried(pack, arc_segment, config, vocab,
                                          out_path):
    bad = yaml.safe_dump({"slots": [ambient_slot(1, kind="nonsense")]},
                         sort_keys=False)
    llm = FakeSegmentLLM(children=9, slots=19, slot_replies=[bad])

    run(pack, arc_segment, config, llm, vocab, out_path)

    assert len(llm.slot_prompts) == 10       # nine leaves, one retried


def test_attempts_are_bounded_by_the_config(pack, arc_segment, config, vocab,
                                            out_path):
    config["segment"]["max_attempts"] = 2
    llm = FakeSegmentLLM(children=9, slots=19,
                         chapter_replies=["junk", "junk", "junk", "junk"])

    run(pack, arc_segment, config, llm, vocab, out_path)

    assert len(llm.expand_prompts) == 2


def test_a_leaf_that_never_validates_is_skipped_not_fatal(pack, arc_segment,
                                                          config, vocab, out_path):
    """The only remaining skip path, and it costs at most one leaf."""
    bad = yaml.safe_dump({"slots": [ambient_slot(1, kind="nonsense")]},
                         sort_keys=False)
    llm = FakeSegmentLLM(children=9, slots=19, slot_replies=[bad, bad, bad, bad])

    brief = run(pack, arc_segment, config, llm, vocab, out_path)

    # Four bad replies at max_attempts=2 exhausts exactly two leaves; the other
    # seven are untouched. Losing 2 of 9 is the cost of the skip path.
    assert brief is not None
    assert len(brief["slots"]) == 7 * 19


def test_a_failed_leaf_is_logged_at_error(pack, arc_segment, config, vocab,
                                          out_path, caplog):
    bad = yaml.safe_dump({"slots": [ambient_slot(1, kind="nonsense")]},
                         sort_keys=False)
    llm = FakeSegmentLLM(children=9, slots=19, slot_replies=[bad, bad, bad, bad])

    with caplog.at_level(logging.ERROR):
        run(pack, arc_segment, config, llm, vocab, out_path)

    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_an_exception_from_the_model_is_survivable(pack, arc_segment, config,
                                                   vocab, out_path):
    class Flaky:
        def __init__(self):
            self.calls = 0
            self.inner = FakeSegmentLLM(children=9, slots=19)
            self._lock = threading.Lock()

        def complete(self, system_prompt, messages):
            with self._lock:
                self.calls += 1
                boom = self.calls == 3
            if boom:
                raise RuntimeError("ollama fell over")
            return self.inner.complete(system_prompt, messages)

    brief = run(pack, arc_segment, config, Flaky(), vocab, out_path)
    assert brief is not None


# --------------------------------------------------------------------------
# Resume
# --------------------------------------------------------------------------

def test_a_finished_segment_makes_no_calls_at_all(pack, arc_segment, config,
                                                  vocab, out_path):
    run(pack, arc_segment, config, llm_default(), vocab, out_path)

    second = FakeSegmentLLM(children=9, slots=19)
    run(pack, arc_segment, config, second, vocab, out_path)

    assert second.expand_prompts == []
    assert second.slot_prompts == []


def test_resume_replans_only_the_unresolved_nodes(pack, arc_segment, config,
                                                  vocab, out_path):
    run(pack, arc_segment, config, llm_default(), vocab, out_path)

    # Blank one leaf's slots, as a crash mid-node would leave it.
    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    tree["seg-001-n3"]["slots"] = None
    tree["seg-001-n3"]["kind"] = None
    tree_path(out_path).write_text(yaml.safe_dump(tree), encoding="utf-8")

    second = FakeSegmentLLM(children=9, slots=19)
    run(pack, arc_segment, config, second, vocab, out_path)

    assert second.expand_prompts == [], "the tree shape was already resolved"
    assert len(second.slot_prompts) == 1


def test_resume_keeps_the_slots_already_on_disk(pack, arc_segment, config, vocab,
                                                out_path):
    first = run(pack, arc_segment, config, llm_default(), vocab, out_path)
    kept = first["slots"][0]["slot_id"]

    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    tree["seg-001-n8"]["slots"] = None
    tree["seg-001-n8"]["kind"] = None
    tree_path(out_path).write_text(yaml.safe_dump(tree), encoding="utf-8")

    second = run(pack, arc_segment, config, FakeSegmentLLM(children=9, slots=19),
                 vocab, out_path)

    assert kept in [s["slot_id"] for s in second["slots"]]


def test_a_tree_checkpoint_that_will_not_parse_is_fatal(pack, arc_segment, config,
                                                        vocab, out_path):
    """Silently replanning over a corrupt checkpoint would throw away
    everything it contained."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree_path(out_path).write_text("{[not yaml", encoding="utf-8")

    with pytest.raises(SegmentPlanError):
        run(pack, arc_segment, config, llm_default(), vocab, out_path)


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------

def test_leaves_are_planned_concurrently(pack, arc_segment, config, vocab,
                                         out_path):
    config["segment"]["concurrency"] = 4
    seen = set()
    barrier = threading.Barrier(4, timeout=10)
    inner = FakeSegmentLLM(children=9, slots=19)

    class Concurrent:
        def complete(self, system_prompt, messages):
            if system_prompt == segment_schema.SYSTEM_PROMPT_LEAF:
                seen.add(threading.current_thread().name)
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
            return inner.complete(system_prompt, messages)

    run(pack, arc_segment, config, Concurrent(), vocab, out_path)

    assert len(seen) >= 2, "leaf calls did not overlap"


def test_the_drain_admits_work_discovered_after_the_first_wave(pack, arc_segment,
                                                               config, vocab,
                                                               out_path):
    """A branch task enqueues children while other tasks are still running.
    A single `with executor:` block over one initial list would never see
    them."""
    run(pack, arc_segment, config, lopsided_llm(), vocab, out_path)

    tree = yaml.safe_load(tree_path(out_path).read_text(encoding="utf-8"))
    assert any(n["depth"] >= 2 for n in tree.values())


# --------------------------------------------------------------------------
# Segment-level final checks
# --------------------------------------------------------------------------

def test_the_sensitivity_budget_is_checked_on_the_whole_segment(pack, arc_segment,
                                                                config, vocab,
                                                                out_path, caplog):
    """Per-leaf was equivalent to per-chapter before; under a tree a six-slot
    leaf with two flag-sensitive slots reads as 33% and warns on noise. The
    budget was written about the segment."""
    flagged = yaml.safe_dump(
        {"slots": [ambient_slot(i, sensitivity="flags",
                                depends_on=["helen-wounded"])
                   for i in range(1, 20)]}, sort_keys=False)
    llm = FakeSegmentLLM(children=9, slots=19, slot_replies=[flagged] * 9)

    with caplog.at_level(logging.WARNING):
        run(pack, arc_segment, config, llm, vocab, out_path)

    assert any("sensitiv" in r.message.lower() for r in caplog.records)


def test_a_thin_segment_warns_about_density(pack, arc_segment, config, vocab,
                                            out_path, caplog):
    # 16 slots clears the per-leaf floor (19 * 0.80 = 15.2) so every leaf is
    # valid, but 9 * 16 = 144 falls under a segment floor of 0.95 * 170 = 161.5.
    config["segment"]["density_floor"] = 0.95
    llm = FakeSegmentLLM(children=9, slots=16)

    with caplog.at_level(logging.WARNING):
        run(pack, arc_segment, config, llm, vocab, out_path)

    assert any("densit" in r.message.lower() for r in caplog.records)


def test_a_thin_segment_is_marked_for_rebrief(pack, arc_segment, config, vocab,
                                              out_path):
    """The API's `rebrief: true` selects on this flag, so it has to reach
    disk rather than only the log."""
    config["segment"]["density_floor"] = 0.95
    llm = FakeSegmentLLM(children=9, slots=16)

    brief = run(pack, arc_segment, config, llm, vocab, out_path)

    assert brief.get("needs_rebrief") is True
    written = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert written.get("needs_rebrief") is True


def test_a_healthy_segment_is_not_marked_for_rebrief(pack, arc_segment, config,
                                                     vocab, out_path):
    brief = run(pack, arc_segment, config, llm_default(), vocab, out_path)
    assert not brief.get("needs_rebrief")


def test_the_tree_shape_is_logged_at_info(pack, arc_segment, config, vocab,
                                          out_path, caplog):
    with caplog.at_level(logging.INFO):
        run(pack, arc_segment, config, llm_default(), vocab, out_path)
    assert any("leaves" in r.message or "leaf" in r.message.lower()
               for r in caplog.records)


# --------------------------------------------------------------------------
# v3 additions — progress and cancellation
# --------------------------------------------------------------------------

def test_progress_is_reported_for_every_completed_node(pack, arc_segment, config,
                                                       vocab, out_path):
    seen = []
    lock = threading.Lock()

    def progress(done, total, node_record):
        with lock:
            seen.append((done, total, node_record["node_id"]))

    run(pack, arc_segment, config, llm_default(), vocab, out_path,
        progress=progress)

    assert seen
    assert [d for d, _, _ in seen] == sorted(d for d, _, _ in seen)
    assert all(isinstance(t, int) for _, t, _ in seen)


def test_progress_default_none_keeps_the_old_signature_working(pack, arc_segment,
                                                               config, vocab,
                                                               out_path):
    assert run(pack, arc_segment, config, llm_default(), vocab, out_path) is not None


def test_cancel_check_stops_the_drain_early(pack, arc_segment, config, vocab,
                                            out_path):
    """A segment plan is GPU-minutes. An operator who cancels must not wait
    for all nine leaves."""
    llm = FakeSegmentLLM(children=9, slots=19)
    calls = []

    def cancel_after_a_while():
        calls.append(1)
        return len(calls) > 3

    brief = run(pack, arc_segment, config, llm, vocab, out_path,
                cancel_check=cancel_after_a_while)

    assert len(llm.slot_prompts) < 9
    assert brief is not None, "a cancel returns the partial tree, it does not raise"


def test_cancel_check_returning_false_completes_normally(pack, arc_segment, config,
                                                         vocab, out_path):
    llm = FakeSegmentLLM(children=9, slots=19)
    brief = run(pack, arc_segment, config, llm, vocab, out_path,
                cancel_check=lambda: False)
    assert len(brief["slots"]) == 9 * 19


def test_a_cancelled_plan_still_checkpoints_what_it_finished(pack, arc_segment,
                                                             config, vocab,
                                                             out_path):
    run(pack, arc_segment, config, FakeSegmentLLM(children=9, slots=19), vocab,
        out_path, cancel_check=lambda: True)
    assert tree_path(out_path).exists()
