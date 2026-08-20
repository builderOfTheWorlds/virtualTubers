"""Acceptance tests for src/segment_schema.py — the pure half of Layer 2.

Layer 2 turns one arc segment into ~170 authored slots, in two passes: 2a
emits ~9 chapter one-liners, and 2b turns each chapter into ~19 slots. This
module owns the parsing, the validation, the prompt text and the final merge
into `brief.yaml`. It never calls a model and never touches disk.

Two rules here are load-bearing in ways that are not obvious:

  - `sensitivity` and `depends_on` (design decision D14) are what tell Layer 3
    how to spend takes 002 and 003. A slot marked `flags` claims a share of a
    three-take budget that has to cover every state combination it declares,
    so the fraction of `flags` slots is capped — over-subscribe it and the
    pool cannot cover the states, which shows up at runtime as a slot with no
    playable line.
  - The brief's top-level key is `slots:`, because `worklist.build_worklist`
    reads `brief["slots"]`. PLAN.md's older single-call pseudocode calls it
    `scenes:`; worklist is the promoted, tested contract and wins.
"""
import logging

import pytest
import yaml

import segment_schema
from arc_helpers import FakeCastMember, FakePack, pack, vocab
from segment_helpers import (ambient_slot, arc_segment, chapter,
                             chapters_reply, segment_config, slots_reply,
                             spine_slot)


@pytest.fixture
def config(segment_config):
    """`vocab` (from arc_helpers) is built from a fixture named `config`."""
    return segment_config


# --------------------------------------------------------------------------
# 2a — parsing and validating chapters
# --------------------------------------------------------------------------

def test_chapters_parse_from_plain_yaml():
    chapters = segment_schema.parse_chapters(chapters_reply(9))
    assert [c["order"] for c in chapters] == list(range(9))


def test_chapters_parse_from_a_fenced_block():
    fenced = "```yaml\n" + chapters_reply(9) + "```"
    assert len(segment_schema.parse_chapters(fenced)) == 9


def test_chapters_parse_from_a_bare_list():
    bare = yaml.safe_dump([chapter(i) for i in range(9)], sort_keys=False)
    assert len(segment_schema.parse_chapters(bare)) == 9


def test_an_unparseable_chapter_reply_raises():
    with pytest.raises(segment_schema.SegmentPlanError):
        segment_schema.parse_chapters("I cannot help with that.")


def test_a_good_chapter_list_validates(config):
    problems = segment_schema.validate_chapters(
        [chapter(i) for i in range(9)], config)
    assert problems == []


@pytest.mark.parametrize("missing", [
    "chapter_id", "order", "title", "summary", "continuity_in", "continuity_out",
])
def test_every_chapter_key_is_required(config, missing):
    ch = {k: v for k, v in chapter(0).items() if k != missing}
    problems = segment_schema.validate_chapters([ch], config)
    assert problems and any(missing in p for p in problems)


def test_the_wrong_number_of_chapters_is_rejected(config):
    """`chapters_per_segment` is how the six hours get divided. Eight chapters
    where nine were asked for is forty minutes of airtime that no 2b call ever
    covers, and nothing downstream notices the gap."""
    problems = segment_schema.validate_chapters(
        [chapter(i) for i in range(8)], config)
    assert problems


def test_chapter_orders_must_run_from_zero(config):
    problems = segment_schema.validate_chapters(
        [chapter(i) for i in range(1, 10)], config)
    assert problems


def test_duplicate_chapter_ids_are_rejected(config):
    chapters = [chapter(i) for i in range(9)]
    chapters[3]["chapter_id"] = chapters[0]["chapter_id"]
    problems = segment_schema.validate_chapters(chapters, config)
    assert problems


def test_an_empty_chapter_summary_is_rejected(config):
    """The summary is the entire input to that chapter's 2b call. An empty one
    passes a has-the-key check and yields nineteen slots of nothing."""
    chapters = [chapter(i) for i in range(9)]
    chapters[2]["summary"] = "   "
    problems = segment_schema.validate_chapters(chapters, config)
    assert problems and any("ch-03" in p for p in problems)


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


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------

def test_the_chapter_prompt_carries_the_segment_synopsis(pack, arc_segment, config):
    prompt = segment_schema.build_chapter_prompt(pack, arc_segment, config, None)
    assert "The company reaches the moonwell and finds it fouled." in prompt


def test_the_chapter_prompt_asks_for_the_configured_chapter_count(pack, arc_segment, config):
    prompt = segment_schema.build_chapter_prompt(pack, arc_segment, config, None)
    assert "9" in prompt


def test_the_chapter_prompt_carries_both_continuity_ends(pack, arc_segment, config):
    prompt = segment_schema.build_chapter_prompt(pack, arc_segment, config, None)
    assert "They are two days out from the ridge." in prompt
    assert "Helen is wounded and the well is tainted." in prompt


def test_the_slot_prompt_carries_the_chapter_summary(pack, arc_segment, config):
    prompt = segment_schema.build_slot_prompt(pack, arc_segment, chapter(2),
                                              config, None)
    assert "part 3" in prompt


def test_the_slot_prompt_states_a_concrete_slot_target(pack, arc_segment, config):
    """`target_slots` divided by `chapters_per_segment` is ~19. Without a
    number the model treats "a sequence of beats" as open-ended and stops
    wherever it feels like, which is how a six-hour segment comes back with
    forty slots."""
    prompt = segment_schema.build_slot_prompt(pack, arc_segment, chapter(0),
                                              config, None)
    assert "19" in prompt


def test_the_slot_prompt_lists_the_legal_state_keys(pack, arc_segment, config):
    """Same reasoning as the arc planner: validation rejects an invented
    `depends_on` key, but rejection costs a retry."""
    prompt = segment_schema.build_slot_prompt(pack, arc_segment, chapter(0),
                                              config, None)
    for flag in config["state"]["flags"]:
        assert flag in prompt


def test_the_slot_prompt_lists_the_cast(pack, arc_segment, config):
    prompt = segment_schema.build_slot_prompt(pack, arc_segment, chapter(0),
                                              config, None)
    assert "helen" in prompt and "buffalo" in prompt


def test_the_slot_prompt_carries_spine_ground_truth(pack, arc_segment, config):
    """The model bridges AROUND authored spine content and never invents spine
    plot. It can only do that if it can see the real narration."""
    prompt = segment_schema.build_slot_prompt(pack, arc_segment, chapter(0),
                                              config, None)
    assert "Water the colour of a bruise." in prompt


def test_the_slot_prompt_demands_ear_written_prompts(pack, arc_segment, config):
    """Matches the pack format doc's existing ambient-authoring rule. Prompts
    written for the page produce takes that are unspeakable aloud."""
    prompt = segment_schema.build_slot_prompt(pack, arc_segment, chapter(0),
                                              config, None)
    assert "for the ear" in prompt.lower()


def test_problems_reach_the_retry_prompt(pack, arc_segment, config):
    prompt = segment_schema.build_slot_prompt(
        pack, arc_segment, chapter(0), config,
        ["slot 's-001' depends_on has unknown key 'helen-cursed'"])
    assert "helen-cursed" in prompt


def test_a_first_attempt_prompt_mentions_no_previous_attempt(pack, arc_segment, config):
    prompt = segment_schema.build_slot_prompt(pack, arc_segment, chapter(0),
                                              config, None)
    assert "previous attempt" not in prompt.lower()


def test_both_system_prompts_are_non_empty():
    assert segment_schema.SYSTEM_PROMPT_CHAPTERS.strip()
    assert segment_schema.SYSTEM_PROMPT_SLOTS.strip()


# --------------------------------------------------------------------------
# merge_brief — what Layer 3 and worklist actually read
# --------------------------------------------------------------------------

def test_the_brief_uses_the_key_worklist_reads(arc_segment, config):
    """`worklist.build_worklist` does `brief["slots"]`. Naming it `scenes`
    yields an empty worklist and a run that reports success in three seconds
    having generated nothing."""
    brief = segment_schema.merge_brief(arc_segment, [chapter(0)],
                                       {"ch-01": [ambient_slot(1)]}, config)
    assert "slots" in brief


def test_the_brief_carries_the_segment_identity(arc_segment, config):
    brief = segment_schema.merge_brief(arc_segment, [chapter(0)],
                                       {"ch-01": [ambient_slot(1)]}, config)
    assert brief["segment_id"] == "seg-001"


def test_slots_are_merged_in_chapter_order(arc_segment, config):
    """Chapters are 2b'd concurrently, so `slots_by_chapter` arrives in
    whatever order the pool finished. Airtime is sequential; a brief whose
    slots are ordered by completion time plays the segment's evening before
    its morning."""
    chapters = [chapter(i) for i in range(3)]
    slots_by_chapter = {
        "ch-03": [ambient_slot(30)],
        "ch-01": [ambient_slot(10)],
        "ch-02": [ambient_slot(20)],
    }
    brief = segment_schema.merge_brief(arc_segment, chapters, slots_by_chapter,
                                       config)
    assert [s["slot_id"] for s in brief["slots"]] == ["s-010", "s-020", "s-030"]


def test_each_slot_records_the_chapter_it_came_from(arc_segment, config):
    """Chapter boundaries are the event roll points, and resume is
    chapter-granular. A slot that cannot say which chapter it belongs to makes
    both impossible."""
    brief = segment_schema.merge_brief(arc_segment, [chapter(0)],
                                       {"ch-01": [ambient_slot(1)]}, config)
    assert brief["slots"][0]["chapter_id"] == "ch-01"


def test_a_chapter_with_no_slots_yet_is_simply_absent(arc_segment, config):
    """Merging is called on partial state during a resumed run. It must not
    raise on the chapters that have not been 2b'd yet."""
    chapters = [chapter(i) for i in range(3)]
    brief = segment_schema.merge_brief(arc_segment, chapters,
                                       {"ch-02": [ambient_slot(20)]}, config)
    assert [s["slot_id"] for s in brief["slots"]] == ["s-020"]


def test_the_brief_keeps_the_arc_continuity(arc_segment, config):
    """Layer 3 conditions its takes on where the segment starts and ends."""
    brief = segment_schema.merge_brief(arc_segment, [chapter(0)],
                                       {"ch-01": [ambient_slot(1)]}, config)
    assert brief["continuity_in"] == "They are two days out from the ridge."
    assert brief["continuity_out"] == "Helen is wounded and the well is tainted."


# --------------------------------------------------------------------------
# the shapes a model reply actually arrives in
# --------------------------------------------------------------------------

def test_a_chapter_summary_yaml_read_as_null_is_rejected(config):
    """`summary:` with nothing after it is valid YAML and parses to None. The
    key IS present, so a required-key check passes it through, and the next
    thing to touch it calls `.strip()` on None — an AttributeError out of a
    function whose entire contract is to return problems instead of raising.
    This is the single most likely malformed reply a model produces."""
    chapters = [chapter(i) for i in range(9)]
    chapters[4]["summary"] = None
    problems = segment_schema.validate_chapters(chapters, config)
    assert problems and any("ch-05" in p for p in problems)


def test_a_chapter_summary_that_is_not_a_string_is_rejected(config):
    chapters = [chapter(i) for i in range(9)]
    chapters[4]["summary"] = ["two", "lines"]
    assert segment_schema.validate_chapters(chapters, config)


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


def test_the_chapter_prompt_does_not_hardcode_the_order_range(pack, arc_segment, config):
    """`chapters_per_segment` is a config knob. A prompt that says "0 to 8"
    while asking for five chapters contradicts itself in the same breath, and
    the model resolves the contradiction whichever way it likes."""
    config["segment"]["chapters_per_segment"] = 5
    prompt = segment_schema.build_chapter_prompt(pack, arc_segment, config, None)
    assert "0 to 8" not in prompt
    assert "5" in prompt


def test_the_slot_target_tracks_the_configured_totals(pack, arc_segment, config):
    """Same reasoning: 200 slots across 10 chapters is 20, not 19."""
    config["segment"]["target_slots"] = 200
    config["segment"]["chapters_per_segment"] = 10
    prompt = segment_schema.build_slot_prompt(pack, arc_segment, chapter(0),
                                              config, None)
    assert "20" in prompt


def test_the_chapter_prompt_states_the_order_range_it_will_be_validated_against(
        pack, arc_segment, config):
    """`validate_chapters` requires `order` to be exactly `range(n)`. A model
    told only "include an order key" numbers them from 1, which is a rejected
    batch and a wasted retry on every single segment of the week. The range
    must be derived, not literal — see the 5-chapter test below."""
    prompt = segment_schema.build_chapter_prompt(pack, arc_segment, config, None)
    assert "0" in prompt and "8" in prompt


def test_the_stated_order_range_follows_the_config(pack, arc_segment, config):
    config["segment"]["chapters_per_segment"] = 5
    prompt = segment_schema.build_chapter_prompt(pack, arc_segment, config, None)
    assert "4" in prompt
    assert "0 to 8" not in prompt
