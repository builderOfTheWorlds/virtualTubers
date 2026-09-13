"""Tests for the show-header stage of app/episode_validator.py — V1 static
validation of the optional 'show' block (roundtable_stream_design.md v1.1
§7.1/§8.1).

Two properties matter most and each has a dedicated test:

  * an episode with NO 'show' key validates exactly as it did before
    (the §7.4 backwards-compat contract), and
  * the stage never touches the filesystem for voices — message-api has no
    /data/voices mount, so a file stat here would make the check unrunnable
    where it needs to run.

Like tests/test_episode_validator.py these exercise the real _dry_run: a
minimal 5-event script renders fine through replay.Performer with pacing off.
"""
import pathlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import episode_validator  # noqa: E402
import voice_registry  # noqa: E402
from episode_validator import (  # noqa: E402
    EpisodeInvalid,
    ROSTER_SIZE,
    validate_episode,
)
from voice_registry import PLATFORM_ONLY_KEYS  # noqa: E402

# The worked example's cast (§7.1), widened to the full seven-slot roster.
VOICES = [
    "narrator_warm",
    "alto_bright",
    "tenor_low",
    "alto_warm",
    "tenor_high",
    "baritone_mid",
    "bass_low",
]


def make_show(slot_count=ROSTER_SIZE):
    slots = [f"tuber_{index}" for index in range(slot_count)]
    return {
        "title": "The Malvakar Riddle — Act 1",
        "slots": slots,
        "persona": {
            slot: {"name": f"Character {index}", "voice": VOICES[index]}
            for index, slot in enumerate(slots)
        },
    }


def make_script(show=None, speakers=None, **overrides):
    """A minimal valid episode. `show` is only added when given, so the
    default script is the pre-show-header shape."""
    speakers = speakers or [None] * 5
    texts = ["hello there", "hi, how can I help?", "ls", "done", "thanks"]
    kinds = ["user_message", "assistant_text", "tool_call",
             "assistant_text", "user_message"]
    events = []
    for kind, text, speaker in zip(kinds, texts, speakers):
        event = {"type": kind, "text": text}
        if kind == "tool_call":
            event["tool"] = "bash"
        if speaker is not None:
            event["speaker"] = speaker
        events.append(event)

    script = {
        "source": "demo-show",
        "project": "virtualTubers",
        "session_id": "sess-1",
        "date": "2026-08-01",
        "events": events,
    }
    if show is not None:
        script["show"] = show
    script.update(overrides)
    return script


# ── success paths ────────────────────────────────────────────────────────────

def test_valid_seven_slot_show_header_passes():
    result = validate_episode(make_script(show=make_show()))
    assert result["name"] == "demo-show"
    assert result["event_count"] == 5


def test_episode_with_no_show_block_still_passes():
    # The §7.4 backwards-compat contract: every recorded-session replay in the
    # library has no 'show' key and must keep validating untouched.
    script = make_script()
    assert "show" not in script
    result = validate_episode(script)
    assert result["name"] == "demo-show"


def test_show_header_with_no_persona_block_passes():
    # 'persona' is optional; slots alone are a complete cast.
    show = make_show(3)
    del show["persona"]
    assert validate_episode(make_script(show=show))["event_count"] == 5


def test_events_may_name_cast_slots():
    show = make_show(3)
    script = make_script(show=show,
                         speakers=["tuber_0", "tuber_1", "tuber_2",
                                   "tuber_0", None])
    assert validate_episode(script)["event_count"] == 5


# ── rule 1: 'show' must be a dict ────────────────────────────────────────────

@pytest.mark.parametrize("bad_show", ["a string", ["tuber_0"], 42])
def test_show_must_be_an_object(bad_show):
    with pytest.raises(EpisodeInvalid, match="'show' must be an object"):
        validate_episode(make_script(show=bad_show))


# ── rule 2: 'show.slots' shape ───────────────────────────────────────────────

def test_show_slots_missing_is_rejected():
    show = make_show(3)
    del show["slots"]
    show["persona"] = {}
    with pytest.raises(EpisodeInvalid, match="has no 'slots'"):
        validate_episode(make_script(show=show))


def test_show_slots_must_be_a_list():
    show = make_show(3)
    show["slots"] = "tuber_0"
    with pytest.raises(EpisodeInvalid, match="'show.slots' must be a list"):
        validate_episode(make_script(show=show))


def test_show_slots_must_not_be_empty():
    show = make_show(3)
    show["slots"] = []
    show["persona"] = {}
    with pytest.raises(EpisodeInvalid, match="'show.slots' is empty"):
        validate_episode(make_script(show=show))


def test_show_slots_entries_must_be_strings():
    show = make_show(3)
    show["slots"][1] = 1
    with pytest.raises(EpisodeInvalid,
                       match=r"'show.slots\[1\]' must be a string slot id"):
        validate_episode(make_script(show=show))


# ── rule 3: slot id shape and roster bound ───────────────────────────────────

@pytest.mark.parametrize("bad_slot", ["alcinoe", "tuber_", "Tuber_1",
                                      "tuber_1x", " tuber_1"])
def test_slot_id_must_match_the_slot_pattern(bad_slot):
    show = make_show(3)
    show["slots"][1] = bad_slot
    show["persona"] = {}
    with pytest.raises(EpisodeInvalid) as excinfo:
        validate_episode(make_script(show=show))
    message = str(excinfo.value)
    assert "is not a slot id" in message
    assert bad_slot in message


def test_slot_index_at_or_beyond_the_roster_is_rejected():
    show = make_show(3)
    show["slots"][2] = f"tuber_{ROSTER_SIZE}"
    show["persona"] = {}
    with pytest.raises(EpisodeInvalid) as excinfo:
        validate_episode(make_script(show=show))
    message = str(excinfo.value)
    assert "outside the roster" in message
    assert f"tuber_{ROSTER_SIZE}" in message


def test_the_last_in_roster_slot_is_accepted():
    show = make_show(ROSTER_SIZE)
    assert show["slots"][-1] == f"tuber_{ROSTER_SIZE - 1}"
    assert validate_episode(make_script(show=show))["event_count"] == 5


# ── rule 4: persona shape and coherence with slots ───────────────────────────

def test_show_persona_must_be_an_object():
    show = make_show(3)
    show["persona"] = ["tuber_0"]
    with pytest.raises(EpisodeInvalid, match="'show.persona' must be an object"):
        validate_episode(make_script(show=show))


def test_persona_key_not_in_slots_is_rejected():
    show = make_show(3)
    show["persona"]["tuber_5"] = {"name": "Uncast", "voice": "bass_low"}
    with pytest.raises(EpisodeInvalid) as excinfo:
        validate_episode(make_script(show=show))
    message = str(excinfo.value)
    assert "'tuber_5'" in message
    assert "not in 'show.slots'" in message


# ── rule 5: persona block shape / no platform leakage ────────────────────────

def test_persona_block_must_be_an_object():
    show = make_show(3)
    show["persona"]["tuber_1"] = "Alcinoe"
    with pytest.raises(EpisodeInvalid,
                       match="'show.persona.tuber_1' must be an object"):
        validate_episode(make_script(show=show))


@pytest.mark.parametrize("leaked_key", PLATFORM_ONLY_KEYS)
def test_persona_block_carrying_platform_detail_is_rejected(leaked_key):
    show = make_show(3)
    show["persona"]["tuber_1"][leaked_key] = "some-platform-detail"
    with pytest.raises(EpisodeInvalid) as excinfo:
        validate_episode(make_script(show=show))
    message = str(excinfo.value)
    assert "platform detail" in message
    assert leaked_key in message


def test_platform_only_keys_come_from_the_registry_seam():
    # Guard against re-hardcoding the tuple here or in the validator.
    assert PLATFORM_ONLY_KEYS == ("model_path", "voice_id", "base_url")


# ── rule 6: voice name must be a registry entry ──────────────────────────────

def test_unknown_voice_name_is_rejected_and_echoed():
    show = make_show(3)
    show["persona"]["tuber_1"]["voice"] = "alto_bright_typo"
    with pytest.raises(EpisodeInvalid) as excinfo:
        validate_episode(make_script(show=show))
    message = str(excinfo.value)
    # A voice name is not a secret: naming it is the entire point of the check.
    assert "alto_bright_typo" in message
    assert "not in the voice registry" in message
    assert "tuber_1" in message


def test_unknown_voice_message_lists_the_known_names():
    show = make_show(3)
    show["persona"]["tuber_2"]["voice"] = "nope"
    with pytest.raises(EpisodeInvalid) as excinfo:
        validate_episode(make_script(show=show))
    message = str(excinfo.value)
    for name in voice_registry.names():
        assert name in message


def test_every_registry_name_is_castable():
    for name in voice_registry.names():
        show = make_show(1)
        show["persona"]["tuber_0"]["voice"] = name
        assert validate_episode(make_script(show=show))["event_count"] == 5


# ── rule 7: slot-shaped speakers must be cast ────────────────────────────────

def test_slot_shaped_speaker_not_in_slots_is_rejected():
    show = make_show(2)
    script = make_script(show=show,
                         speakers=["tuber_0", "tuber_4", None, None, None])
    with pytest.raises(EpisodeInvalid) as excinfo:
        validate_episode(script)
    message = str(excinfo.value)
    assert "event 1" in message
    assert "'tuber_4'" in message
    assert "not in 'show.slots'" in message


def test_non_slot_shaped_speaker_is_allowed():
    # OPEN-2 default (§7.4): a speaker that does not look like a slot id
    # renders in the show log with the raw id, so it must NOT be rejected.
    show = make_show(2)
    script = make_script(show=show,
                         speakers=["tuber_0", "narrator_npc", "the_crowd",
                                   None, "tuber_1"])
    assert validate_episode(script)["event_count"] == 5


def test_speakers_are_only_checked_when_a_show_block_exists():
    # Without a header there is no cast to be incoherent with.
    script = make_script(speakers=["tuber_4", None, None, None, None])
    assert validate_episode(script)["event_count"] == 5


# ── the capability constraint: no filesystem access for voices ───────────────

def test_show_check_never_touches_the_filesystem(monkeypatch):
    """message-api has NO /data/voices mount, so registry membership must be a
    pure config lookup. Any Path.exists during the stage means the check
    cannot run where it has to run."""
    def exploding_exists(self, *args, **kwargs):
        raise AssertionError(f"show validation stat'd the filesystem: {self}")

    # Warm the registry cache first: reading config/voices.yaml is the config
    # lookup itself, not a voice-asset stat, and in production the file is
    # mounted. What must never happen is a voice-file stat.
    voice_registry.load()
    monkeypatch.setattr(pathlib.Path, "exists", exploding_exists)

    episode_validator._check_show(make_script(show=make_show()))


def test_show_check_touches_no_data_voices_path(monkeypatch):
    opened = []
    real_open = open

    def recording_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", recording_open)
    episode_validator._check_show(make_script(show=make_show()))

    assert not [path for path in opened if "/data/voices" in path]
