"""Segment-scoped spine scenes in the leaf layer (Layer 2).

Pins the 2026-09-29 fix: a leaf may only use its own arc segment's
spine_scenes, so an ambient-only block (off-hours, build, ship) cannot pull
in the day's one authored spine scene.
"""
import pytest

import segment_schema
from segment_helpers import segment_config, node, spine_slot  # noqa: F401
from arc_helpers import pack, vocab  # noqa: F401


@pytest.fixture
def config(segment_config):
    """`vocab` (from arc_helpers) is built from a fixture named `config`."""
    return segment_config


def test_leaf_prompt_only_offers_the_segments_own_spine_scenes(pack, segment_config):
    seg = {"id": "off-arc", "synopsis": "Night.", "continuity_in": "", "continuity_out": "",
           "spine_scenes": []}
    prompt = segment_schema.build_leaf_prompt(pack, seg, [], node(target_slots=4),
                                              segment_config, None)
    assert "- moonwell:" not in prompt
    assert "every slot must be kind: ambient" in prompt


def test_leaf_prompt_keeps_every_spine_when_the_segment_does_not_say(pack, segment_config):
    seg = {"id": "legacy", "synopsis": "Old.", "continuity_in": "", "continuity_out": ""}
    prompt = segment_schema.build_leaf_prompt(pack, seg, [], node(target_slots=4),
                                              segment_config, None)
    assert "- moonwell:" in prompt
    assert "- portal-encounter:" in prompt


def test_a_spine_slot_outside_the_segments_spine_scenes_is_rejected(pack, vocab, segment_config):
    problems = segment_schema.validate_slots([spine_slot(1)], pack, vocab, segment_config,
                                             allowed_spine=[])
    assert any("not a spine scene of this segment" in p for p in problems)


def test_a_spine_slot_inside_the_segments_spine_scenes_passes(pack, vocab, segment_config):
    assert segment_schema.validate_slots([spine_slot(1)], pack, vocab, segment_config,
                                         allowed_spine=["moonwell"]) == []


def test_validate_slots_ignores_spine_scope_when_not_given(pack, vocab, segment_config):
    assert segment_schema.validate_slots([spine_slot(1)], pack, vocab, segment_config) == []
