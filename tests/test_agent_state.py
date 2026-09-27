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


def test_write_state_survives_concurrent_tmp_path_collision(tmp_path):
    """Regression for a live outage: two write_state calls racing on the
    same fixed f"{path}.tmp" name could have the second call's os.replace
    source vanish (already consumed by the first call), raising
    FileNotFoundError uncaught out of agent.py's tick loop and crashing the
    ENTIRE worker's message consumer — no more replay_request handling at
    all — since startup.sh runs agent.py with no restart-on-crash
    supervisor. write_state must fall back to a per-call-unique temp name
    and still land a valid state file rather than raising."""
    path = str(tmp_path / "state.json")
    # Simulate the race directly: pre-create the fixed tmp path as a
    # directory (not a plain leftover file) so the real code path's
    # open(tmp_path, "w") itself raises FileNotFoundError-compatible... no —
    # simplest faithful repro is to delete the tmp file out from under
    # os.replace, which is exactly what a second writer's own os.replace
    # does to a first writer's still-open tmp file.
    tmp_path_file = path + ".tmp"

    real_replace = os.replace
    calls = []

    def replace_that_deletes_first(src, dst):
        calls.append(src)
        if len(calls) == 1:
            # Pretend a concurrent writer already consumed this exact tmp
            # file before we got to rename it.
            os.unlink(src)
            raise FileNotFoundError(2, "No such file or directory", src)
        real_replace(src, dst)

    import agent_state as agent_state_module
    orig = agent_state_module.os.replace
    agent_state_module.os.replace = replace_that_deletes_first
    try:
        written = write_state(path, "speaking", action="second writer wins")
    finally:
        agent_state_module.os.replace = orig

    assert os.path.exists(path)
    loaded = read_state(path)
    assert loaded["expression"] == "speaking"
    assert loaded["updated_at"] == written["updated_at"]
    assert not os.path.exists(tmp_path_file)
