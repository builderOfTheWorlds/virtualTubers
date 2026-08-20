"""Acceptance tests for src/arc_schema.py — the pure half of Layer 1.

Everything here is a function of its arguments: no LLM, no network, no disk.
These are the checks that decide whether a model reply becomes canon, and the
closed-vocabulary rules among them are the reason an invented state key fails
loudly here instead of evaporating silently at runtime — nothing ever sets it,
so every slot that reads it takes the else-branch forever, with nothing in any
log to say a story thread went missing.
"""
import logging
import math

import pytest
import yaml

import arc_schema
from arc_helpers import (CARRY_KEYS, FakeLLM, FakePack, FakeScene, config,
                         pack, perfect_llm, reply_for, segment, vocab)


# --------------------------------------------------------------------------
# n_segments
# --------------------------------------------------------------------------

def test_n_segments_at_the_shipped_defaults(config):
    assert arc_schema.n_segments(config) == 28


def test_a_partial_final_segment_still_gets_planned(config):
    """168 hours is the target, not a guarantee. 170 hours is 28 full segments
    plus a stub, and rounding DOWN would silently drop the stub's two hours of
    airtime — the one shortfall nobody notices until the stream runs dry."""
    config["arc"]["hours_total"] = 170
    assert arc_schema.n_segments(config) == math.ceil(170 / 6) == 29


def test_a_zero_segment_length_is_rejected_rather_than_dividing_by_zero(config):
    config["arc"]["segment_hours"] = 0
    with pytest.raises(arc_schema.ArcPlanError):
        arc_schema.n_segments(config)


# --------------------------------------------------------------------------
# build_context — what the model is told about the campaign
# --------------------------------------------------------------------------

def test_context_carries_the_campaign_identity(pack, config):
    context = arc_schema.build_context(pack, config)
    assert "The Ashiorid Loop" in context
    assert "weird-west folk horror" in context


def test_context_carries_spine_scenes_with_their_narration(pack, config):
    """The arc model paces around the spine. Scene ids alone would let it place
    `portal-encounter` without knowing it is the loop boundary."""
    context = arc_schema.build_context(pack, config)
    assert "portal-encounter" in context
    assert "It opens the way it always does." in context


def test_context_carries_lore(pack, config):
    context = arc_schema.build_context(pack, config)
    assert "Time in Ashiorid folds back on itself" in context


def test_context_carries_ambient_scene_ids(pack, config):
    """Most segments are pure ambient. A model that cannot see the ambient
    pool writes `ambient_focus` entries that name nothing."""
    context = arc_schema.build_context(pack, config)
    assert "camp-chatter" in context
    assert "road-song" in context


def test_context_is_deterministic(pack, config):
    """Two runs must produce byte-identical context. Iterating a dict in
    nondeterministic order would make every resumed run send a different
    prompt, quietly defeating any prompt-level caching and making a
    misbehaving run impossible to reproduce."""
    assert arc_schema.build_context(pack, config) == arc_schema.build_context(pack, config)


# --------------------------------------------------------------------------
# parse_reply — models do not answer with clean YAML
# --------------------------------------------------------------------------

def test_plain_yaml_parses():
    segments = arc_schema.parse_reply(reply_for([0, 1]))
    assert [s["order"] for s in segments] == [0, 1]


def test_a_fenced_yaml_block_parses():
    """Instruction-tuned models wrap YAML in a code fence roughly half the
    time regardless of what the prompt asked for. Retrying on that would burn
    a 40-second generation to get back an equivalent answer."""
    fenced = "```yaml\n" + reply_for([0]) + "```"
    assert len(arc_schema.parse_reply(fenced)) == 1


def test_a_fence_without_a_language_tag_parses():
    fenced = "```\n" + reply_for([0]) + "```"
    assert len(arc_schema.parse_reply(fenced)) == 1


def test_prose_before_the_yaml_is_tolerated():
    chatty = "Sure! Here is the arc plan you asked for:\n\n" + reply_for([0])
    assert len(arc_schema.parse_reply(chatty)) == 1


def test_a_bare_top_level_list_parses():
    """Dropping the `segments:` wrapper is the single most common deviation.
    The content is unambiguous, so accepting it costs nothing and saves a
    retry."""
    bare = yaml.safe_dump([segment(0)], sort_keys=False)
    assert len(arc_schema.parse_reply(bare)) == 1


def test_an_unparseable_reply_raises(caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(arc_schema.ArcPlanError):
        arc_schema.parse_reply("I'm sorry, I can't help with that.")


def test_a_segments_key_that_is_not_a_list_raises():
    with pytest.raises(arc_schema.ArcPlanError):
        arc_schema.parse_reply(yaml.safe_dump({"segments": "twenty-eight of them"}))


def test_a_list_of_non_mappings_raises():
    with pytest.raises(arc_schema.ArcPlanError):
        arc_schema.parse_reply(yaml.safe_dump({"segments": ["seg-001", "seg-002"]}))


# --------------------------------------------------------------------------
# normalize_segment — MVP forward-compatibility
# --------------------------------------------------------------------------

def test_a_missing_fork_normalizes_to_none():
    """Macro-forks are deferred, but the on-disk schema is fixed NOW so the
    later increment does not have to migrate a written arc plan. Filling the
    key in ourselves is strictly better than asking the model for it: with no
    fork design in the prompt yet, anything it invents is noise."""
    normalized = arc_schema.normalize_segment({k: v for k, v in segment(0).items()
                                             if k != "fork"})
    assert normalized["fork"] is None


def test_a_missing_event_windows_normalizes_to_empty():
    """Same reasoning, sharper: there is no event table yet, so every event id
    the model could name would be a hallucination."""
    normalized = arc_schema.normalize_segment({k: v for k, v in segment(0).items()
                                             if k != "event_windows"})
    assert normalized["event_windows"] == []


def test_normalization_does_not_overwrite_what_the_model_supplied():
    normalized = arc_schema.normalize_segment(segment(0, event_windows=["harvest"]))
    assert normalized["event_windows"] == ["harvest"]


# --------------------------------------------------------------------------
# validate_batch — the closed vocabulary is enforced here
# --------------------------------------------------------------------------

def test_a_good_batch_has_no_problems(vocab, config):
    problems = arc_schema.validate_batch([segment(0), segment(1)],
                                       expected_orders=[0, 1],
                                       known_ids=set(), vocab=vocab, config=config)
    assert problems == []


@pytest.mark.parametrize("missing", [
    "id", "order", "loop", "hours", "spine_scenes", "ambient_focus",
    "synopsis", "continuity_in", "continuity_out", "carry_in", "carry_out",
])
def test_every_required_key_is_required(vocab, config, missing):
    seg = {k: v for k, v in segment(0).items() if k != missing}
    problems = arc_schema.validate_batch([seg], expected_orders=[0],
                                       known_ids=set(), vocab=vocab, config=config)
    assert problems, f"a segment missing {missing!r} was accepted"
    assert any(missing in p for p in problems)


def test_a_problem_names_the_segment_it_is_about(vocab, config):
    seg = {k: v for k, v in segment(4).items() if k != "synopsis"}
    problems = arc_schema.validate_batch([seg], expected_orders=[4],
                                       known_ids=set(), vocab=vocab, config=config)
    assert any("seg-005" in p for p in problems)


def test_an_id_colliding_with_an_already_planned_segment_is_rejected(vocab, config):
    """Resume makes this reachable: the model is asked for orders 12-17 and
    answers with seg-001. Accepting it would give the plan two segments with
    one id, and every downstream path (`segments/<id>/brief.yaml`) would
    collide on disk."""
    problems = arc_schema.validate_batch([segment(12, id="seg-001")],
                                       expected_orders=[12],
                                       known_ids={"seg-001"}, vocab=vocab,
                                       config=config)
    assert any("seg-001" in p for p in problems)


def test_a_duplicate_id_within_one_batch_is_rejected(vocab, config):
    problems = arc_schema.validate_batch([segment(0), segment(1, id="seg-001")],
                                       expected_orders=[0, 1],
                                       known_ids=set(), vocab=vocab, config=config)
    assert problems


def test_orders_must_be_the_ones_that_were_asked_for(vocab, config):
    """The batch loop asks for a specific span. A model that renumbers from
    zero on every call would otherwise overwrite batch 1 five times over."""
    problems = arc_schema.validate_batch([segment(0), segment(1)],
                                       expected_orders=[6, 7],
                                       known_ids=set(), vocab=vocab, config=config)
    assert problems


def test_orders_out_of_sequence_are_rejected(vocab, config):
    problems = arc_schema.validate_batch([segment(1), segment(0)],
                                       expected_orders=[0, 1],
                                       known_ids=set(), vocab=vocab, config=config)
    assert problems


def test_a_short_batch_is_rejected(vocab, config):
    """Asked for six, answered with four. Silently accepting it leaves two
    hours-blocks unplanned that no later batch will ever ask about."""
    problems = arc_schema.validate_batch([segment(0)], expected_orders=[0, 1],
                                       known_ids=set(), vocab=vocab, config=config)
    assert problems


def test_a_carry_out_key_outside_the_vocabulary_is_rejected(vocab, config):
    """This is the failure the closed vocabulary exists to catch, and it is
    invisible at runtime: nothing ever sets `helen-cursed`, so every slot that
    reads it takes the else-branch forever and the arc quietly loses a thread.
    A hard failure here costs one retry."""
    problems = arc_schema.validate_batch([segment(0, carry_out={"helen-cursed": True})],
                                       expected_orders=[0], known_ids=set(),
                                       vocab=vocab, config=config)
    assert any("helen-cursed" in p for p in problems)


def test_a_carry_in_key_outside_the_vocabulary_is_rejected(vocab, config):
    problems = arc_schema.validate_batch([segment(0, carry_in={"helen-cursed": False})],
                                       expected_orders=[0], known_ids=set(),
                                       vocab=vocab, config=config)
    assert any("helen-cursed" in p for p in problems)


def test_a_flag_that_is_not_a_carry_key_is_still_rejected_in_carry_out(vocab, config):
    """`buffalo-lost-axe` is a real flag but not a carry key. Carry keys are
    the subset that survives a loop reset; carrying a non-carry flag across
    the boundary contradicts the reset it is supposed to survive."""
    problems = arc_schema.validate_batch([segment(0, carry_out={"buffalo-lost-axe": True})],
                                       expected_orders=[0], known_ids=set(),
                                       vocab=vocab, config=config)
    assert any("buffalo-lost-axe" in p for p in problems)


def test_a_spine_scene_that_does_not_exist_is_rejected(vocab, config):
    problems = arc_schema.validate_batch([segment(0, spine_scenes=["the-drowning"])],
                                       expected_orders=[0], known_ids=set(),
                                       vocab=vocab, config=config)
    assert any("the-drowning" in p for p in problems)


def test_a_real_spine_scene_is_accepted(vocab, config):
    problems = arc_schema.validate_batch([segment(0, spine_scenes=["portal-encounter"])],
                                       expected_orders=[0], known_ids=set(),
                                       vocab=vocab, config=config)
    assert problems == []


def test_the_wrong_number_of_hours_is_rejected(vocab, config):
    """Every segment is `arc.segment_hours` long; Layer 2's slot budget is
    computed from that constant. A segment claiming 3 hours would be planned
    with 6 hours of slots."""
    problems = arc_schema.validate_batch([segment(0, hours=3)], expected_orders=[0],
                                       known_ids=set(), vocab=vocab, config=config)
    assert problems


def test_an_empty_synopsis_is_rejected(vocab, config):
    """The synopsis is the entire brief Layer 2 gets for the segment. An empty
    one passes a has-the-key check and produces 170 slots of nothing."""
    problems = arc_schema.validate_batch([segment(0, synopsis="   ")],
                                       expected_orders=[0], known_ids=set(),
                                       vocab=vocab, config=config)
    assert problems


@pytest.mark.parametrize("bad_loop", [-1, "one", 1.5, None])
def test_a_nonsensical_loop_number_is_rejected(vocab, config, bad_loop):
    problems = arc_schema.validate_batch([segment(0, loop=bad_loop)],
                                       expected_orders=[0], known_ids=set(),
                                       vocab=vocab, config=config)
    assert problems


def test_carry_maps_must_be_mappings_not_lists(vocab, config):
    """`carry_out: [helen-wounded]` is the shape a model reaches for when it
    is thinking of a set. It has no values, so nothing downstream can read
    it, and a key-membership check that iterates a list still passes."""
    problems = arc_schema.validate_batch([segment(0, carry_out=["helen-wounded"])],
                                       expected_orders=[0], known_ids=set(),
                                       vocab=vocab, config=config)
    assert problems


# --------------------------------------------------------------------------
# build_prompt — ONE builder, used by every attempt
# --------------------------------------------------------------------------

def test_the_prompt_names_the_orders_being_requested(config):
    prompt = arc_schema.build_prompt("CONTEXT", [6, 7, 8], "They reached the ford.",
                                   config, None)
    assert "6" in prompt and "8" in prompt


def test_the_prompt_carries_the_context(config):
    assert "CONTEXT" in arc_schema.build_prompt("CONTEXT", [0], "", config, None)


def test_the_prompt_carries_the_previous_continuity(config):
    prompt = arc_schema.build_prompt("CONTEXT", [6], "They reached the ford.",
                                   config, None)
    assert "They reached the ford." in prompt


def test_an_empty_previous_continuity_becomes_the_start_of_the_arc(config):
    """One builder, one place this default lives. When the retry path built
    its own prompt separately, this is exactly the line that went missing from
    the copy."""
    prompt = arc_schema.build_prompt("CONTEXT", [0], "", config, None)
    assert "start of the arc" in prompt.lower()


def test_the_prompt_lists_every_legal_carry_key(config):
    prompt = arc_schema.build_prompt("CONTEXT", [0], "", config, None)
    for key in CARRY_KEYS:
        assert key in prompt


def test_problems_appear_in_the_prompt_when_supplied(config):
    prompt = arc_schema.build_prompt("CONTEXT", [0], "", config,
                                   ["segment 'seg-001' carry_out has unknown key 'helen-cursed'"])
    assert "helen-cursed" in prompt


def test_no_problem_text_leaks_into_a_first_attempt(config):
    """A first attempt that mentions "the previous attempt" is confusing to the
    model and a tell that the builder is stitching state it should not have."""
    prompt = arc_schema.build_prompt("CONTEXT", [0], "", config, None)
    assert "previous attempt" not in prompt.lower()


