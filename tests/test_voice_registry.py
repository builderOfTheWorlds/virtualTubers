"""
Tests for app/voice_registry.py — the symbolic voice registry
(roundtable_stream_design.md v1.1 §7.2).

Everything here is a pure config-lookup test. Registry fixtures are built under
tmp_path, so nothing asserts against the real config/voices.yaml (which will
gain entries over time). No real .onnx is ever opened: verify()'s load step
imports ``tts_client._load_local_voice`` lazily, so tests monkeypatch that
attribute and use empty placeholder files to satisfy the existence check.

The registry caches per resolved path in a MODULE-LEVEL dict, so every test
clears it via the autouse ``clear_cache`` fixture — without that, one test's
registry leaks into the next.

conftest.py adds app/ to sys.path, so `import voice_registry` works directly.
"""
import pathlib

import pytest
import yaml

import tts_client
import voice_registry


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    """Module-level _cache is shared state — reset before AND after each test."""
    voice_registry._cache.clear()
    monkeypatch.delenv("VOICE_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("VOICES_DIR", raising=False)
    yield
    voice_registry._cache.clear()


@pytest.fixture
def write_registry(tmp_path):
    """Write an arbitrary document to a registry file and return its path."""
    counter = {"n": 0}

    def _write(document, name=None):
        counter["n"] += 1
        path = tmp_path / (name or f"voices_{counter['n']}.yaml")
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(yaml.safe_dump(document), encoding="utf-8")
        return str(path)

    return _write


@pytest.fixture
def models_dir(tmp_path):
    """A voices dir with empty placeholder .onnx files (never actually loaded)."""
    d = tmp_path / "voices"
    d.mkdir()
    for name in ("alto.onnx", "tenor.onnx"):
        (d / name).write_bytes(b"")
    return d


@pytest.fixture
def good_registry(write_registry, models_dir):
    return write_registry({
        "version": 1,
        "voices": {
            "alto_bright": {"provider": "piper",
                            "model_path": str(models_dir / "alto.onnx")},
            "tenor_warm": {"provider": "piper",
                           "model_path": str(models_dir / "tenor.onnx")},
        },
    })


@pytest.fixture
def no_load(monkeypatch):
    """Neuter the piper load step: verify() must never touch a real model."""
    calls = []
    monkeypatch.setattr(tts_client, "_load_local_voice",
                        lambda model: calls.append(model))
    return calls


# ── default_path() ────────────────────────────────────────────────────────────
def test_default_path_honours_env_override(monkeypatch, tmp_path):
    target = tmp_path / "elsewhere.yaml"
    monkeypatch.setenv("VOICE_REGISTRY_PATH", str(target))
    assert voice_registry.default_path() == str(target)


def test_default_path_falls_back_to_a_voices_yaml(monkeypatch):
    monkeypatch.delenv("VOICE_REGISTRY_PATH", raising=False)
    # Either the in-container path or the repo-relative one; both end the same.
    assert voice_registry.default_path().endswith("voices.yaml")


# ── load(): happy path ────────────────────────────────────────────────────────
def test_load_returns_voices_mapping(good_registry):
    voices = voice_registry.load(good_registry)
    assert set(voices) == {"alto_bright", "tenor_warm"}
    assert voices["alto_bright"]["provider"] == "piper"


def test_load_reads_env_path_when_no_argument(good_registry, monkeypatch):
    monkeypatch.setenv("VOICE_REGISTRY_PATH", good_registry)
    assert set(voice_registry.load()) == {"alto_bright", "tenor_warm"}


# ── load(): never raises — each degradation yields {} ─────────────────────────
def test_load_missing_file_returns_empty(tmp_path):
    assert voice_registry.load(str(tmp_path / "nope.yaml")) == {}


def test_load_malformed_yaml_returns_empty(write_registry):
    path = write_registry("version: 1\nvoices: [unclosed\n  : : :\n")
    assert voice_registry.load(path) == {}


def test_load_top_level_list_returns_empty(write_registry):
    path = write_registry(["alto_bright", "tenor_warm"])
    assert voice_registry.load(path) == {}


def test_load_wrong_version_returns_empty(write_registry):
    path = write_registry({"version": 2, "voices": {"a": {"provider": "piper"}}})
    assert voice_registry.load(path) == {}


def test_load_missing_version_returns_empty(write_registry):
    path = write_registry({"voices": {"a": {"provider": "piper"}}})
    assert voice_registry.load(path) == {}


def test_load_missing_voices_block_returns_empty(write_registry):
    assert voice_registry.load(write_registry({"version": 1})) == {}


# ── load(): per-entry skipping is partial, not fatal ──────────────────────────
def test_non_mapping_entry_is_skipped_siblings_survive(write_registry):
    path = write_registry({
        "version": 1,
        "voices": {
            "bogus": "just-a-string",
            "good": {"provider": "piper", "model_path": "/data/voices/x.onnx"},
        },
    })
    voices = voice_registry.load(path)
    assert "bogus" not in voices
    assert set(voices) == {"good"}


def test_entry_without_provider_is_skipped_siblings_survive(write_registry):
    path = write_registry({
        "version": 1,
        "voices": {
            "no_provider": {"model_path": "/data/voices/x.onnx"},
            "good": {"provider": "openai", "voice_id": "nova"},
        },
    })
    voices = voice_registry.load(path)
    assert "no_provider" not in voices
    assert set(voices) == {"good"}


# ── Caching ───────────────────────────────────────────────────────────────────
def test_second_load_hits_the_cache(write_registry, tmp_path):
    path = write_registry({"version": 1, "voices": {"a": {"provider": "piper"}}},
                          name="cached.yaml")
    first = voice_registry.load(path)
    assert set(first) == {"a"}

    # Change the file on disk; a cached load must NOT see it.
    pathlib.Path(path).write_text(
        yaml.safe_dump({"version": 1, "voices": {"b": {"provider": "piper"}}}),
        encoding="utf-8")
    assert set(voice_registry.load(path)) == {"a"}


def test_force_rereads_changed_file(write_registry):
    path = write_registry({"version": 1, "voices": {"a": {"provider": "piper"}}},
                          name="forced.yaml")
    assert set(voice_registry.load(path)) == {"a"}

    pathlib.Path(path).write_text(
        yaml.safe_dump({"version": 1, "voices": {"b": {"provider": "piper"}}}),
        encoding="utf-8")
    assert set(voice_registry.load(path, force=True)) == {"b"}


# ── is_known() / resolve() / names() ──────────────────────────────────────────
def test_is_known_true_for_present_name(good_registry):
    assert voice_registry.is_known("alto_bright", good_registry) is True


def test_is_known_false_for_absent_name(good_registry):
    assert voice_registry.is_known("no_such_voice", good_registry) is False


def test_is_known_touches_no_voice_file(write_registry):
    """The V1 (§8.1) path runs in message-api, which has no /data/voices mount:
    a name lookup must succeed even when the model file does not exist."""
    path = write_registry({
        "version": 1,
        "voices": {"alto_bright": {"provider": "piper",
                                   "model_path": "/data/voices/definitely-absent.onnx"}},
    })
    assert voice_registry.is_known("alto_bright", path) is True


def test_resolve_returns_fragment(good_registry, models_dir):
    fragment = voice_registry.resolve("alto_bright", good_registry)
    assert fragment["provider"] == "piper"
    assert fragment["model_path"] == str(models_dir / "alto.onnx")


def test_resolve_unknown_returns_none(good_registry):
    assert voice_registry.resolve("no_such_voice", good_registry) is None


def test_resolve_returns_a_copy(good_registry):
    """TTSClient.voice_for merges the fragment — mutating it must not poison
    the cached registry for the next caller."""
    first = voice_registry.resolve("alto_bright", good_registry)
    first["model_path"] = "/tmp/hacked.onnx"
    first["extra"] = "injected"

    second = voice_registry.resolve("alto_bright", good_registry)
    assert second["model_path"] != "/tmp/hacked.onnx"
    assert "extra" not in second


def test_names_are_sorted(write_registry):
    path = write_registry({
        "version": 1,
        "voices": {
            "zeta": {"provider": "piper"},
            "alpha": {"provider": "piper"},
            "mid": {"provider": "piper"},
        },
    })
    assert voice_registry.names(path) == ["alpha", "mid", "zeta"]


def test_names_empty_for_broken_registry(tmp_path):
    assert voice_registry.names(str(tmp_path / "missing.yaml")) == []


# ── resolve_model_path() ──────────────────────────────────────────────────────
def test_resolve_model_path_without_voices_dir_is_identity():
    assert voice_registry.resolve_model_path("/data/voices/alto.onnx") == \
        "/data/voices/alto.onnx"


def test_resolve_model_path_rebases_basename(tmp_path):
    out = voice_registry.resolve_model_path("/data/voices/alto.onnx", str(tmp_path))
    assert out == str(tmp_path / "alto.onnx")


def test_resolve_model_path_honours_voices_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICES_DIR", str(tmp_path))
    assert voice_registry.resolve_model_path("/data/voices/alto.onnx") == \
        str(tmp_path / "alto.onnx")


def test_resolve_model_path_argument_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICES_DIR", "/env/dir")
    assert voice_registry.resolve_model_path("/data/voices/alto.onnx", str(tmp_path)) == \
        str(tmp_path / "alto.onnx")


# ── verify() ──────────────────────────────────────────────────────────────────
def test_verify_all_good(good_registry, no_load):
    ok, results = voice_registry.verify(good_registry)
    assert ok is True
    assert len(results) == 2
    assert {name for name, _, _ in results} == {"alto_bright", "tenor_warm"}
    assert all(status == "ok" for _, status, _ in results)
    assert len(no_load) == 2          # the load step really ran, on our stub


def test_verify_missing_model_file_fails(write_registry, no_load, tmp_path):
    path = write_registry({
        "version": 1,
        "voices": {"ghost": {"provider": "piper",
                             "model_path": str(tmp_path / "absent.onnx")}},
    })
    ok, results = voice_registry.verify(path)
    assert ok is False
    assert results[0][0] == "ghost"
    assert results[0][1] == "failed"
    assert "not found" in results[0][2]
    assert no_load == []               # never tried to load a missing file


def test_verify_non_piper_provider_is_skipped_and_still_ok(write_registry,
                                                           models_dir, no_load):
    path = write_registry({
        "version": 1,
        "voices": {
            "cloud": {"provider": "openai", "voice_id": "nova"},
            "local": {"provider": "piper",
                      "model_path": str(models_dir / "alto.onnx")},
        },
    })
    ok, results = voice_registry.verify(path)
    statuses = dict((name, status) for name, status, _ in results)
    assert statuses == {"cloud": "skipped", "local": "ok"}
    assert ok is True                  # a skip must never fail the inventory


def test_verify_piper_without_model_path_fails(write_registry, no_load):
    path = write_registry({
        "version": 1,
        "voices": {"pathless": {"provider": "piper"}},
    })
    ok, results = voice_registry.verify(path)
    assert ok is False
    name, status, detail = results[0]
    assert (name, status) == ("pathless", "failed")
    assert "piper" in detail and "no model_path" in detail


def test_verify_kokoro_without_model_path_names_kokoro(write_registry, no_load):
    """A pathless kokoro entry must name kokoro, not piper.

    kokoro is an alias for the same local .onnx setup (tts_client._BACKENDS), so
    it reaches the same branch as piper. The detail string used to hardcode
    "piper", which pointed a reader at the wrong registry entry when debugging.
    """
    path = write_registry({
        "version": 1,
        "voices": {"pathless_kokoro": {"provider": "kokoro"}},
    })
    ok, results = voice_registry.verify(path)
    assert ok is False
    name, status, detail = results[0]
    assert (name, status) == ("pathless_kokoro", "failed")
    assert "kokoro" in detail
    assert "piper" not in detail


def test_verify_uses_voices_dir_to_rebase(write_registry, models_dir, no_load):
    """Container paths in the registry are rebased for a host/CI run."""
    path = write_registry({
        "version": 1,
        "voices": {"alto_bright": {"provider": "piper",
                                   "model_path": "/data/voices/alto.onnx"}},
    })
    ok, results = voice_registry.verify(path, voices_dir=str(models_dir))
    assert ok is True
    assert results[0][1] == "ok"
    assert no_load == [str(models_dir / "alto.onnx")]


def test_verify_load_failure_is_reported_as_failed(write_registry, models_dir,
                                                   monkeypatch):
    def boom(model):
        raise RuntimeError("corrupt onnx")

    monkeypatch.setattr(tts_client, "_load_local_voice", boom)
    path = write_registry({
        "version": 1,
        "voices": {"alto_bright": {"provider": "piper",
                                   "model_path": str(models_dir / "alto.onnx")}},
    })
    ok, results = voice_registry.verify(path)
    assert ok is False
    assert results[0][1] == "failed"
    assert "RuntimeError" in results[0][2]


def test_verify_empty_registry_returns_false_and_no_results(tmp_path):
    assert voice_registry.verify(str(tmp_path / "missing.yaml")) == (False, [])


# ── main() ────────────────────────────────────────────────────────────────────
def test_main_list_prints_one_name_per_line(good_registry, capsys):
    rc = voice_registry.main(["--registry", good_registry, "--list"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["alto_bright", "tenor_warm"]


def test_main_list_uses_env_registry(good_registry, monkeypatch, capsys):
    monkeypatch.setenv("VOICE_REGISTRY_PATH", good_registry)
    assert voice_registry.main(["--list"]) == 0
    assert capsys.readouterr().out.strip().splitlines() == \
        ["alto_bright", "tenor_warm"]


def test_main_verify_returns_zero_on_good_registry(good_registry, no_load, capsys):
    rc = voice_registry.main(["--registry", good_registry, "--verify"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out
    assert "0 failed" in out


def test_main_verify_returns_one_on_missing_model(write_registry, no_load,
                                                  tmp_path, capsys):
    path = write_registry({
        "version": 1,
        "voices": {"ghost": {"provider": "piper",
                             "model_path": str(tmp_path / "absent.onnx")}},
    })
    rc = voice_registry.main(["--registry", path, "--verify"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "FAILED" in captured.out
    assert "1 failed: ghost" in captured.out


def test_main_verify_returns_one_on_empty_registry(tmp_path, capsys):
    rc = voice_registry.main(["--registry", str(tmp_path / "missing.yaml"),
                              "--verify"])
    assert rc == 1
    assert "EMPTY / UNLOADABLE" in capsys.readouterr().err


def test_main_with_no_action_prints_help(good_registry, capsys):
    assert voice_registry.main(["--registry", good_registry]) == 0
    assert "usage:" in capsys.readouterr().out
