"""Adapter tests for the opencode/aider backends: the CLI is faked (these
tools aren't installed on dev machines) — what's under test is config
generation, commit behavior, and TaskResult assembly around the call."""
import json

import pytest

import coding_backends.opencode_backend as opencode_module
import coding_backends.aider_backend as aider_module
from coding_backends.opencode_backend import OpenCodeBackend
from coding_backends.aider_backend import AiderBackend
from git_client import GitClient


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("x = 1\n", encoding="utf-8")
    git = GitClient(str(ws), "TEST-1")
    git.init_repo()
    return ws, git


LLM_CONFIG = {"base_url": "http://llmhost:11434", "model": "test-model"}


def _fake_cli(monkeypatch, module, rc=0, output="ok", side_effect=None):
    calls = []

    def fake(cmd, cwd, env=None, timeout=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        if side_effect:
            side_effect()
        return rc, output

    monkeypatch.setattr(module, "run_cli", fake)
    return calls


def test_opencode_writes_config_and_commits_changes(workspace, monkeypatch):
    ws, git = workspace

    def edit_a_file():
        (ws / "app.py").write_text("x = 2\n", encoding="utf-8")

    calls = _fake_cli(monkeypatch, opencode_module, side_effect=edit_a_file)
    backend = OpenCodeBackend(str(ws), git, {}, 60, llm_config=LLM_CONFIG)

    result = backend.run_task("change x to 2")

    assert result.success
    assert result.backend == "opencode"
    config = json.loads((ws / "opencode.json").read_text(encoding="utf-8"))
    assert config["provider"]["ollama"]["options"]["baseURL"] == "http://llmhost:11434/v1"
    assert config["model"] == "ollama/test-model"
    assert calls[0]["cmd"][:2] == ["opencode", "run"]


def test_opencode_nonzero_exit_is_failure(workspace, monkeypatch):
    ws, git = workspace
    _fake_cli(monkeypatch, opencode_module, rc=1, output="boom")
    backend = OpenCodeBackend(str(ws), git, {}, 60, llm_config=LLM_CONFIG)

    result = backend.run_task("anything")

    assert not result.success
    assert "exited 1" in result.error


def test_aider_passes_ollama_env_and_model(workspace, monkeypatch):
    ws, git = workspace
    calls = _fake_cli(monkeypatch, aider_module)
    backend = AiderBackend(str(ws), git, {}, 60, llm_config=LLM_CONFIG)

    backend.run_task("do a thing")

    call = calls[0]
    assert call["env"]["OLLAMA_API_BASE"] == "http://llmhost:11434"
    assert "ollama_chat/test-model" in call["cmd"]
    assert "--yes-always" in call["cmd"]


def test_aider_fallback_commit_sweeps_dirty_tree(workspace, monkeypatch):
    ws, git = workspace

    def edit_without_commit():
        (ws / "app.py").write_text("x = 3\n", encoding="utf-8")

    _fake_cli(monkeypatch, aider_module, side_effect=edit_without_commit)
    backend = AiderBackend(str(ws), git, {}, 60, llm_config=LLM_CONFIG)

    result = backend.run_task("change x to 3")

    assert result.success
    assert result.committed
    assert not git.is_dirty()


def test_backend_model_override_beats_llm_config(workspace, monkeypatch):
    ws, git = workspace
    calls = _fake_cli(monkeypatch, aider_module)
    backend = AiderBackend(
        str(ws), git, {"model": "special-model"}, 60, llm_config=LLM_CONFIG
    )

    backend.run_task("task")

    assert "ollama_chat/special-model" in calls[0]["cmd"]
