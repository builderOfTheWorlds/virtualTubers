import pytest

from coding_backend import (
    CodingBackendError,
    TaskResult,
    build_coding_backend,
    run_cli,
    tail,
)


def test_taskresult_to_payload_round_trips_fields():
    result = TaskResult(
        backend="native", success=True, commit="abc123", committed=True,
        files_changed=2, insertions=10, deletions=3, duration_s=1.5,
        output="done", error=None,
    )
    payload = result.to_payload()
    assert payload["backend"] == "native"
    assert payload["success"] is True
    assert payload["commit"] == "abc123"
    assert payload["files_changed"] == 2


def test_tail_truncates_long_text():
    text = "x" * 10000
    out = tail(text, limit=100)
    assert len(out) == 101  # ellipsis + tail
    assert out.startswith("…")


def test_tail_passes_short_text_and_none():
    assert tail("short") == "short"
    assert tail(None) == ""


def test_run_cli_missing_tool_returns_failure_not_raise(tmp_path):
    rc, output = run_cli(["definitely-not-a-real-tool-xyz"], cwd=str(tmp_path))
    assert rc == -1
    assert "not installed" in output


def test_build_coding_backend_none_provider_returns_none():
    assert build_coding_backend({"coding_backend": {"provider": "none"}}) is None
    assert build_coding_backend({}) is None


def test_build_coding_backend_unknown_provider_raises(tmp_path):
    config = {"coding_backend": {"provider": "magic", "workspace": str(tmp_path)}}
    with pytest.raises(CodingBackendError):
        build_coding_backend(config)


def test_build_coding_backend_native_requires_llm_client(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.py").write_text("pass\n", encoding="utf-8")
    config = {
        "coding_backend": {"provider": "native", "workspace": str(ws)},
        "agent": {"name": "TEST-1"},
    }
    with pytest.raises(CodingBackendError):
        build_coding_backend(config, llm_client=None)


def test_build_coding_backend_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CODING_BACKEND", "none")
    config = {"coding_backend": {"provider": "native", "workspace": str(tmp_path)}}
    assert build_coding_backend(config) is None
