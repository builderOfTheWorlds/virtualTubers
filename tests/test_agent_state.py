import os

import pytest

from agent_state import resolve_state_path, write_state, read_state, DEFAULT_STATE_FILE


def test_resolve_state_path_prefers_env_var(monkeypatch):
    monkeypatch.setenv("AGENT_STATE_FILE", "/tmp/from-env.json")
    assert resolve_state_path({"state_file": "/tmp/from-config.json"}) == "/tmp/from-env.json"


def test_resolve_state_path_falls_back_to_config(monkeypatch):
    monkeypatch.delenv("AGENT_STATE_FILE", raising=False)
    assert resolve_state_path({"state_file": "/tmp/from-config.json"}) == "/tmp/from-config.json"


def test_resolve_state_path_defaults_when_nothing_set(monkeypatch):
    monkeypatch.delenv("AGENT_STATE_FILE", raising=False)
    assert resolve_state_path({}) == DEFAULT_STATE_FILE
    assert resolve_state_path(None) == DEFAULT_STATE_FILE


def test_write_state_then_read_state_round_trips(tmp_path):
    path = str(tmp_path / "state.json")

    written = write_state(path, "speaking", action="replying to manager", bubble="On it!")

    assert os.path.exists(path)
    loaded = read_state(path)
    assert loaded["expression"] == "speaking"
    assert loaded["action"] == "replying to manager"
    assert loaded["bubble"] == "On it!"
    assert loaded["updated_at"] == written["updated_at"]


def test_write_state_leaves_no_temp_file_behind(tmp_path):
    path = str(tmp_path / "state.json")
    write_state(path, "idle")
    assert not os.path.exists(path + ".tmp")


def test_read_state_missing_file_returns_none(tmp_path):
    assert read_state(str(tmp_path / "does-not-exist.json")) is None


def test_read_state_malformed_json_returns_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert read_state(str(path)) is None
