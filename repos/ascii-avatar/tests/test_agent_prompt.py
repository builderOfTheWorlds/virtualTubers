import json
import pytest
from avatar.agent_prompt import build_prompt, parse_response


class TestBuildPrompt:
    def test_returns_system_and_user(self):
        result = build_prompt(
            sessions=[
                {"project": "vyzibl", "last_event": "PostToolUse",
                 "status": "active", "tool_count": 5, "error_count": 0},
            ],
            last_speech_ago=20.0,
        )
        assert "system" in result
        assert "user" in result
        assert "Ghost" in result["system"]
        assert "vyzibl" in result["user"]

    def test_includes_debounce_warning(self):
        result = build_prompt(sessions=[], last_speech_ago=3.0)
        assert "spoke" in result["user"].lower() or "recent" in result["user"].lower()

    def test_includes_session_data(self):
        result = build_prompt(
            sessions=[
                {"project": "xentra", "last_event": "PostToolUseFailure",
                 "status": "error", "tool_count": 3, "error_count": 1},
            ],
            last_speech_ago=60.0,
        )
        assert "xentra" in result["user"]
        assert "error" in result["user"].lower()


class TestParseResponse:
    def test_valid_json(self):
        raw = '{"state": "thinking", "speak": null}'
        state, speak = parse_response(raw)
        assert state == "thinking"
        assert speak is None

    def test_valid_with_speech(self):
        raw = '{"state": "error", "speak": "Build failed. Check vyzibl."}'
        state, speak = parse_response(raw)
        assert state == "error"
        assert speak == "Build failed. Check vyzibl."

    def test_malformed_json_returns_defaults(self):
        state, speak = parse_response("not json at all")
        assert state == "idle"
        assert speak is None

    def test_missing_state_returns_idle(self):
        state, speak = parse_response('{"speak": "hello"}')
        assert state == "idle"
        assert speak == "hello"

    def test_truncates_long_speech(self):
        raw = json.dumps({"state": "speaking", "speak": "a " * 100})
        state, speak = parse_response(raw)
        # Should truncate to roughly 10 words
        assert len(speak.split()) <= 12

    def test_extracts_json_from_markdown_fence(self):
        raw = '```json\n{"state": "thinking", "speak": null}\n```'
        state, speak = parse_response(raw)
        assert state == "thinking"
        assert speak is None
