"""Tests for app/tts_client.py — provider-switchable TTS with measured
durations. Backends are mocked; no real synthesis or network in unit tests."""
import struct
import sys
import wave
from pathlib import Path

import httpx
import piper.voice
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


def write_wav_into(wav_file, seconds=1.25, rate=8000):
    """Same as write_wav, but onto an already-open wave.Wave_write (what
    PiperVoice.synthesize_wav is handed)."""
    frames = int(seconds * rate)
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(rate)
    wav_file.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))


class FakeVoice:
    """Stand-in for piper.voice.PiperVoice: writes a known-duration silent
    WAV instead of doing real synthesis. `load_calls` (a list passed in per
    test, not a class attribute — avoids state leaking between tests) records
    every model path PiperVoice.load() was called with."""

    def __init__(self, model_path):
        self.model_path = model_path

    def synthesize_wav(self, text, wav_file, syn_config=None, **kwargs):
        self.last_syn_config = syn_config
        write_wav_into(wav_file)


def make_fake_voice_class(load_calls):
    """A FakeVoice subclass bound to this test's own load_calls list."""
    class _FakeVoice(FakeVoice):
        @classmethod
        def load(cls, model_path):
            load_calls.append(model_path)
            return cls(model_path)
    return _FakeVoice


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


def test_synthesize_piper_local_returns_measured_duration(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")
    monkeypatch.setattr(tts_client, "_LOCAL_VOICES", {})
    monkeypatch.setattr(piper.voice, "PiperVoice", make_fake_voice_class([]))

    client = TTSClient({"provider": "piper", "model_path": str(model)})
    narration = client.synthesize("hello stream", tmp_path / "out.wav")
    assert narration.duration == pytest.approx(1.25, abs=0.01)
    assert narration.audio_path == tmp_path / "out.wav"


def test_piper_local_loads_model_only_once_across_scenes(tmp_path, monkeypatch):
    """The whole point of the single-session change: PiperVoice.load() must
    be paid once per model, not once per synthesize() call."""
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")
    load_calls = []
    monkeypatch.setattr(tts_client, "_LOCAL_VOICES", {})
    monkeypatch.setattr(piper.voice, "PiperVoice", make_fake_voice_class(load_calls))

    client = TTSClient({"provider": "piper", "model_path": str(model)})
    client.synthesize("first line", tmp_path / "a.wav")
    client.synthesize("second line", tmp_path / "b.wav")
    assert len(load_calls) == 1


def test_piper_local_rate_maps_to_inverse_length_scale(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")
    monkeypatch.setattr(tts_client, "_LOCAL_VOICES", {})
    FakeCls = make_fake_voice_class([])
    monkeypatch.setattr(piper.voice, "PiperVoice", FakeCls)

    client = TTSClient({"provider": "piper", "model_path": str(model), "rate": 2.0})
    client.synthesize("hi", tmp_path / "out.wav")
    voice_instance = next(iter(tts_client._LOCAL_VOICES.values()))
    assert voice_instance.last_syn_config.length_scale == pytest.approx(0.5)


def test_synthesize_piper_local_missing_model_raises(tmp_path):
    client = TTSClient({"provider": "piper", "model_path": str(tmp_path / "gone.onnx")})
    with pytest.raises(TTSError, match="model not found"):
        client.synthesize("hi", tmp_path / "out.wav")


def test_piper_local_missing_package_raises_friendly_error(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")
    monkeypatch.setattr(tts_client, "_LOCAL_VOICES", {})
    monkeypatch.setitem(sys.modules, "piper.voice", None)
    client = TTSClient({"provider": "piper", "model_path": str(model)})
    with pytest.raises(TTSError, match="piper-tts package not installed"):
        client.synthesize("hi", tmp_path / "out.wav")


def test_piper_local_synthesis_failure_propagates(tmp_path, monkeypatch):
    class BoomVoice(FakeVoice):
        @classmethod
        def load(cls, model_path):
            return cls(model_path)

        def synthesize_wav(self, *a, **k):
            raise RuntimeError("boom")

    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")
    monkeypatch.setattr(tts_client, "_LOCAL_VOICES", {})
    monkeypatch.setattr(piper.voice, "PiperVoice", BoomVoice)
    client = TTSClient({"provider": "piper", "model_path": str(model)})
    with pytest.raises(RuntimeError, match="boom"):
        client.synthesize("hi", tmp_path / "out.wav")


# ── piper: remote mode (voice.base_url / TTS_BASE_URL) ──────────────────────

def test_piper_remote_posts_text_and_writes_response(tmp_path, monkeypatch):
    captured = {}

    class FakeResponse:
        content = b"fake-wav-bytes"

        def raise_for_status(self):
            pass

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    client = TTSClient({
        "provider": "piper",
        "model_path": "/data/voices/en_US-lessac-low.onnx",
        "base_url": "http://tts-box:5000",
    })
    out = tmp_path / "out.wav"
    client._backend("hello stream", out, client.voice_for("coder"))
    assert out.read_bytes() == b"fake-wav-bytes"
    assert captured["url"] == "http://tts-box:5000/synthesize"
    assert captured["json"]["text"] == "hello stream"
    assert captured["json"]["voice"] == "en_US-lessac-low"


def test_piper_remote_raises_on_http_error(tmp_path, monkeypatch):
    request = httpx.Request("POST", "http://tts-box:5000/synthesize")

    class FakeResponse:
        status_code = 500
        text = "model not loaded"

        def raise_for_status(self):
            raise httpx.HTTPStatusError("boom", request=request, response=self)

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse())
    client = TTSClient({"provider": "piper", "base_url": "http://tts-box:5000"})
    with pytest.raises(TTSError, match="model not loaded"):
        client._backend("hi", tmp_path / "out.wav", client.voice_for("coder"))


def test_build_tts_base_url_env_override(monkeypatch):
    monkeypatch.setenv("TTS_BASE_URL", "http://remote-tts:5000")
    client = build_tts_client({"voice": {"provider": "piper", "model_path": "/x.onnx"}})
    assert client.voice_for("coder")["base_url"] == "http://remote-tts:5000"


def test_openai_without_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TTSClient({"provider": "openai"})
    # raises TTSError whether the package is missing or the key is unset
    with pytest.raises(TTSError):
        client.synthesize("hi", tmp_path / "out.wav")


# ── fake (testing-only instant synthesis) ───────────────────────────────────

def test_fake_is_not_none_unlike_null(monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    # "fake" must stay a real client (duets treat None as a hard refusal),
    # unlike "null"/"none", which build_tts_client turns into None.
    assert build_tts_client({"voice": {"provider": "fake"}}) is not None
    assert build_tts_client({"voice": {"provider": "null"}}) is None


def test_fake_never_shells_out(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("fake provider must never spawn a subprocess")
    monkeypatch.setattr(tts_client.subprocess, "run", explode)
    client = TTSClient({"provider": "fake"})
    narration = client.synthesize("hello stream", tmp_path / "out.wav")
    assert narration.audio_path.exists()


def test_fake_duration_scales_with_word_count(tmp_path):
    client = TTSClient({"provider": "fake"})
    short = client.synthesize("hi", tmp_path / "short.wav")
    long = client.synthesize(" ".join(["word"] * 30), tmp_path / "long.wav")
    assert long.duration > short.duration


def test_fake_duration_floored_for_very_short_text(tmp_path):
    client = TTSClient({"provider": "fake"})
    narration = client.synthesize("hi", tmp_path / "out.wav")
    assert narration.duration >= tts_client.FAKE_MIN_DURATION_S
