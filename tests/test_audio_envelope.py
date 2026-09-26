"""Tests for app/audio_envelope.py — the audio-driven mouth animation channel."""
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from audio_envelope import DEFAULT_RATE_HZ, compute_envelope, sample_envelope  # noqa: E402


def _write_wav(path, samples, rate=16000, n_channels=1, sampwidth=2):
    """Write a mono/stereo 16-bit PCM WAV from a flat list of int samples."""
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(n_channels)
        wav.setsampwidth(sampwidth)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))


# ── compute_envelope ─────────────────────────────────────────────────────────
def test_a_silent_clip_returns_a_flat_zero_envelope(tmp_path):
    wav_path = tmp_path / "silent.wav"
    _write_wav(wav_path, [0] * 16000)  # 1 second of silence at 16kHz

    values, rate_hz = compute_envelope(wav_path)

    assert rate_hz == DEFAULT_RATE_HZ
    assert values
    assert all(v == 0.0 for v in values)


def test_a_missing_file_returns_an_empty_envelope_not_raise(tmp_path):
    values, rate_hz = compute_envelope(tmp_path / "does_not_exist.wav")

    assert values == []
    assert rate_hz == DEFAULT_RATE_HZ


def test_the_loudest_window_in_a_clip_normalizes_to_one(tmp_path):
    wav_path = tmp_path / "tone.wav"
    # A full-scale square wave — its RMS window should normalize to ~1.0.
    samples = [32000 if i % 2 == 0 else -32000 for i in range(16000)]
    _write_wav(wav_path, samples)

    values, _ = compute_envelope(wav_path)

    assert max(values) == 1.0
    assert all(0.0 <= v <= 1.0 for v in values)


def test_envelope_length_matches_the_requested_rate(tmp_path):
    wav_path = tmp_path / "one_second.wav"
    _write_wav(wav_path, [16000] * 16000, rate=16000)  # 1 second

    values, rate_hz = compute_envelope(wav_path, rate_hz=20)

    # ~20 windows for a 1-second clip at 20Hz (allow off-by-one from truncation).
    assert 19 <= len(values) <= 20
    assert rate_hz == 20


def test_a_quiet_but_non_silent_clip_still_reaches_full_scale(tmp_path):
    # A clip that never gets loud in absolute terms should still normalize
    # its loudest window to 1.0 — a whisper must not always render half-shut.
    wav_path = tmp_path / "quiet.wav"
    _write_wav(wav_path, [100] * 16000)

    values, _ = compute_envelope(wav_path)

    assert max(values) == 1.0


def test_stereo_audio_is_averaged_to_mono(tmp_path):
    wav_path = tmp_path / "stereo.wav"
    # Interleaved L/R samples.
    samples = []
    for _ in range(8000):
        samples.extend([32000, -32000])
    _write_wav(wav_path, samples, n_channels=2)

    values, _ = compute_envelope(wav_path)

    assert values  # did not blow up or return empty on the multi-channel path


def test_a_zero_length_wav_returns_an_empty_envelope(tmp_path):
    wav_path = tmp_path / "empty.wav"
    _write_wav(wav_path, [])

    values, rate_hz = compute_envelope(wav_path)

    assert values == []
    assert rate_hz == DEFAULT_RATE_HZ


# ── sample_envelope ──────────────────────────────────────────────────────────
def test_sample_envelope_reads_the_right_index():
    envelope = [0.1, 0.5, 0.9, 0.3]

    assert sample_envelope(envelope, rate_hz=1, elapsed_s=2.0) == 0.9


def test_sample_envelope_returns_zero_past_the_end():
    envelope = [0.1, 0.5]

    assert sample_envelope(envelope, rate_hz=1, elapsed_s=10.0) == 0.0


def test_sample_envelope_returns_zero_for_negative_elapsed():
    envelope = [0.1, 0.5]

    assert sample_envelope(envelope, rate_hz=1, elapsed_s=-1.0) == 0.0


def test_sample_envelope_returns_zero_for_an_empty_envelope():
    assert sample_envelope([], rate_hz=20, elapsed_s=0.5) == 0.0
