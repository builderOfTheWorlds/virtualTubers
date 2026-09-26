"""Tests for app/emotion.py — the Ekman-6 + neutral vocabulary shared by
narration prompts, agent_state, and the mesh-morph layer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from emotion import (  # noqa: E402
    EMOTIONS, DEFAULT_EMOTION, normalize_emotion, parse_structured_reply,
    STRUCTURED_REPLY_INSTRUCTION,
)


def test_emotions_is_ekman_six_plus_neutral():
    assert set(EMOTIONS) == {
        "neutral", "happy", "sad", "angry", "afraid", "surprised", "disgusted",
    }


def test_default_emotion_is_neutral():
    assert DEFAULT_EMOTION == "neutral"


# ── normalize_emotion ────────────────────────────────────────────────────────
def test_normalize_emotion_passes_through_a_valid_value():
    assert normalize_emotion("happy") == "happy"


def test_normalize_emotion_is_case_insensitive():
    assert normalize_emotion("HAPPY") == "happy"
    assert normalize_emotion(" Sad ") == "sad"


def test_normalize_emotion_defaults_unrecognized_to_neutral():
    assert normalize_emotion("furious") == "neutral"
    assert normalize_emotion("") == "neutral"
    assert normalize_emotion(None) == "neutral"
    assert normalize_emotion(42) == "neutral"


# ── parse_structured_reply ───────────────────────────────────────────────────
def test_parses_a_clean_json_object():
    line, emotion = parse_structured_reply('{"line": "We won!", "emotion": "happy"}')
    assert line == "We won!"
    assert emotion == "happy"


def test_parses_json_inside_a_code_fence():
    raw = '```json\n{"line": "Careful.", "emotion": "afraid"}\n```'
    line, emotion = parse_structured_reply(raw)
    assert line == "Careful."
    assert emotion == "afraid"


def test_parses_json_with_leading_and_trailing_prose():
    raw = 'Sure, here is my reply:\n{"line": "Fine.", "emotion": "sad"}\nHope that helps!'
    line, emotion = parse_structured_reply(raw)
    assert line == "Fine."
    assert emotion == "sad"


def test_an_unrecognized_emotion_in_the_json_normalizes_to_neutral():
    line, emotion = parse_structured_reply('{"line": "Huh.", "emotion": "furious"}')
    assert line == "Huh."
    assert emotion == "neutral"


def test_json_missing_emotion_defaults_to_neutral():
    line, emotion = parse_structured_reply('{"line": "Just a line."}')
    assert line == "Just a line."
    assert emotion == "neutral"


def test_json_missing_line_returns_empty_string():
    line, emotion = parse_structured_reply('{"emotion": "happy"}')
    assert line == ""
    assert emotion == "happy"


def test_a_bare_string_with_no_json_is_treated_as_the_whole_line():
    line, emotion = parse_structured_reply("Just talking, no JSON here.")
    assert line == "Just talking, no JSON here."
    assert emotion == "neutral"


def test_malformed_json_falls_back_to_whole_reply_as_line():
    raw = '{"line": "broken, "emotion": "happy"}'
    line, emotion = parse_structured_reply(raw)
    assert line == raw
    assert emotion == "neutral"


def test_empty_or_non_string_reply_returns_empty_line_neutral():
    assert parse_structured_reply("") == ("", "neutral")
    assert parse_structured_reply("   ") == ("", "neutral")
    assert parse_structured_reply(None) == ("", "neutral")


def test_structured_reply_instruction_lists_every_emotion():
    for name in EMOTIONS:
        assert name in STRUCTURED_REPLY_INSTRUCTION
