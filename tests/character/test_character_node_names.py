"""WP-11 tests for app/character/node_names.py: the mechanical node-name check.

Frozen test list (playbook §4 WP-11, items 7-9). Plan §10: a node name matches
`^[a-z0-9]+(-[a-z0-9]+){1,7}$`, starts with a verb from the allowlist
(knows|lives|wants|fears|trusts|likes|dislikes|believes|remembers|is|has|can|
cannot|owes|suspects|hopes), and has no word from a banned list.
`check(name, age=None) -> list[str]` returns the reasons it fails ([] = good).

OB-41 adaptation (.claude/prompts/ashiorid_office_build_plan.md OB-41: the pilot
cast is the 8 office characters): the good and bad examples are the plan §10
ones PLUS office ones. The office good examples are real node names from
campaigns/ashiorid_office/profiles/*.yaml. `knows-qui-llusions` (§10 "garbled")
passes every rule §10 specifies, so it is not asserted here (tracker question).
"""
import pytest

from pending import require

node_names = require("character.node_names", "app/character/node_names.py", wp="WP-11")

PLAN_GOOD = ["knows-wingardium-leviosa", "lives-in-cupboard-under-stairs",
             "wants-to-win-house-cup", "fears-uncle-vernon", "trusts-hagrid"]
OFFICE_GOOD = ["trusts-tech-lead-with-the-how", "knows-corvane-renewal-is-due",
               "fears-being-next-empty-office", "has-never-met-the-board",
               "wants-one-green-week", "cannot-read-the-code", "is-the-third-ceo"]

#: name -> a word the reason must contain (lower-cased), so each failure says why.
BAD = {
    # plan §10
    "godsley-shelter-abuse": "verb",           # not verb-first (and misspelled)
    "wingardsium-levia": "verb",               # misspelled, not verb-first
    "pass-third-year-exams": "verb",           # wrong age; "pass" is not an allowed verb
    # office (OB-41)
    "tech-lead-is-trusted": "verb",            # the verb is not first
    "Trusts-Tech-Lead": "lowercase",           # upper case
    "trusts_tech_lead": "lowercase",           # underscores are not hyphens
    "trusts--tech-lead": "lowercase",          # an empty word
    "remembers-the-last-loop": "banned",       # loop mechanics are never character knowledge
    "knows-this-is-a-simulation": "banned",
    "suspects-an-ai-wrote-the-script": "banned",
}


# T11.7
@pytest.mark.parametrize("name", PLAN_GOOD + OFFICE_GOOD)
def test_good_examples_pass(name):
    assert node_names.check(name) == []
    assert node_names.check(name, age=11) == []  # `age` is accepted (a prompt rule in v1)


# T11.8
@pytest.mark.parametrize("name,reason_word", sorted(BAD.items()))
def test_bad_examples_fail_with_a_reason_each(name, reason_word):
    reasons = node_names.check(name)
    assert reasons, f"{name!r} should fail"
    assert all(isinstance(r, str) and r.strip() for r in reasons)
    assert any(reason_word in r.lower() for r in reasons), reasons


# T11.8 (not a string / empty)
@pytest.mark.parametrize("value", ["", None, 42])
def test_non_string_or_empty_name_fails(value):
    assert node_names.check(value)


# T11.9
def test_regex_bounds_one_word_fails_eight_pass_nine_fail():
    one = "knows"
    eight = "knows-" + "-".join(["a"] * 7)
    nine = "knows-" + "-".join(["a"] * 8)
    assert len(eight.split("-")) == 8 and len(nine.split("-")) == 9
    assert node_names.check(one)
    assert node_names.check(eight) == []
    assert node_names.check(nine)
    assert node_names.check("knows-" + "b" * 40) == []  # word length is not bounded
    assert node_names.check("is-3rd-ceo") == []           # digits are allowed
