"""Tests for app/voice_prep_checkpoint.py and its integration into
revoice.prepare_show — resumable replay voice prep. The DB is replaced by an
in-memory store with the same interface as narration_store.CheckpointStore;
LLM and TTS are fakes that count calls."""
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import voice_prep_checkpoint  # noqa: E402
from revoice import prepare_show  # noqa: E402
from voice_prep_checkpoint import (  # noqa: E402
    PrepCheckpoint, build_checkpoint, compute_prep_key, compute_scene_hash,
)

EVENTS = [
    {"type": "user_message", "text": "Fix the flaky test please"},
    {"type": "assistant_text", "text": "On it, boss."},
    {"type": "tool_call", "tool": "Bash", "error": False, "input_summary": "",
     "output_summary": "", "detail_file": None,
     "detail": {"command": "pytest -x", "output": "1 failed"}},
    {"type": "assistant_text", "text": "That should do it."},
]


class MemoryStore:
    """In-memory CheckpointStore double. `fail_on` names a method that raises."""

    def __init__(self, fail_on=None):
        self.rows = {}
        self.fail_on = fail_on
        self.closed = False
        self.cleared = []

    def _maybe_fail(self, name):
        if self.fail_on == name:
            raise RuntimeError(f"db down during {name}")

    def ensure_schema(self, prune_days=None):
        self._maybe_fail("ensure_schema")

    def load_index(self, prep_key):
        self._maybe_fail("load_index")
        return {index: {"scene_hash": r["scene_hash"], "text": r["text"],
                        "has_audio": r["audio"] is not None,
                        "audio_duration_s": r["duration"]}
                for (key, index), r in self.rows.items() if key == prep_key}

    def load_audio(self, prep_key, scene_index):
        self._maybe_fail("load_audio")
        row = self.rows.get((prep_key, scene_index))
        return row["audio"] if row else None

    def save_scene(self, prep_key, scene_index, scene_hash, episode, worker_id,
                   text, audio_bytes, duration):
        self._maybe_fail("save_scene")
        self.rows[(prep_key, scene_index)] = {
            "scene_hash": scene_hash, "text": text, "audio": audio_bytes,
            "duration": duration}

    def clear(self, prep_key):
        self.cleared.append(prep_key)
        for key in [k for k in self.rows if k[0] == prep_key]:
            del self.rows[key]

    def close(self):
        self.closed = True


def _write_wav(path, seconds=1.0, rate=8000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = int(seconds * rate)
        wav.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))


class CountingLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, system_prompt, messages):
        self.calls += 1
        return f"Spoken line number {self.calls}."


class CountingTTS:
    """Writes a real WAV so checkpointed bytes round-trip; `fail_after`
    raises (simulating a crash) once that many lines were synthesized."""

    def __init__(self, fail_after=None, error_every=False):
        self.calls = 0
        self.fail_after = fail_after
        self.error_every = error_every

    def synthesize(self, text, out_wav, speaker="coder"):
        from tts_client import Narration

        if self.error_every:
            raise RuntimeError("tts down")
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise KeyboardInterrupt("container killed mid-prep")
        self.calls += 1
        _write_wav(out_wav, seconds=1.0 + self.calls / 10)
        return Narration(audio_path=Path(out_wav), duration=1.0 + self.calls / 10)


def _checkpoint(store, key="k1"):
    cp = PrepCheckpoint(store, key, "ep1", "roundtable")
    cp.open()
    return cp


# ── hashing ──────────────────────────────────────────────────────────────────

def test_prep_key_ignores_transport_only_settings():
    """Switching CPU -> GPU service mid-prep must still resume: base_url,
    use_cuda, cpu_threads, remote_fallback don't change the audio."""
    cpu = {"provider": "piper", "model_path": "/v/a.onnx"}
    gpu = {**cpu, "base_url": "http://tts-gpu:5000", "use_cuda": True,
           "cpu_threads": 2, "remote_fallback": False}
    assert compute_prep_key("ep", "GM", cpu) == compute_prep_key("ep", "GM", gpu)


@pytest.mark.parametrize("change", [
    {"episode": "ep2"}, {"worker_name": "OTHER"}, {"speed": 2.0}, {"skip_llm": True},
    {"voice_config": {"provider": "piper", "model_path": "/v/b.onnx"}},
    {"voice_config": {"provider": "piper", "model_path": "/v/a.onnx", "rate": 1.2}},
])
def test_prep_key_changes_with_anything_that_changes_the_show(change):
    base = {"episode": "ep", "worker_name": "GM", "speed": 1.0, "skip_llm": False,
            "voice_config": {"provider": "piper", "model_path": "/v/a.onnx"}}
    changed = {**base, **change}
    assert compute_prep_key(**base) != compute_prep_key(**changed)


def test_scene_hash_ignores_prep_outputs_but_not_content():
    scene = {"kind": "boss_talk", "speaker": "boss", "events": [{"text": "hi"}]}
    annotated = {**scene, "narration": "x", "audio": object(), "target_duration": 3}
    assert compute_scene_hash(scene, words=10) == compute_scene_hash(annotated, words=10)
    edited = {**scene, "events": [{"text": "hello"}]}
    assert compute_scene_hash(scene, words=10) != compute_scene_hash(edited, words=10)
    assert compute_scene_hash(scene, words=10) != compute_scene_hash(scene, words=11)
    assert compute_scene_hash(scene, voice_name="a") != compute_scene_hash(scene, voice_name="b")


# ── prepare_show resume ──────────────────────────────────────────────────────

def test_prepare_show_resumes_after_crash_without_redoing_scenes(tmp_path):
    store = MemoryStore()
    script = {"events": EVENTS}
    llm1, tts1 = CountingLLM(), CountingTTS(fail_after=2)
    with pytest.raises(KeyboardInterrupt):
        prepare_show(script, llm1, tts1, tmp_path / "run1", checkpoint=_checkpoint(store))
    assert tts1.calls == 2 and len(store.rows) == 2  # two scenes persisted before the crash

    llm2, tts2 = CountingLLM(), CountingTTS()
    cp2 = _checkpoint(store)
    progress = []
    show = prepare_show(script, llm2, tts2, tmp_path / "run2", checkpoint=cp2,
                        progress=progress.append)
    total = len(show)
    assert cp2.restored == 2
    assert tts2.calls == total - 2 and llm2.calls == total - 2
    # restored scenes carry the ORIGINAL text and a real, readable WAV
    assert show[0]["narration"] == "Spoken line number 1."
    assert show[0]["audio"].audio_path.read_bytes() == store.rows[("k1", 0)]["audio"]
    assert show[0]["audio"].audio_path.parent == tmp_path / "run2"
    assert all(scene["audio"] is not None for scene in show)
    # progress keeps the "scene i/N:" shape the control panel parses
    assert progress[0].startswith(f"scene 1/{total}: restored")
    assert any(m.startswith(f"scene 3/{total}: writing") for m in progress)


def test_prepare_show_regenerates_only_changed_scenes(tmp_path):
    store = MemoryStore()
    prepare_show({"events": EVENTS}, CountingLLM(), CountingTTS(), tmp_path / "a",
                 checkpoint=_checkpoint(store))
    edited = [dict(e) for e in EVENTS]
    edited[0]["text"] = "Fix the OTHER flaky test please"
    tts = CountingTTS()
    cp = _checkpoint(store)
    show = prepare_show({"events": edited}, CountingLLM(), tts, tmp_path / "b", checkpoint=cp)
    assert tts.calls == 1 and cp.restored == len(show) - 1


def test_prepare_show_retries_scenes_whose_tts_failed(tmp_path):
    """A silent scene checkpointed because TTS failed must be retried on
    resume, not restored as silent forever."""
    store = MemoryStore()
    prepare_show({"events": EVENTS}, CountingLLM(), CountingTTS(error_every=True),
                 tmp_path / "a", checkpoint=_checkpoint(store))
    assert all(r["audio"] is None for r in store.rows.values())
    tts = CountingTTS()
    show = prepare_show({"events": EVENTS}, CountingLLM(), tts, tmp_path / "b",
                        checkpoint=_checkpoint(store))
    assert tts.calls == len(show)
    assert all(scene["audio"] is not None for scene in show)


def test_prepare_show_db_failure_mid_prep_keeps_preparing(tmp_path, capsys):
    store = MemoryStore(fail_on="save_scene")
    cp = _checkpoint(store)
    tts = CountingTTS()
    show = prepare_show({"events": EVENTS}, CountingLLM(), tts, tmp_path, checkpoint=cp)
    assert tts.calls == len(show)  # the whole show still got prepared
    assert cp.enabled is False
    assert capsys.readouterr().err.count("event=disabled") == 1  # logged once, not per scene


def test_open_failure_disables_checkpoint(tmp_path):
    cp = PrepCheckpoint(MemoryStore(fail_on="load_index"), "k", "ep", "w")
    assert cp.open() == 0 and cp.enabled is False
    assert cp.lookup(0, "h", tmp_path / "x.wav") is None


def test_load_audio_failure_falls_back_to_regenerating(tmp_path):
    store = MemoryStore()
    prepare_show({"events": EVENTS}, CountingLLM(), CountingTTS(), tmp_path / "a",
                 checkpoint=_checkpoint(store))
    store.fail_on = "load_audio"
    tts = CountingTTS()
    show = prepare_show({"events": EVENTS}, CountingLLM(), tts, tmp_path / "b",
                        checkpoint=_checkpoint(store))
    assert tts.calls == len(show)


def test_prepare_show_without_checkpoint_unchanged(tmp_path):
    tts = CountingTTS()
    show = prepare_show({"events": EVENTS}, CountingLLM(), tts, tmp_path)
    assert tts.calls == len(show)


# ── build_checkpoint config gate ─────────────────────────────────────────────

@pytest.mark.parametrize("env,cfg", [("0", None), ("false", None), (None, False), (None, "off")])
def test_build_checkpoint_disabled_by_env_or_config(env, cfg, monkeypatch):
    if env is None:
        monkeypatch.delenv("VOICE_PREP_CHECKPOINT", raising=False)
    else:
        monkeypatch.setenv("VOICE_PREP_CHECKPOINT", env)
    voice = {"provider": "piper"}
    if cfg is not None:
        voice["checkpoint"] = cfg
    assert build_checkpoint("ep", "GM", {"voice": voice}, store_factory=MemoryStore) is None


def test_build_checkpoint_default_on_and_opens(monkeypatch):
    monkeypatch.delenv("VOICE_PREP_CHECKPOINT", raising=False)
    cp = build_checkpoint("ep", "GM", {"voice": {"provider": "piper"}},
                          worker_id="roundtable", store_factory=MemoryStore)
    assert isinstance(cp, PrepCheckpoint) and cp.enabled and cp.worker_id == "roundtable"


def test_build_checkpoint_none_when_store_unavailable(monkeypatch):
    monkeypatch.delenv("VOICE_PREP_CHECKPOINT", raising=False)
    monkeypatch.setattr(voice_prep_checkpoint.narration_store, "available", lambda: False)
    assert build_checkpoint("ep", "GM", {"voice": {"provider": "piper"}}) is None


def test_build_checkpoint_none_and_closed_when_open_fails(monkeypatch):
    monkeypatch.delenv("VOICE_PREP_CHECKPOINT", raising=False)
    stores = []

    def factory():
        stores.append(MemoryStore(fail_on="ensure_schema"))
        return stores[-1]
    assert build_checkpoint("ep", "GM", {"voice": {}}, store_factory=factory) is None
    assert stores[0].closed


def test_clear_failure_is_swallowed(capsys):
    store = MemoryStore()
    store.clear = lambda key: (_ for _ in ()).throw(RuntimeError("down"))
    PrepCheckpoint(store, "k", "ep", "w").clear()
    assert "clear_failed" in capsys.readouterr().err
