"""WP-00 acceptance tests: the qwen_worker harness runs on Windows.

The harness hardcoded a POSIX venv path and localhost/Ollama defaults that do
not hold on the dev PC (Windows venv layout, Ollama on the gx10 box). These
tests pin the two fixes the WP requires:

  * ``sandbox.venv_python`` resolves the venv interpreter for either host.
  * the Ollama base-url / model defaults are overridable from the environment
    so a ``runner.py preflight`` reaches the gx10 without a CLI flag.

``deploy/`` must land in the verification sandbox (later WPs read its compose
and scripts), and ``*.har`` captures must never be copied (they are large and
irrelevant to the suite).

Import note (deliberate deviation from the usual ``sys.path.insert(0, ...)``
convention): the harness package contains a ``runner.py`` whose bare name
collides with the benchmarker's flat ``runner`` module
(``utilities/benchmarker/lib/runner.py``), which its own conftest also puts on
``sys.path``. If this test inserted ``tools/qwen_worker`` onto ``sys.path``,
the benchmark's ``from runner import Runner`` would resolve to the harness's
file and abort the whole suite's collection. We therefore load the two harness
modules by explicit file path under unique top-level names and never touch
``sys.path``.
"""
import importlib.util
import os
import pathlib
import shutil

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
QWEN_WORKER = ROOT / "tools" / "qwen_worker"


def _load_module(alias, filename):
    """Load a harness module by file path under a unique top-level name.

    Re-executes the file on every call (the result is not cached in
    ``sys.modules``), so import-time reads — e.g. ``os.environ`` — see the
    environment as it stands at load time. This is what lets the env-override
    test re-read ``ollama_client`` under a patched environment.
    """
    spec = importlib.util.spec_from_file_location(alias, QWEN_WORKER / filename)
    assert spec is not None and spec.loader is not None  # path checked by caller
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sandbox = _load_module("qw_wp00_sandbox", "sandbox.py")


def _fresh_ollama():
    return _load_module("qw_wp00_ollama", "ollama_client.py")


# ── venv_python (T00.1–T00.3) ────────────────────────────────────────────────
def test_venv_python_finds_posix(tmp_path):
    """T00.1: venv_python returns .venv/bin/python when it exists."""
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    result = sandbox.venv_python(tmp_path)
    assert result == tmp_path / ".venv" / "bin" / "python"


def test_venv_python_finds_windows(tmp_path):
    """T00.2: venv_python returns .venv/Scripts/python.exe when only that exists."""
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_text("")
    result = sandbox.venv_python(tmp_path)
    assert result == tmp_path / ".venv" / "Scripts" / "python.exe"


def test_venv_python_missing_raises(tmp_path):
    """T00.3: venv_python raises a clear error when neither interpreter exists."""
    (tmp_path / ".venv").mkdir(parents=True)
    with pytest.raises(FileNotFoundError) as excinfo:
        sandbox.venv_python(tmp_path)
    message = str(excinfo.value)
    assert "python" in message.lower()
    assert ".venv" in message


# ── build_sandbox (T00.4) ────────────────────────────────────────────────────
def test_build_sandbox_copies_deploy_and_skips_har(tmp_path):
    """T00.4: build_sandbox copies deploy/ but never copies *.har files."""
    (tmp_path / "deploy").mkdir(parents=True)
    (tmp_path / "deploy" / "data.json").write_text("{}")
    (tmp_path / "deploy" / "capture.har").write_text("[]")
    # A har outside deploy/ too: the ignore must not be scoped to deploy/.
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "tests" / "site.har").write_text("[]")

    sandbox_root = sandbox.build_sandbox(tmp_path, {})
    try:
        assert (sandbox_root / "deploy" / "data.json").is_file()
        assert not (sandbox_root / "deploy" / "capture.har").exists()
        assert not (sandbox_root / "tests" / "site.har").exists()
    finally:
        shutil.rmtree(sandbox_root, ignore_errors=True)


# ── ollama_client env defaults (T00.5) ───────────────────────────────────────
def test_env_vars_override_ollama_defaults(monkeypatch):
    """T00.5: QWEN_WORKER_BASE_URL / QWEN_WORKER_MODEL override the defaults."""
    builtin_base = "http://localhost:11434"
    builtin_model = "qwen3-coder:30b"

    # Baseline: no env set -> built-in defaults.
    os.environ.pop("QWEN_WORKER_BASE_URL", None)
    os.environ.pop("QWEN_WORKER_MODEL", None)
    base0 = _fresh_ollama()
    assert base0.DEFAULT_BASE_URL == builtin_base
    assert base0.DEFAULT_MODEL == builtin_model

    # Override: the env vars win. monkeypatch restores them on teardown.
    monkeypatch.setenv("QWEN_WORKER_BASE_URL", "http://gx10:11434")
    monkeypatch.setenv("QWEN_WORKER_MODEL", "qwen3.8:27b")
    base1 = _fresh_ollama()
    assert base1.DEFAULT_BASE_URL == "http://gx10:11434"
    assert base1.DEFAULT_MODEL == "qwen3.8:27b"
