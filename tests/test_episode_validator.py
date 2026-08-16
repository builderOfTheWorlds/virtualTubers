"""Tests for app/episode_validator.py — the 4-stage gate (shape, name, leak
audit, dry run) every uploaded episode passes through before message-api's
POST /replays stores it.

No real DB or renderer mocking needed for the success path: a minimal
5-event script actually renders fine through the real replay.Performer and
revoice.plan_scenes with pacing disabled, so most tests exercise the real
_dry_run. Only the dedicated dry-run-failure test replaces replay.Performer.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import episode_validator  # noqa: E402
import replay  # noqa: E402
from episode_validator import (  # noqa: E402
    EpisodeInvalid,
    MIN_EVENTS,
    REQUIRED_KEYS,
    resolve_name,
    validate_episode,
)


def make_valid_script(**overrides):
    script = {
        "source": "demo-ep",
        "project": "virtualTubers",
        "session_id": "sess-1",
        "date": "2026-08-01",
        "events": [
            {"type": "user_message", "text": "hello there"},
            {"type": "assistant_text", "text": "hi, how can I help?"},
            {"type": "tool_call", "tool": "bash", "text": "ls"},
            {"type": "assistant_text", "text": "done"},
            {"type": "user_message", "text": "thanks"},
        ],
    }
    script.update(overrides)
    return script


# ── shape ────────────────────────────────────────────────────────────────────

def test_validate_episode_fails_when_script_is_not_a_dict():
    with pytest.raises(EpisodeInvalid, match="expected a JSON object"):
        validate_episode(["not", "a", "dict"])


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_validate_episode_fails_when_a_required_key_is_missing(key):
    script = make_valid_script()
    del script[key]
    with pytest.raises(EpisodeInvalid, match="missing required key"):
        validate_episode(script)


def test_validate_episode_fails_when_events_is_not_a_list():
    script = make_valid_script(events="not a list")
    with pytest.raises(EpisodeInvalid, match="'events' must be a list"):
        validate_episode(script)


def test_validate_episode_fails_when_fewer_than_min_events():
    script = make_valid_script(events=[{"type": "assistant_text", "text": "hi"}])
    assert len(script["events"]) < MIN_EVENTS
    with pytest.raises(EpisodeInvalid, match="are needed for a watchable episode"):
        validate_episode(script)


def test_validate_episode_fails_when_an_event_is_not_a_dict():
    script = make_valid_script()
    script["events"][0] = "not a dict"
    with pytest.raises(EpisodeInvalid, match="expected an object"):
        validate_episode(script)


def test_validate_episode_fails_on_unsupported_event_type():
    script = make_valid_script()
    script["events"][0] = {"type": "screenshot", "text": "hi"}
    with pytest.raises(EpisodeInvalid, match="unsupported type"):
        validate_episode(script)


@pytest.mark.parametrize("kind,field", [
    ("user_message", "text"),
    ("assistant_text", "text"),
    ("tool_call", "tool"),
])
def test_validate_episode_fails_when_event_missing_required_field(kind, field):
    script = make_valid_script()
    script["events"][0] = {"type": kind}
    with pytest.raises(EpisodeInvalid, match=f"missing required field {field!r}"):
        validate_episode(script)


def test_shape_failure_is_reported_before_name_failure():
    # events too short AND an invalid name — shape (_check_shape) runs
    # before resolve_name, so the shape message must be the one raised.
    script = make_valid_script(events=[{"type": "assistant_text", "text": "hi"}],
                                source="bad name with spaces")
    with pytest.raises(EpisodeInvalid, match="are needed for a watchable episode"):
        validate_episode(script)


# ── name ─────────────────────────────────────────────────────────────────────

def test_resolve_name_uses_override_when_given():
    script = make_valid_script(source="from-script")
    assert resolve_name(script, override="from-override") == "from-override"


def test_resolve_name_falls_back_to_script_source():
    script = make_valid_script(source="from-script")
    assert resolve_name(script) == "from-script"


def test_resolve_name_fails_when_no_name_available():
    with pytest.raises(EpisodeInvalid, match="no episode name"):
        resolve_name({})


def test_resolve_name_fails_when_override_is_blank():
    script = make_valid_script(source="from-script")
    with pytest.raises(EpisodeInvalid, match="no episode name"):
        resolve_name(script, override="   ")


@pytest.mark.parametrize("bad_name", [
    "has spaces",
    "path/separator",
    "../traversal",
    "trailing/slash/",
    "semi;colon",
    "quote'mark",
])
def test_resolve_name_fails_on_disallowed_characters(bad_name):
    with pytest.raises(EpisodeInvalid, match="letters, digits, dot"):
        resolve_name(None, override=bad_name)


def test_resolve_name_accepts_a_128_character_name():
    name = "a" * 128
    assert resolve_name(None, override=name) == name


def test_resolve_name_rejects_a_129_character_name():
    with pytest.raises(EpisodeInvalid, match="1-128 characters"):
        resolve_name(None, override="a" * 129)


def test_resolve_name_accepts_dots_dashes_and_underscores():
    name = "ep_1.2-final"
    assert resolve_name(None, override=name) == name


# ── leak audit ───────────────────────────────────────────────────────────────

def test_validate_episode_fails_leak_audit_without_echoing_the_match(monkeypatch):
    monkeypatch.setattr(episode_validator, "audit", lambda payload: "sk-ant-super-secret-value")

    with pytest.raises(EpisodeInvalid) as excinfo:
        validate_episode(make_valid_script())

    assert "failed the leak audit" in str(excinfo.value)
    assert "sk-ant-super-secret-value" not in str(excinfo.value)


def test_validate_episode_passes_when_audit_finds_nothing(monkeypatch):
    monkeypatch.setattr(episode_validator, "audit", lambda payload: None)
    result = validate_episode(make_valid_script())
    assert result["name"] == "demo-ep"


def test_validate_episode_actually_trips_on_a_real_leaked_password():
    # End-to-end through the real session_log_parser.audit, no monkeypatch:
    # a credential-shaped value embedded in event text must be caught.
    script = make_valid_script()
    script["events"][1]["text"] = "ran export POSTGRES_PASSWORD=hunter2 now"
    with pytest.raises(EpisodeInvalid, match="failed the leak audit"):
        validate_episode(script)


# ── byte size cap ────────────────────────────────────────────────────────────

def test_validate_episode_fails_when_over_max_bytes(monkeypatch):
    monkeypatch.setattr(episode_validator, "MAX_BYTES", 10)
    with pytest.raises(EpisodeInvalid, match="byte limit"):
        validate_episode(make_valid_script())


def test_validate_episode_passes_at_default_max_bytes():
    # Sanity check the real default is generous enough for a normal script.
    result = validate_episode(make_valid_script())
    assert result["byte_size"] < episode_validator.MAX_BYTES


# ── dry run ──────────────────────────────────────────────────────────────────

def test_dry_run_raises_episode_invalid_when_performer_explodes(monkeypatch):
    def exploding_performer(*args, **kwargs):
        raise RuntimeError("renderer exploded")
    monkeypatch.setattr(replay, "Performer", exploding_performer)

    with pytest.raises(EpisodeInvalid, match="dry-run performance"):
        episode_validator._dry_run(make_valid_script())


def test_validate_episode_fails_when_dry_run_explodes(monkeypatch):
    def exploding_performer(*args, **kwargs):
        raise RuntimeError("renderer exploded")
    monkeypatch.setattr(replay, "Performer", exploding_performer)

    with pytest.raises(EpisodeInvalid, match="dry-run performance"):
        validate_episode(make_valid_script())


# ── success path ─────────────────────────────────────────────────────────────

def test_validate_episode_succeeds_on_a_valid_script():
    result = validate_episode(make_valid_script())
    assert result["name"] == "demo-ep"
    assert result["event_count"] == 5
    assert isinstance(result["byte_size"], int)
    assert result["byte_size"] > 0


def test_validate_episode_success_uses_name_override():
    result = validate_episode(make_valid_script(), name="overridden-name")
    assert result["name"] == "overridden-name"
