"""Tests for app/table/contract_filter.py (near-duplicate goals and redundant leaves).

Plan: .claude/prompts/ashiorid_office_loop_fix_plan.md P2.
"""
import pytest

from pending import require

cf = require("table.contract_filter", "app/table/contract_filter.py", wp="office-loop P2")


def _p(scene_id, goal, seg="seg", leaf="leaf"):
    return {"scene_id": scene_id, "segment_id": seg, "leaf_id": leaf, "canon_goal": goal}


# ---------------------------------------------------------------- words / jaccard
def test_goal_words_lowercases_and_drops_stopwords():
    w = cf.goal_words("The Office Manager arrives, and the lights GO on.")
    assert "office" in w and "manager" in w and "arrives" in w and "lights" in w
    assert "the" not in w and "and" not in w
    assert isinstance(w, frozenset)


def test_jaccard_basic_and_empty():
    assert cf.jaccard(frozenset("ab"), frozenset("ab")) == 1.0
    assert cf.jaccard(frozenset("ab"), frozenset("cd")) == 0.0
    assert cf.jaccard(frozenset(), frozenset("ab")) == 0.0
    assert cf.jaccard(frozenset("abc"), frozenset("abd")) == pytest.approx(0.5)


def test_similarity_of_reworded_arrival_is_high():
    a = "The Party Member looks up from their writing as the Office Manager enters."
    b = "The Party Member looks up from their writing as the Office Manager arrives."
    assert cf.similarity(a, b) >= 0.6
    assert cf.similarity(a, "Marketing drafts the release notes.") < 0.3


# ---------------------------------------------------------------- dedupe_near
def test_dedupe_near_drops_near_duplicate_and_reports():
    payloads = [
        _p("a", "The team gathers in the conference room for the daily standup."),
        _p("b", "The Analyst outlines the functional requirements."),
        _p("c", "The team gathers in the conference room for the standup."),
    ]
    kept, dropped = cf.dedupe_near(payloads, threshold=0.6, window=8)
    assert [p["scene_id"] for p in kept] == ["a", "b"]
    assert len(dropped) == 1
    d_id, k_id, score = dropped[0]
    assert d_id == "c" and k_id == "a" and score >= 0.6


def test_dedupe_near_window_allows_far_repeat():
    goal = "The team takes a break for lunch in the kitchen."
    payloads = [_p("a", goal)] + [_p(f"x{i}", f"unique topic number {i} zebra{i} quartz{i}")
                                  for i in range(5)] + [_p("b", goal)]
    kept, dropped = cf.dedupe_near(payloads, threshold=0.6, window=3)
    assert "b" in [p["scene_id"] for p in kept] and dropped == []


def test_dedupe_near_empty_goal_never_dropped_and_input_not_mutated():
    payloads = [_p("a", ""), _p("b", ""), _p("c", "Same words here now."), _p("d", "Same words here now.")]
    snapshot = [dict(p) for p in payloads]
    kept, dropped = cf.dedupe_near(payloads)
    assert [p["scene_id"] for p in kept] == ["a", "b", "c"]
    assert payloads == snapshot


def test_dedupe_near_empty_input():
    assert cf.dedupe_near([]) == ([], [])


# ---------------------------------------------------------------- collapse_leaves
def _office_like():
    return [
        _p("n0-1", "The Party Member finishes writing as the Office Manager arrives.", leaf="n0"),
        _p("n0-2", "The Office Manager tidies the office and checks schedules.", leaf="n0"),
        _p("n1-1", "The Party Member looks up from writing as the Office Manager arrives.", leaf="n1"),
        _p("n1-2", "The Office Manager tidies up the office, checking the schedules.", leaf="n1"),
        _p("n1-3", "A courier drops off a mysterious parcel.", leaf="n1"),
        _p("o-1", "The Office Manager arrives early.", seg="other", leaf="o0"),
    ]


def test_collapse_leaves_drops_mostly_repeated_leaf():
    kept, dropped = cf.collapse_leaves(_office_like(), threshold=0.5, min_share=0.5)
    ids = [p["scene_id"] for p in kept]
    assert ids == ["n0-1", "n0-2", "o-1"]          # other segment is never compared
    assert len(dropped) == 1
    leaf, against, share = dropped[0]
    assert leaf == "n1" and against == "n0" and share == pytest.approx(2 / 3)


def test_collapse_leaves_keeps_leaf_below_share():
    kept, dropped = cf.collapse_leaves(_office_like(), threshold=0.5, min_share=0.9)
    assert len(kept) == 6 and dropped == []


def test_collapse_leaves_first_leaf_of_segment_always_kept():
    payloads = [_p("a", "Same goal words here.", leaf="l0"),
                _p("b", "Same goal words here.", leaf="l0")]
    kept, dropped = cf.collapse_leaves(payloads)
    assert len(kept) == 2 and dropped == []


def test_collapse_leaves_compares_against_all_kept_leaves_in_segment():
    payloads = [
        _p("a", "Engineer and Tester pass code back and forth.", leaf="l0"),
        _p("b", "Marketing crafts the product pitch.", leaf="l1"),
        _p("c", "Engineer and Tester pass code back and forth for fixes.", leaf="l2"),
        _p("d", "Marketing crafts the product pitch and messaging.", leaf="l2"),
    ]
    kept, dropped = cf.collapse_leaves(payloads, threshold=0.5, min_share=0.5)
    assert [p["scene_id"] for p in kept] == ["a", "b"]
    assert dropped[0][0] == "l2" and dropped[0][1] in ("l0", "l1")


def test_collapse_leaves_empty():
    assert cf.collapse_leaves([]) == ([], [])
