"""Acceptance tests for src/segment_schema.py — the PURE half of Layer 2.

Layer 2 turns one arc segment into a brief: a tree of nodes, whose leaves hold
the slots Layer 3 will voice. Everything here is a function of its arguments —
no LLM, no network, no disk.

v3 replaces the flat nine-chapters-per-segment split with a RECURSIVE tree.
The reason is airtime: a segment is six hours, and how much happens in those
six hours is wildly uneven. A flat split gives a quiet stretch of road the
same budget as the arrival at the moonwell. A tree lets the model say "this
slice carries three times the weight of that one" and recurse only where the
density actually demands it.

Two properties matter more than any individual function here:

  * WORDS RECONCILE EXACTLY. Children's `target_words` must sum to the
    parent's, to the word. Slots are allowed to drift by rounding; words are
    not. Words are the budget that ties back to the 168-hour target, and a
    tree that leaks 3% per level loses a fifth of the week by depth four.

  * THE TREE ALWAYS TERMINATES. `max_depth` and expand-exhaustion both force
    a leaf rather than dropping the node. A forced leaf is oversized and
    flagged; a dropped node is a silent hole in the airtime.
"""
import math

import pytest
import yaml

import segment_schema
from segment_schema import SegmentPlanError
from segment_helpers import (segment_config, arc_segment, node, child,
                             children_reply, ambient_slot, spine_slot,
                             slots_reply)
from arc_helpers import FakeCastMember, FakePack, pack, vocab


WORDS_PER_SLOT = 105 * 3          # words_per_take * takes_per_slot


# ---------------------------------------------------------------------------
# derive_target_slots — the words -> slots conversion used at every level
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("words,expected", [
    (53600, round(53600 / WORDS_PER_SLOT)),   # the segment root: 170
    (5985, 19),                                # exactly max_leaf_slots
    (315, 1),
    (0, 0),
])
def test_derive_target_slots_converts_words_to_slots(segment_config, words, expected):
    assert segment_schema.derive_target_slots(words, segment_config) == expected


def test_derive_target_slots_matches_the_configured_root_total(segment_config):
    """The root's derived slot count must agree with the `target_slots` that
    generation.yaml states independently, or the two drift silently."""
    derived = segment_schema.derive_target_slots(
        segment_config["segment"]["target_words"], segment_config)
    assert derived == segment_config["segment"]["target_slots"]


# ---------------------------------------------------------------------------
# leaf_eligible — the recursion trigger
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slots,expected", [
    (1, True), (18, True), (19, True),    # <= max_leaf_slots
    (20, False), (170, False),
])
def test_leaf_eligible_at_the_max_leaf_slots_boundary(segment_config, slots, expected):
    assert segment_schema.leaf_eligible(slots, segment_config) is expected


# ---------------------------------------------------------------------------
# validate_tree_config — the knob interlock
#
# These four knobs can be set mutually infeasible, and the failure mode is
# silent: every expand fails validation, retries, degrades, and the run
# produces one giant forced leaf per segment. That is operator error, so it
# RAISES rather than returning problems.
# ---------------------------------------------------------------------------

def test_validate_tree_config_accepts_the_shipped_defaults(segment_config):
    assert segment_schema.validate_tree_config(segment_config) == []


def test_min_node_words_above_half_a_leaf_is_infeasible(segment_config):
    """A branch must be splittable into at least two floor-legal children.
    max_leaf_slots(19) * 315 = 5985 words, so a floor above 2992 makes every
    split illegal."""
    segment_config["segment"]["tree"]["min_node_words"] = 4000
    problems = segment_schema.validate_tree_config(segment_config)
    assert problems
    assert any("min_node_words" in p for p in problems)


@pytest.mark.parametrize("knob,value", [
    ("max_children", 1),
    ("max_children", 0),
    ("max_depth", 0),
    ("max_leaf_slots", 0),
    ("leaf_density_floor", 0),
    ("leaf_density_floor", 1.5),
])
def test_validate_tree_config_rejects_out_of_range_knobs(segment_config, knob, value):
    segment_config["segment"]["tree"][knob] = value
    problems = segment_schema.validate_tree_config(segment_config)
    assert problems, f"{knob}={value} should be rejected"
    assert any(knob in p for p in problems)


def test_leaf_density_floor_of_exactly_one_is_allowed(segment_config):
    segment_config["segment"]["tree"]["leaf_density_floor"] = 1.0
    assert segment_schema.validate_tree_config(segment_config) == []


# ---------------------------------------------------------------------------
# distribute_words — weights into exact word budgets
# ---------------------------------------------------------------------------

def test_uniform_weights_split_the_parent_evenly(segment_config):
    parent = node(target_words=9000, target_slots=29)
    children = [child(i, weight=1.0) for i in range(3)]

    out = segment_schema.distribute_words(children, parent, segment_config)

    assert [c["target_words"] for c in out] == [3000, 3000, 3000]


def test_weights_scale_the_share(segment_config):
    parent = node(target_words=12000, target_slots=38)
    children = [child(0, weight=3.0), child(1, weight=1.0)]

    out = segment_schema.distribute_words(children, parent, segment_config)

    assert [c["target_words"] for c in out] == [9000, 3000]


def test_weights_need_not_sum_to_one(segment_config):
    """The model is asked for relative weight, not a normalized fraction.
    Insisting on normalization would be a retry for something arithmetic can
    fix locally."""
    parent = node(target_words=8000, target_slots=25)
    children = [child(0, weight=30), child(1, weight=10)]

    out = segment_schema.distribute_words(children, parent, segment_config)

    assert [c["target_words"] for c in out] == [6000, 2000]


@pytest.mark.parametrize("total,weights", [
    (53600, [1.0, 1.0, 1.0]),          # 53600/3 does not divide evenly
    (10000, [1.0, 1.0, 1.0]),
    (7, [1.0, 1.0]),
    (53600, [2.7, 1.3, 5.5, 0.9]),
    (100000, [1] * 11),
])
def test_distributed_words_always_sum_to_the_parent_exactly(segment_config, total, weights):
    """THE central invariant. Rounding remainder is reconciled onto the
    largest child so the sum is exact — words are the budget that ties back to
    the 168-hour target, and a per-level leak compounds with depth."""
    parent = node(target_words=total, target_slots=1)
    children = [child(i, weight=w) for i, w in enumerate(weights)]

    out = segment_schema.distribute_words(children, parent, segment_config)

    assert sum(c["target_words"] for c in out) == total


def test_rounding_remainder_lands_on_the_largest_child(segment_config):
    parent = node(target_words=10000, target_slots=1)
    children = [child(0, weight=1.0), child(1, weight=1.0), child(2, weight=1.0)]

    out = segment_schema.distribute_words(children, parent, segment_config)

    assert sum(c["target_words"] for c in out) == 10000
    assert max(c["target_words"] for c in out) == 3334


def test_distribute_words_also_sets_derived_target_slots(segment_config):
    parent = node(target_words=12600, target_slots=40)
    children = [child(0, weight=1.0), child(1, weight=1.0)]

    out = segment_schema.distribute_words(children, parent, segment_config)

    for entry in out:
        assert entry["target_slots"] == segment_schema.derive_target_slots(
            entry["target_words"], segment_config)


def test_distribute_words_does_not_mutate_its_input(segment_config):
    parent = node(target_words=6000, target_slots=19)
    children = [child(0, weight=1.0), child(1, weight=1.0)]

    segment_schema.distribute_words(children, parent, segment_config)

    assert "target_words" not in children[0]


# ---------------------------------------------------------------------------
# parse_children
# ---------------------------------------------------------------------------

def test_parse_children_reads_the_children_key():
    parsed = segment_schema.parse_children(children_reply(4))
    assert len(parsed) == 4
    assert parsed[0]["order"] == 0


def test_parse_children_tolerates_a_yaml_fence():
    fenced = "Sure!\n```yaml\n" + children_reply(2) + "```\n"
    assert len(segment_schema.parse_children(fenced)) == 2


def test_parse_children_accepts_a_bare_list():
    bare = yaml.safe_dump([child(0), child(1)], sort_keys=False)
    assert len(segment_schema.parse_children(bare)) == 2


def test_parse_children_raises_on_unparseable_reply():
    with pytest.raises(SegmentPlanError):
        segment_schema.parse_children("I'm afraid I can't do that.")


# ---------------------------------------------------------------------------
# validate_children
# ---------------------------------------------------------------------------

def test_valid_children_produce_no_problems(segment_config):
    parent = node(target_words=53600, target_slots=170)
    children = [child(i) for i in range(9)]
    assert segment_schema.validate_children(children, parent, segment_config) == []


@pytest.mark.parametrize("missing", ["order", "title", "summary",
                                     "continuity_in", "continuity_out", "weight"])
def test_a_child_missing_a_required_key_is_a_problem(segment_config, missing):
    parent = node(target_words=53600, target_slots=170)
    children = [child(0), child(1)]
    del children[0][missing]

    problems = segment_schema.validate_children(children, parent, segment_config)

    assert problems
    assert any(missing in p for p in problems)


def test_required_key_check_returns_early_and_does_not_crash(segment_config):
    """Later checks index into the child. Running them on one missing `weight`
    must report a problem, not die with a KeyError — that would turn a
    recoverable bad batch into a crashed run."""
    parent = node(target_words=53600, target_slots=170)
    children = [{"order": 0}]

    problems = segment_schema.validate_children(children, parent, segment_config)

    assert problems


@pytest.mark.parametrize("orders", [[0, 1, 3], [1, 2, 3], [0, 0, 1], [0, 2, 1]])
def test_orders_must_be_contiguous_from_zero(segment_config, orders):
    parent = node(target_words=53600, target_slots=170)
    children = [child(o) for o in orders]
    # `child(o)` sets order=o; a duplicate/gap must be caught.
    problems = segment_schema.validate_children(children, parent, segment_config)
    if orders == [0, 2, 1]:
        assert problems == [], "out-of-sequence but complete 0..N-1 is fine"
    else:
        assert problems


def test_an_empty_child_list_is_a_problem(segment_config):
    parent = node(target_words=53600, target_slots=170)
    assert segment_schema.validate_children([], parent, segment_config)


def test_more_children_than_max_children_is_a_problem(segment_config):
    parent = node(target_words=200000, target_slots=635)
    children = [child(i) for i in range(13)]      # max_children is 12

    problems = segment_schema.validate_children(children, parent, segment_config)

    assert problems
    assert any("max_children" in p or "12" in p for p in problems)


@pytest.mark.parametrize("weight", [0, -1, "heavy", None])
def test_weight_must_be_a_positive_number(segment_config, weight):
    parent = node(target_words=53600, target_slots=170)
    children = [child(0, weight=weight), child(1, weight=1.0)]

    problems = segment_schema.validate_children(children, parent, segment_config)

    assert problems
    assert any("weight" in p for p in problems)


@pytest.mark.parametrize("field", ["summary", "continuity_in", "continuity_out"])
def test_empty_prose_fields_are_problems(segment_config, field):
    parent = node(target_words=53600, target_slots=170)
    children = [child(0, **{field: "   "}), child(1)]

    problems = segment_schema.validate_children(children, parent, segment_config)

    assert problems
    assert any(field in p for p in problems)


def test_a_child_below_min_node_words_is_rejected(segment_config):
    """The floor is checked on the DISTRIBUTED share, not asked of the model.
    A lopsided weight that starves one child below 2000 words fails the whole
    expand call and is retried."""
    parent = node(target_words=20000, target_slots=63)
    children = [child(0, weight=100.0), child(1, weight=1.0)]   # child 1 gets ~198

    problems = segment_schema.validate_children(children, parent, segment_config)

    assert problems
    assert any("min_node_words" in p or "2000" in p for p in problems)


def test_problem_strings_name_the_offending_child(segment_config):
    """During an unattended run these strings are the entire diagnosis, and
    they are fed back into the retry prompt."""
    parent = node(target_words=53600, target_slots=170)
    children = [child(0), child(1, weight=-5)]

    problems = segment_schema.validate_children(children, parent, segment_config)

    assert any("1" in p for p in problems)


# ---------------------------------------------------------------------------
# validate_slots — unchanged, except the new optional node density check
# ---------------------------------------------------------------------------

def test_validate_slots_without_a_node_does_not_check_count(pack, vocab, config):
    """Backwards compatible: today's callers pass no node and get no count
    check."""
    segment_config = config
    slots = [ambient_slot(i) for i in range(1, 4)]
    assert segment_schema.validate_slots(slots, pack, vocab, segment_config) == []


def test_a_leaf_under_the_density_floor_is_a_problem(pack, vocab, config):
    """Under recursion there are more leaves and more chances to
    under-deliver, and the segment-level check only fires once at the very
    end. Catching it per-leaf turns a silent shortfall into a retry."""
    segment_config = config
    leaf = node(target_slots=19)
    slots = [ambient_slot(i) for i in range(1, 6)]      # 5 << 19 * 0.80

    problems = segment_schema.validate_slots(slots, pack, vocab, segment_config,
                                             node=leaf)

    assert problems
    assert any("density" in p.lower() or "19" in p for p in problems)


def test_a_leaf_at_the_density_floor_passes(pack, vocab, config):
    segment_config = config
    leaf = node(target_slots=10)
    slots = [ambient_slot(i) for i in range(1, 9)]      # 8 == 10 * 0.80

    assert segment_schema.validate_slots(slots, pack, vocab, segment_config,
                                         node=leaf) == []


@pytest.fixture
def config(segment_config):
    """`vocab` (from arc_helpers) is built from a fixture named `config`."""
    return segment_config


# ===========================================================================
# UNCHANGED BEHAVIOUR — preserved verbatim from the pre-v3 suite.
#
# The tree rewrite touches chapters/nodes and the brief merge. Slot parsing,
# slot validation and the sensitivity budget are untouched by it, so their
# tests stay exactly as they were: they are the oracle proving the rewrite
# did not disturb the parts it had no business disturbing.
# ===========================================================================

# --------------------------------------------------------------------------
# 2b — parsing and validating slots
# --------------------------------------------------------------------------

def test_slots_parse_from_plain_yaml():
    slots = segment_schema.parse_slots(slots_reply(19))
    assert len(slots) == 19


def test_slots_parse_from_a_fenced_block():
    assert len(segment_schema.parse_slots("```\n" + slots_reply(3) + "```")) == 3


def test_a_good_ambient_slot_validates(pack, vocab, config):
    problems = segment_schema.validate_slots([ambient_slot(1)], pack, vocab, config)
    assert problems == []


def test_a_good_spine_slot_validates(pack, vocab, config):
    problems = segment_schema.validate_slots([spine_slot(1)], pack, vocab, config)
    assert problems == []


def test_an_unknown_slot_kind_is_rejected(pack, vocab, config):
    """`kind` decides whether Layer 3 generates dialogue for the slot at all.
    An unrecognised value is silently skipped by both branches, so the slot
    becomes a hole in the airtime that nothing reports."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, kind="interlude")], pack, vocab, config)
    assert problems and any("interlude" in p for p in problems)


@pytest.mark.parametrize("missing", [
    "slot_id", "kind", "prompt", "lore", "participants", "sensitivity",
    "depends_on",
])
def test_every_ambient_slot_key_is_required(pack, vocab, config, missing):
    slot = {k: v for k, v in ambient_slot(1).items() if k != missing}
    problems = segment_schema.validate_slots([slot], pack, vocab, config)
    assert problems and any(missing in p for p in problems)


@pytest.mark.parametrize("missing", ["slot_id", "kind", "scene_ref", "participants"])
def test_every_spine_slot_key_is_required(pack, vocab, config, missing):
    slot = {k: v for k, v in spine_slot(1).items() if k != missing}
    problems = segment_schema.validate_slots([slot], pack, vocab, config)
    assert problems and any(missing in p for p in problems)


def test_a_spine_slot_pointing_at_a_scene_that_does_not_exist_is_rejected(pack, vocab, config):
    """Spine slots are pointers to authored canon. A dangling `scene_ref` is a
    slot that will never play anything, and because Layer 3 deliberately does
    not generate for spine slots, nothing ever fills the gap."""
    problems = segment_schema.validate_slots(
        [spine_slot(1, scene_ref="the-drowning")], pack, vocab, config)
    assert problems and any("the-drowning" in p for p in problems)


def test_an_ambient_slot_may_not_carry_a_scene_ref(pack, vocab, config):
    """A slot cannot be both generated and canon. Accepting both would let a
    generated take overwrite scripted spine dialogue."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, scene_ref="moonwell")], pack, vocab, config)
    assert problems


def test_an_unknown_lore_stem_is_rejected(pack, vocab, config):
    """Lore stems are looked up by name at generation time. An invented stem
    resolves to nothing, so the slot is generated with no grounding at all and
    reads as generic fantasy filler."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, lore=["the-second-moon"])], pack, vocab, config)
    assert problems and any("the-second-moon" in p for p in problems)


def test_an_unknown_participant_is_rejected(pack, vocab, config):
    problems = segment_schema.validate_slots(
        [ambient_slot(1, participants=["helen", "nobody"])], pack, vocab, config)
    assert problems and any("nobody" in p for p in problems)


def test_a_slot_with_no_participants_is_rejected(pack, vocab, config):
    problems = segment_schema.validate_slots(
        [ambient_slot(1, participants=[])], pack, vocab, config)
    assert problems


def test_an_empty_ambient_prompt_is_rejected(pack, vocab, config):
    problems = segment_schema.validate_slots(
        [ambient_slot(1, prompt="  ")], pack, vocab, config)
    assert problems


def test_duplicate_slot_ids_are_rejected(pack, vocab, config):
    """Slot ids become directory names under `slots/`. Two slots sharing one
    id write their takes into the same directory, and the second silently
    inherits the first's."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1), ambient_slot(1)], pack, vocab, config)
    assert problems


# --------------------------------------------------------------------------
# sensitivity and depends_on (D14) — the take budget's contract
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sensitivity", ["none", "tone", "flags"])
def test_the_three_declared_sensitivities_are_accepted(pack, vocab, config, sensitivity):
    depends_on = ["helen-wounded"] if sensitivity == "flags" else []
    problems = segment_schema.validate_slots(
        [ambient_slot(1, sensitivity=sensitivity, depends_on=depends_on)],
        pack, vocab, config)
    assert problems == []


def test_an_unknown_sensitivity_is_rejected(pack, vocab, config):
    problems = segment_schema.validate_slots(
        [ambient_slot(1, sensitivity="vibes")], pack, vocab, config)
    assert problems and any("vibes" in p for p in problems)


def test_a_depends_on_key_outside_the_state_vocabulary_is_rejected(pack, vocab, config):
    """The same evaporation failure the arc planner guards against, one layer
    down: nothing ever sets `helen-cursed`, so the conditioned takes built
    from it can never be selected and the slot quietly falls back to its
    neutral take forever."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, sensitivity="flags", depends_on=["helen-cursed"])],
        pack, vocab, config)
    assert problems and any("helen-cursed" in p for p in problems)


def test_depends_on_must_be_empty_unless_sensitivity_is_flags(pack, vocab, config):
    """`worklist.conditions_for` only reads `depends_on` for `flags` slots.
    Dependencies declared on a `none` or `tone` slot are silently ignored, so
    the slot claims a state dependency it does not actually get."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, sensitivity="tone", depends_on=["helen-wounded"])],
        pack, vocab, config)
    assert problems


def test_a_flags_slot_with_no_dependencies_is_rejected(pack, vocab, config):
    """`conditions_for` builds its candidate list from `depends_on`; an empty
    one yields no candidates and every take falls back to neutral. The slot
    declared it varies by state and then does not."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, sensitivity="flags", depends_on=[])], pack, vocab, config)
    assert problems


def test_a_flags_slot_cannot_declare_more_states_than_takes_can_cover(pack, vocab, config):
    """The budget is arithmetic, not taste. With `takes_per_slot: 3` and
    `neutral_takes: 1` there are exactly two conditioned takes, and
    `conditions_for` emits two candidates per flag. Three dependencies need
    six takes to cover; the four uncovered combinations play a line written
    for the wrong state."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, sensitivity="flags",
                      depends_on=["helen-wounded", "moonwell-tainted",
                                  "buffalo-lost-axe"])],
        pack, vocab, config)
    assert problems


def test_one_dependency_fits_the_budget_exactly(pack, vocab, config):
    """Two conditioned takes, two candidates (true and false). This is the
    shape the take budget was designed around."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, sensitivity="flags", depends_on=["helen-wounded"])],
        pack, vocab, config)
    assert problems == []


def test_sensitivity_mix_counts_each_kind(config):
    slots = ([ambient_slot(i) for i in range(1, 6)]
             + [ambient_slot(i, sensitivity="tone") for i in range(6, 9)]
             + [ambient_slot(i, sensitivity="flags",
                             depends_on=["helen-wounded"]) for i in range(9, 11)])
    mix = segment_schema.sensitivity_mix(slots)
    assert mix == {"none": 5, "tone": 3, "flags": 2}


def test_spine_slots_are_not_counted_in_the_mix(config):
    """Spine slots have no `sensitivity` at all — they are canon. Counting
    them would dilute the fraction and let the flags budget be exceeded."""
    mix = segment_schema.sensitivity_mix([ambient_slot(1), spine_slot(2)])
    assert sum(mix.values()) == 1


def test_a_sensitivity_budget_within_the_cap_reports_nothing(config):
    slots = ([ambient_slot(i, sensitivity="flags", depends_on=["helen-wounded"])
              for i in range(1, 4)]
             + [ambient_slot(i) for i in range(4, 11)])
    assert segment_schema.check_sensitivity_budget(slots, config) is None


def test_exceeding_the_sensitivity_budget_is_reported(config):
    """Over the cap the three-take pool cannot cover the declared states and
    the airtime patch tier is over-subscribed. This is a WARNING, not a
    rejection — the brief is still usable, it just needs a human to look."""
    slots = ([ambient_slot(i, sensitivity="flags", depends_on=["helen-wounded"])
              for i in range(1, 8)]
             + [ambient_slot(i) for i in range(8, 11)])
    message = segment_schema.check_sensitivity_budget(slots, config)
    assert message and "0.4" in message


def test_the_budget_check_survives_a_slot_list_with_no_ambient_slots(config):
    """Division by zero on a spine-only chapter. Rare, but it would abort a
    six-hour segment over a chapter that is entirely scripted canon."""
    assert segment_schema.check_sensitivity_budget([spine_slot(1)], config) is None


def test_an_ambient_prompt_yaml_read_as_null_is_rejected(pack, vocab, config):
    """Same failure one layer down."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, prompt=None)], pack, vocab, config)
    assert problems and any("s-001" in p for p in problems)


def test_participants_that_yaml_read_as_null_is_rejected(pack, vocab, config):
    problems = segment_schema.validate_slots(
        [ambient_slot(1, participants=None)], pack, vocab, config)
    assert problems


def test_a_depends_on_that_yaml_read_as_null_is_rejected(pack, vocab, config):
    """`sensitivity: flags` with a bare `depends_on:` is the same claim as an
    empty list — the slot says it varies by state and then does not."""
    problems = segment_schema.validate_slots(
        [ambient_slot(1, sensitivity="flags", depends_on=None)],
        pack, vocab, config)
    assert problems





# ---------------------------------------------------------------------------
# build_expand_prompt / build_leaf_prompt
# ---------------------------------------------------------------------------

def test_expand_prompt_carries_the_segment_and_node_context(pack, segment_config,
                                                            arc_segment):
    target = node(node_id="seg-001-n1", depth=1, target_words=20000,
                  target_slots=63)

    prompt = segment_schema.build_expand_prompt(
        pack, arc_segment, [], target, segment_config, None)

    assert arc_segment["synopsis"] in prompt
    assert target["summary"] in prompt
    assert "20000" in prompt
    assert "weight" in prompt.lower()


def test_expand_prompt_states_an_expected_child_count(pack, segment_config,
                                                      arc_segment):
    """ceil(target_slots / max_leaf_slots), capped at max_children. Without a
    guideline the model picks a number unrelated to the airtime it is
    dividing."""
    target = node(target_slots=170)
    expected = min(math.ceil(170 / 19), 12)

    prompt = segment_schema.build_expand_prompt(
        pack, arc_segment, [], target, segment_config, None)

    assert str(expected) in prompt


def test_expected_child_count_is_capped_at_max_children(pack, segment_config,
                                                        arc_segment):
    target = node(target_slots=5000)
    prompt = segment_schema.build_expand_prompt(
        pack, arc_segment, [], target, segment_config, None)
    assert "12" in prompt


def test_expand_prompt_renders_the_ancestor_chain_shallow_to_deep(pack, segment_config,
                                                                  arc_segment):
    a1 = node(node_id="seg-001-n0", depth=1, title="First half")
    a2 = node(node_id="seg-001-n0-n1", depth=2, title="The ford")
    target = node(node_id="seg-001-n0-n1-n0", depth=3, target_slots=40)

    prompt = segment_schema.build_expand_prompt(
        pack, arc_segment, [a1, a2], target, segment_config, None)

    assert prompt.index("First half") < prompt.index("The ford")


def test_expand_prompt_includes_previous_problems_on_retry(pack, segment_config,
                                                           arc_segment):
    target = node(target_slots=170)
    prompt = segment_schema.build_expand_prompt(
        pack, arc_segment, [], target, segment_config,
        ["child 1 weight must be a positive number, got -5"])
    assert "-5" in prompt


def test_expand_prompt_says_nothing_about_a_previous_attempt_when_clean(
        pack, segment_config, arc_segment):
    target = node(target_slots=170)
    prompt = segment_schema.build_expand_prompt(
        pack, arc_segment, [], target, segment_config, None)
    assert "previous" not in prompt.lower()


def test_leaf_prompt_asks_for_the_nodes_slot_count(pack, segment_config, arc_segment):
    leaf = node(node_id="seg-001-n2", depth=1, target_slots=17, target_words=5355)

    prompt = segment_schema.build_leaf_prompt(
        pack, arc_segment, [], leaf, segment_config, None)

    assert "17" in prompt
    assert arc_segment["synopsis"] in prompt


def test_leaf_prompt_carries_the_spine_scenes(pack, segment_config, arc_segment):
    """Spine ground truth is not chapter-scoped — every leaf sees it
    regardless of depth, exactly as the flat version did."""
    leaf = node(target_slots=19)
    prompt = segment_schema.build_leaf_prompt(
        pack, arc_segment, [], leaf, segment_config, None)
    assert "moonwell" in prompt


def test_both_prompts_are_deterministic(pack, segment_config, arc_segment):
    """Two runs must produce byte-identical prompts, or a misbehaving run
    cannot be reproduced."""
    target = node(target_slots=170)
    ancestors = [node(node_id="seg-001-n0", depth=1)]

    for builder in (segment_schema.build_expand_prompt,
                    segment_schema.build_leaf_prompt):
        first = builder(pack, arc_segment, ancestors, target, segment_config, None)
        second = builder(pack, arc_segment, ancestors, target, segment_config, None)
        assert first == second


# ---------------------------------------------------------------------------
# merge_brief — the tree flattened into the shape worklist.py already reads
# ---------------------------------------------------------------------------

def _leaf_tree():
    """Root with three leaf children, 2 slots each, in order."""
    root = node(node_id="seg-001", depth=0, kind="branch",
                children=["seg-001-n0", "seg-001-n1", "seg-001-n2"])
    nodes = {"seg-001": root}
    for order in range(3):
        nid = f"seg-001-n{order}"
        nodes[nid] = node(node_id=nid, parent_id="seg-001", order=order, depth=1,
                          target_words=17866, target_slots=2, kind="leaf",
                          slots=[ambient_slot(order * 2 + 1),
                                 ambient_slot(order * 2 + 2)])
    return nodes


def test_merge_brief_produces_the_worklist_contract(arc_segment, segment_config):
    brief = segment_schema.merge_brief(arc_segment, _leaf_tree(), segment_config)

    for key in ("segment_id", "order", "loop", "hours", "synopsis",
                "continuity_in", "continuity_out", "carry_in", "carry_out",
                "slots"):
        assert key in brief, f"brief is missing {key!r}"
    assert brief["segment_id"] == "seg-001"
    assert brief["carry_out"] == {"helen-wounded": True, "moonwell-tainted": True}


def test_merge_brief_flattens_leaves_in_dfs_order(arc_segment, segment_config):
    """Draining in arc order is what makes a stopped run leave a contiguous
    usable prefix of airtime rather than a scattered fraction."""
    brief = segment_schema.merge_brief(arc_segment, _leaf_tree(), segment_config)

    assert [s["slot_id"] for s in brief["slots"]] == [
        "s-001", "s-002", "s-003", "s-004", "s-005", "s-006"]


def test_every_slot_is_tagged_with_its_node_id(arc_segment, segment_config):
    """Provenance moves from chapter_id to node_id."""
    brief = segment_schema.merge_brief(arc_segment, _leaf_tree(), segment_config)

    assert brief["slots"][0]["node_id"] == "seg-001-n0"
    assert brief["slots"][-1]["node_id"] == "seg-001-n2"
    assert all("chapter_id" not in s for s in brief["slots"])


def test_merge_brief_includes_a_nodes_summary(arc_segment, segment_config):
    """So a human reading brief.yaml by hand can see the tree shape the run
    actually produced."""
    brief = segment_schema.merge_brief(arc_segment, _leaf_tree(), segment_config)

    assert "nodes" in brief
    by_id = {n["node_id"]: n for n in brief["nodes"]}
    leaf = by_id["seg-001-n0"]
    assert leaf["depth"] == 1
    assert leaf["kind"] == "leaf"
    assert leaf["forced"] is False
    assert leaf["target_slots"] == 2
    assert leaf["slots"] == 2       # the ACTUAL count, next to the target


def test_merge_brief_reports_a_forced_leaf(arc_segment, segment_config):
    nodes = _leaf_tree()
    nodes["seg-001-n1"]["forced"] = True

    brief = segment_schema.merge_brief(arc_segment, nodes, segment_config)

    by_id = {n["node_id"]: n for n in brief["nodes"]}
    assert by_id["seg-001-n1"]["forced"] is True


def test_merge_brief_walks_a_deep_tree(arc_segment, segment_config):
    """Depth is not fixed at one — a grandchild's slots must appear in the
    right place in the flattened order."""
    nodes = {
        "seg-001": node(node_id="seg-001", depth=0, kind="branch",
                        children=["seg-001-n0", "seg-001-n1"]),
        "seg-001-n0": node(node_id="seg-001-n0", parent_id="seg-001", order=0,
                           depth=1, kind="branch",
                           children=["seg-001-n0-n0", "seg-001-n0-n1"]),
        "seg-001-n0-n0": node(node_id="seg-001-n0-n0", parent_id="seg-001-n0",
                              order=0, depth=2, kind="leaf",
                              slots=[ambient_slot(1)]),
        "seg-001-n0-n1": node(node_id="seg-001-n0-n1", parent_id="seg-001-n0",
                              order=1, depth=2, kind="leaf",
                              slots=[ambient_slot(2)]),
        "seg-001-n1": node(node_id="seg-001-n1", parent_id="seg-001", order=1,
                           depth=1, kind="leaf", slots=[ambient_slot(3)]),
    }

    brief = segment_schema.merge_brief(arc_segment, nodes, segment_config)

    assert [s["slot_id"] for s in brief["slots"]] == ["s-001", "s-002", "s-003"]
    assert brief["slots"][0]["node_id"] == "seg-001-n0-n0"


def test_merge_brief_orders_siblings_by_order_not_dict_insertion(arc_segment,
                                                                 segment_config):
    nodes = _leaf_tree()
    reversed_nodes = {"seg-001": nodes["seg-001"]}
    for nid in ("seg-001-n2", "seg-001-n1", "seg-001-n0"):
        reversed_nodes[nid] = nodes[nid]

    brief = segment_schema.merge_brief(arc_segment, reversed_nodes, segment_config)

    assert [s["slot_id"] for s in brief["slots"]] == [
        "s-001", "s-002", "s-003", "s-004", "s-005", "s-006"]
