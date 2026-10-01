"""Tests for the day-structure support (arc.day_phases) and segment-scoped
spine scenes.

Motivation: the 2026-09-29 ashiorid_office run asked for a 24h day and got
four copies of the 06:00 standup, because the arc prompt never told the model
which part of the day each segment was and the leaf prompt offered every pack
spine scene to every block. These tests pin the fix.
"""
import pytest

import arc_schema
from arc_helpers import config, pack, segment, vocab  # noqa: F401 (fixtures)


DAY = [
    {"clock": "00:00-06:00", "label": "off-hours", "focus": "The dark office.",
     "spine_scenes": []},
    {"clock": "06:00-12:00", "label": "morning", "focus": "Standup and planning.",
     "spine_scenes": ["moonwell"]},
    {"clock": "12:00-18:00", "label": "build", "focus": "Building and testing.",
     "spine_scenes": []},
    {"clock": "18:00-00:00", "label": "ship", "focus": "Release and wind-down.",
     "spine_scenes": []},
]


@pytest.fixture
def day_config(config):
    config["arc"]["hours_total"] = 24
    config["arc"]["batch_size"] = 4
    config["arc"]["day_phases"] = [dict(p) for p in DAY]
    return config


# --------------------------------------------------------------------------
# day_phase_map
# --------------------------------------------------------------------------

def test_day_phase_map_is_empty_without_the_config_key(config):
    assert arc_schema.day_phase_map(config) == {}


def test_day_phase_map_assigns_one_phase_per_order(day_config):
    phases = arc_schema.day_phase_map(day_config)
    assert [phases[o]["label"] for o in range(4)] == ["off-hours", "morning", "build", "ship"]


def test_day_phase_map_repeats_the_day_across_a_week(day_config):
    day_config["arc"]["hours_total"] = 168
    phases = arc_schema.day_phase_map(day_config)
    assert len(phases) == 28
    assert phases[5]["label"] == "morning"


@pytest.mark.parametrize("bad", [
    "not a list",
    ["not a mapping"],
    [{"clock": "", "label": "x", "focus": "y"}],
    [{"clock": "a", "label": "x", "focus": "y", "spine_scenes": "moonwell"}],
])
def test_day_phase_map_rejects_malformed_config(day_config, bad):
    day_config["arc"]["day_phases"] = bad
    with pytest.raises(arc_schema.ArcPlanError):
        arc_schema.day_phase_map(day_config)


# --------------------------------------------------------------------------
# build_prompt / validate_batch
# --------------------------------------------------------------------------

def test_arc_prompt_states_each_orders_block_of_the_day(day_config):
    prompt = arc_schema.build_prompt("ctx", [0, 1, 2, 3], "", day_config, None,
                                     spine_scene_ids=["moonwell"])
    assert "DAY STRUCTURE" in prompt
    assert "order 0: 00:00-06:00 (off-hours)" in prompt
    assert "order 1: 06:00-12:00 (morning)" in prompt
    assert "Spine scenes: moonwell." in prompt
    assert "No spine scene: spine_scenes must be []." in prompt


def test_arc_prompt_has_no_day_section_without_day_phases(config):
    prompt = arc_schema.build_prompt("ctx", [0], "", config, None)
    assert "DAY STRUCTURE" not in prompt


def test_system_prompt_no_longer_hardcodes_a_168_hour_week():
    assert "168" not in arc_schema.SYSTEM_PROMPT
    assert "28 segments" not in arc_schema.SYSTEM_PROMPT


def test_a_day_that_follows_its_phases_validates(day_config, vocab):
    segs = [segment(0), segment(1, spine_scenes=["moonwell"]), segment(2), segment(3)]
    assert arc_schema.validate_batch(segs, [0, 1, 2, 3], set(), vocab, day_config) == []


def test_a_block_using_another_blocks_spine_scene_is_rejected(day_config, vocab):
    """The observed failure: every block re-used the standup."""
    segs = [segment(0, spine_scenes=["moonwell"]), segment(1, spine_scenes=["moonwell"]),
            segment(2), segment(3)]
    problems = arc_schema.validate_batch(segs, [0, 1, 2, 3], set(), vocab, day_config)
    assert any("order 0" in p and "off-hours" in p for p in problems)


def test_a_block_missing_its_spine_scene_is_rejected(day_config, vocab):
    segs = [segment(0), segment(1), segment(2), segment(3)]
    problems = arc_schema.validate_batch(segs, [0, 1, 2, 3], set(), vocab, day_config)
    assert any("order 1" in p and "moonwell" in p for p in problems)
