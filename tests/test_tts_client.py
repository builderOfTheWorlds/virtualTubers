"""Tests for app/tts_client.py — provider-switchable TTS with measured
durations. Backends are mocked; no real synthesis or network in unit tests."""
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import tts_client  # noqa: E402
from tts_client import TTSClient, TTSError, build_tts_client, wav_duration  # noqa: E402


def write_wav(path, seconds=1.0, rate=8000):
    """A real (silent) WAV of a known duration."""
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))


# ── build_tts_client ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("voice", [
    {},                            # no voice section at all
    {"voice": {}},                 # empty section
    {"voice": {"provider": "null"}},
    {"voice": {"provider": "none"}},
])
def test_build_returns_none_when_voice_disabled(voice, monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    assert build_tts_client(voice) is None


def test_build_unknown_provider_raises(monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    with pytest.raises(TTSError):
        build_tts_client({"voice": {"provider": "gramophone"}})


def test_build_env_var_overrides_config(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "piper")
    client = build_tts_client({"voice": {"provider": "null"}})
    assert client is not None and client.provider == "piper"


def test_build_accepts_bare_voice_section(monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    assert build_tts_client({"provider": "piper"}).provider == "piper"


# ── duration measurement ─────────────────────────────────────────────────────

def test_wav_duration_measured_from_header(tmp_path):
    p = tmp_path / "x.wav"
    write_wav(p, seconds=2.5)
    assert wav_duration(p) == pytest.approx(2.5, abs=0.01)


def test_wav_duration_garbage_without_ffprobe_raises(tmp_path, monkeypatch):
    p = tmp_path / "x.wav"
    p.write_bytes(b"not a wav at all")
    monkeypatch.setattr(tts_client.shutil, "which", lambda name: None)
    with pytest.raises(TTSError):
        wav_duration(p)


# ── speaker voice resolution ─────────────────────────────────────────────────

def test_voice_for_merges_speaker_overrides():
    client = TTSClient({
        "provider": "piper",
        "model_path": "/data/voices/coder.onnx",
        "speakers": {"boss": {"model_path": "/data/voices/boss.onnx"}},
    })
    assert client.voice_for("coder")["model_path"] == "/data/voices/coder.onnx"
    assert client.voice_for("boss")["model_path"] == "/data/voices/boss.onnx"
    # unknown speaker falls back to the base voice, and the speakers table
    # itself never leaks into a backend's config
    assert client.voice_for("narrator")["model_path"] == "/data/voices/coder.onnx"
    assert "speakers" not in client.voice_for("boss")


# ── synthesize ───────────────────────────────────────────────────────────────

def test_synthesize_empty_text_raises(tmp_path):
    client = TTSClient({"provider": "piper"})
    with pytest.raises(TTSError):
        client.synthesize("   ", tmp_path / "out.wav")


def test_synthesize_piper_returns_measured_duration(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")

    def fake_run(cmd, **kwargs):
        out_index = cmd.index("--output_file") + 1
        write_wav(Path(cmd[out_index]), seconds=1.25)

        class Proc:
            returncode = 0
            stderr = ""
        return Proc()

    monkeypatch.setattr(tts_client.shutil, "which", lambda name: "piper")
    monkeypatch.setattr(tts_client.subprocess, "run", fake_run)
    client = TTSClient({"provider": "piper", "model_path": str(model)})
    narration = client.synthesize("hello stream", tmp_path / "out.wav")
    assert narration.duration == pytest.approx(1.25, abs=0.01)
    assert narration.audio_path == tmp_path / "out.wav"


def test_synthesize_piper_missing_model_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_client.shutil, "which", lambda name: "piper")
    client = TTSClient({"provider": "piper", "model_path": str(tmp_path / "gone.onnx")})
    with pytest.raises(TTSError, match="model not found"):
        client.synthesize("hi", tmp_path / "out.wav")


def test_synthesize_piper_cli_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_client.shutil, "which", lambda name: None)
    monkeypatch.setattr(tts_client.Path, "exists", lambda self: False)
    client = TTSClient({"provider": "piper"})
    with pytest.raises(TTSError, match="piper CLI not found"):
        client.synthesize("hi", tmp_path / "out.wav")


def test_synthesize_backend_failure_raises(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")

    def fake_run(cmd, **kwargs):
        class Proc:
            returncode = 1
            stderr = "boom"
        return Proc()

    monkeypatch.setattr(tts_client.shutil, "which", lambda name: "piper")
    monkeypatch.setattr(tts_client.subprocess, "run", fake_run)
    client = TTSClient({"provider": "piper", "model_path": str(model)})
    with pytest.raises(TTSError, match="boom"):
        client.synthesize("hi", tmp_path / "out.wav")


def test_openai_without_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TTSClient({"provider": "openai"})
    # raises TTSError whether the package is missing or the key is unset
    with pytest.raises(TTSError):
        client.synthesize("hi", tmp_path / "out.wav")
