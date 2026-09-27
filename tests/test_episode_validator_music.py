"""Tests for the music-cue stage of app/episode_validator.py: the optional
per-event `music` object and the optional `show.music` header
(docs/episode_validator.md "Scene mood cues").

These exercise the real validate_episode (including the dry run) so they
also prove a `music` key is harmless to replay.Performer and plan_scenes.
"""
import copy

import pytest

import episode_validator
from episode_validator import EpisodeInvalid, GEMS_MOODS, validate_episode


def make_script(music_on=None, show=None):
    """Minimal valid 5-event episode; `music_on` maps event index -> music."""
    events = [
        {"type": "user_message", "text": "the tavern door creaks"},
        {"type": "assistant_text", "text": "who goes there?"},
        {"type": "tool_call", "tool": "bash", "text": "ls"},
        {"type": "assistant_text", "text": "done"},
        {"type": "user_message", "text": "thanks"},
    ]
    for index, music in (music_on or {}).items():
        events[index]["music"] = music
    script = {"source": "music-demo", "project": "virtualTubers",
              "session_id": "s1", "date": "2026-09-27", "events": events}
    if show is not None:
        script["show"] = show
    return script


# ── success paths ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("music", [
    {"mood": ["tension"]},
    {"mood": "sadness"},
    {"mood": ["tension", "nostalgia"], "intensity": 0.8, "scene_id": "party-attack"},
    {"mood": ["wonder"], "intensity": 0},
    {"mood": ["wonder"], "intensity": 1},
])
def test_valid_event_music_passes(music):
    assert validate_episode(make_script({0: music, 3: {"mood": "power"}}))["event_count"] == 5


def test_music_only_show_header_passes_without_slots():
    script = make_script({0: {"mood": ["tension"]}}, show={"music": {"theme": "ashiorid"}})
    assert validate_episode(script)["name"] == "music-demo"


def test_music_only_show_header_skips_speaker_slot_checks():
    # A music-only header casts nobody, so a slot-shaped speaker is judged
    # exactly as with no header at all (allowed).
    script = make_script(show={"music": {"theme": "ashiorid"}})
    script["events"][1]["speaker"] = "tuber_3"
    assert validate_episode(script)["event_count"] == 5


def test_show_music_alongside_slots_passes():
    show = {"slots": ["tuber_0"], "music": {"theme": "hp_test-2"}}
    assert validate_episode(make_script(show=show))["event_count"] == 5


def test_episode_without_music_is_untouched():
    assert validate_episode(make_script())["event_count"] == 5


def test_gems_moods_match_campaign_and_music_vocabularies():
    from campaign.pack import MOODS as PACK_MOODS
    from music.mood_map import MOODS as MUSIC_MOODS
    assert GEMS_MOODS == PACK_MOODS
    assert GEMS_MOODS == set(MUSIC_MOODS) - {"neutral"}


# ── event.music failures ─────────────────────────────────────────────────────

@pytest.mark.parametrize("music, pattern", [
    ("tension", r"event 2 music must be an object"),
    ({"intensity": 0.5}, r"event 2 music is missing required field 'mood'"),
    ({"mood": []}, r"event 2 music\.mood must be a GEMS mood name or a non-empty list"),
    ({"mood": 3}, r"event 2 music\.mood must be a GEMS mood name"),
    ({"mood": "neutral"}, r"'neutral', which is not a GEMS mood"),
    ({"mood": ["tension", "spooky"]}, r"'spooky', which is not a GEMS mood"),
    ({"mood": [1]}, r"'int', which is not a GEMS mood"),
    ({"mood": "tension", "intensity": "high"}, r"intensity must be a number"),
    ({"mood": "tension", "intensity": True}, r"intensity must be a number"),
    ({"mood": "tension", "intensity": 1.5}, r"intensity is 1.5, outside the valid range 0..1"),
    ({"mood": "tension", "intensity": -0.1}, r"outside the valid range 0..1"),
    ({"mood": "tension", "scene_id": 7}, r"scene_id must be a string"),
    ({"mood": "tension", "scene_id": "x" * 129}, r"scene_id must be a string of at most 128"),
])
def test_invalid_event_music_is_rejected(music, pattern):
    with pytest.raises(EpisodeInvalid, match=pattern):
        validate_episode(make_script({2: music}))


# ── show.music failures ──────────────────────────────────────────────────────

@pytest.mark.parametrize("music, pattern", [
    ("ashiorid", r"'show.music' must be an object"),
    ({"theme": "../etc"}, r"'show.music.theme' must be 1-64 characters"),
    ({"theme": ""}, r"'show.music.theme' must be 1-64 characters"),
    ({"theme": "a" * 65}, r"'show.music.theme' must be 1-64 characters"),
    ({"theme": 5}, r"'show.music.theme' must be 1-64 characters"),
])
def test_invalid_show_music_is_rejected(music, pattern):
    with pytest.raises(EpisodeInvalid, match=pattern):
        validate_episode(make_script(show={"music": music}))


def test_show_with_other_keys_still_requires_slots():
    with pytest.raises(EpisodeInvalid, match="has no 'slots'"):
        validate_episode(make_script(show={"music": {"theme": "ashiorid"}, "title": "x"}))


def test_music_check_does_not_mutate_the_script():
    script = make_script({0: {"mood": "tension", "intensity": 0.4}},
                         show={"music": {"theme": "ashiorid"}})
    before = copy.deepcopy(script)
    episode_validator._check_music(script)
    assert script == before
