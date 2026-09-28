"""Action beats in .claude/prompts/build_campaign_episode.py: a text-less
action beat is worded by its primitive (as the dry-run renderer does) and
aired as a speakerless user_message, instead of being dropped.

The builder is a script, not a module, so it is loaded by path.
"""
import importlib.util
import pathlib

import pytest

from campaign.pack import load_pack
from campaign.primitives import render as primitive_render

REPO = pathlib.Path(__file__).resolve().parents[1]
BUILDER = REPO / ".claude" / "prompts" / "build_campaign_episode.py"


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("_builder_actions", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def office_pack():
    return load_pack(REPO / "campaigns" / "ashiorid_office")


def _action_beats(pack):
    return [b for s in pack.scenes.values() for b in s.beats
            if b.kind == "action" and not b.text and b.primitive]


def test_office_action_beats_reach_episode_as_user_messages(builder, office_pack):
    episode = builder.build_episode(office_pack, "t", "p", max_scenes=10)
    texts = [e["text"] for e in episode["events"] if e["type"] == "user_message"]
    beats = _action_beats(office_pack)
    assert beats
    for beat in beats:
        name = office_pack.cast[beat.speaker].name
        assert primitive_render(beat.primitive, name, beat.params).strip() in texts


def test_action_text_empty_for_unknown_speaker_or_primitive(builder, office_pack):
    beat = _action_beats(office_pack)[0]
    ghost = type(beat)(**{**beat.__dict__, "speaker": "nobody"})
    bogus = type(beat)(**{**beat.__dict__, "primitive": "not_a_verb"})
    assert builder._action_text(office_pack, ghost) == ""
    assert builder._action_text(office_pack, bogus) == ""
