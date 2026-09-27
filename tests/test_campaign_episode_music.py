"""Scene-mood carry-through in the .claude/prompts campaign episode builders
(docs/episode_validator.md "Scene mood cues"): every campaign scene's GEMS
`mood:` list must reach the aired episode JSON as a `music` object on that
scene's first event, with the pack name as show.music.theme — and the
result must still pass the real episode validator.

The builders are scripts, not modules, so they are loaded by path.
"""
import importlib.util
import pathlib

import pytest

from campaign.pack import load_pack
from episode_validator import validate_episode

REPO = pathlib.Path(__file__).resolve().parents[1]
PROMPTS = REPO / ".claude" / "prompts"


def _load_script(filename):
    spec = importlib.util.spec_from_file_location(
        f"_builder_{filename[:-3]}", PROMPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ashiorid_pack():
    return load_pack(REPO / "campaigns" / "ashiorid")


@pytest.fixture(scope="module")
def campaign_builder():
    return _load_script("build_campaign_episode.py")


@pytest.fixture(scope="module")
def full_builder():
    return _load_script("build_ashiorid_full_episode.py")


def _expected_scene_cues(scenes):
    return [(s.id, list(s.mood)) for s in scenes if s.mood]


def test_build_campaign_episode_carries_every_scene_mood(ashiorid_pack, campaign_builder):
    ep = campaign_builder.build_episode(ashiorid_pack, "ashiorid", "virtualTubers", 12)
    cues = [(e["music"]["scene_id"], e["music"]["mood"]) for e in ep["events"] if "music" in e]
    expected = _expected_scene_cues(campaign_builder.walk_scenes(ashiorid_pack, 12))
    # NOTE: today only ashiorid's AMBIENT scenes carry moods, and this
    # builder skips ambient scenes — so the list may legitimately be empty.
    assert cues == expected
    assert ep["show"] == {"music": {"theme": "ashiorid"}}
    assert validate_episode(ep, "ashiorid")["event_count"] == len(ep["events"])


def test_build_campaign_episode_scene_without_mood_gets_no_cue(campaign_builder):
    import types
    scene = types.SimpleNamespace(id="s1", enter_narration="Hello.", mood=[],
                                  beats=[], ambient=False, default_next=None)
    events = [{"type": "user_message", "text": "Hello."}]
    campaign_builder.attach_scene_music(events, 0, scene)
    assert "music" not in events[0]


def test_full_episode_builder_carries_spine_moods(ashiorid_pack, full_builder):
    events, _words, loops = full_builder.build_events(ashiorid_pack, 30, target_words=1)
    assert loops == 1
    spine = full_builder._spine_scenes(ashiorid_pack, 30)
    spine_ids = {s.id for s in spine}
    cues = [(e["music"]["scene_id"], e["music"]["mood"]) for e in events
            if "music" in e and e["music"]["scene_id"] in spine_ids]
    assert cues == _expected_scene_cues(spine)
    assert full_builder.show_music_header(ashiorid_pack) == {"music": {"theme": "ashiorid"}}


def test_full_episode_builder_cues_injected_ambient_takes(tmp_path, ashiorid_pack, full_builder):
    """Ambient takes come from <pack>/generated/<scene_id>/*.yaml; the first
    event of each injected take must carry that ambient scene's mood."""
    import copy
    pack = copy.copy(ashiorid_pack)
    pack.root = tmp_path
    moody = [sid for sid in pack.ambient_scene_ids() if pack.scene(sid).mood]
    assert moody, "ashiorid ambient scenes are expected to carry moods"
    for sid in pack.ambient_scene_ids():
        take_dir = tmp_path / "generated" / sid
        take_dir.mkdir(parents=True)
        (take_dir / "001.yaml").write_text(
            "beats:\n  - speaker: gm\n    text: ambient line for " + sid + "\n",
            encoding="utf-8")
    events, _words, _loops = full_builder.build_events(pack, 30, target_words=1)
    ambient_cues = {}
    for e in events:
        if e["text"].startswith("ambient line for "):
            sid = e["text"][len("ambient line for "):]
            ambient_cues[sid] = e.get("music")
    assert ambient_cues, "the spine is expected to inject at least one ambient take"
    for sid, music in ambient_cues.items():
        mood = list(pack.scene(sid).mood)
        if mood:
            assert music == {"mood": mood, "scene_id": sid}
        else:
            assert music is None


def test_carry_music_into_part_reopens_split_part_with_prior_mood(full_builder):
    previous = [{"type": "user_message", "text": "a",
                 "music": {"mood": ["tension"], "scene_id": "s1"}},
                {"type": "user_message", "text": "b"}]
    part = [{"type": "user_message", "text": "c"}]
    full_builder.carry_music_into_part(part, previous)
    assert part[0]["music"] == {"mood": ["tension"], "scene_id": "s1"}
    # a copy, not an alias of the earlier event's dict
    assert part[0]["music"] is not previous[0]["music"]


def test_carry_music_into_part_keeps_parts_own_cue(full_builder):
    part = [{"type": "user_message", "text": "c", "music": {"mood": ["wonder"], "scene_id": "s9"}}]
    full_builder.carry_music_into_part(
        part, [{"type": "user_message", "text": "a", "music": {"mood": ["tension"]}}])
    assert part[0]["music"]["mood"] == ["wonder"]
