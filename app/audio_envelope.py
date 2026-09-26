#!/usr/bin/env python3
"""
audio_envelope.py
Computes a coarse amplitude envelope from a WAV file for audio-driven
mouth animation (docs/avatar_emotion_design.md). Whoever starts playback
of a synthesized line (replay.py's Performer, campaign/renderer.py's
SceneRenderer) calls compute_envelope() once on the just-synthesized WAV
and writes the result into agent_state.json alongside the bubble/emotion,
so the avatar pane process — which never touches the audio itself — can
sample "how loud is the line right now" purely from the state file, the
same "the state file is the whole IPC" convention agent_state.py already
uses for expression/bubble.

Deliberately stdlib + numpy only (no librosa/scipy): a coarse RMS-per-
window envelope at ~20 samples/second is plenty for a low-poly codec
mouth quad — anything finer is invisible at that vertex budget.
"""
import logging
import wave

import numpy as np

log = logging.getLogger(__name__)

#: Samples per second of envelope data. 20Hz means one mouth-shape update
#: every 50ms — smooth enough to read as continuous motion, coarse enough
#: that even a multi-minute line stays a small (~1200-float) JSON array.
DEFAULT_RATE_HZ = 20


def compute_envelope(wav_path, rate_hz=DEFAULT_RATE_HZ):
    """Return (values, rate_hz): `values` is a list of floats in 0..1, one
    per 1/rate_hz-second window, each the window's RMS amplitude normalized
    against the loudest window in the whole clip (so a quiet line still
    reaches mouth_open=1.0 rather than always looking half-shut).

    Best-effort: any failure (bad/missing WAV, zero-length audio, an
    unsupported sample format) returns ([], rate_hz) rather than raising —
    audio must never take the avatar pane down, matching audio_player.py's
    own "every failure mode is soft" contract. An empty envelope means the
    caller falls back to the heuristic (non-audio-driven) mouth cycling.
    """
    try:
        with wave.open(str(wav_path), "rb") as wav:
            rate = wav.getframerate()
            n_channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            n_frames = wav.getnframes()
            raw = wav.readframes(n_frames)
    except (wave.Error, EOFError, OSError) as exc:
        log.warning("audio_envelope: could not read %s (%r) — no envelope", wav_path, exc)
        return [], rate_hz

    if not raw or rate <= 0 or sample_width not in (1, 2, 4):
        return [], rate_hz

    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[sample_width]
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if sample_width == 1:
        samples -= 128.0  # WAV's 8-bit PCM is unsigned, centered at 128

    if n_channels > 1:
        usable = len(samples) - (len(samples) % n_channels)
        samples = samples[:usable].reshape(-1, n_channels).mean(axis=1)

    if samples.size == 0:
        return [], rate_hz

    window = max(1, int(rate / rate_hz))
    n_windows = max(1, len(samples) // window)
    trimmed = samples[: n_windows * window].reshape(n_windows, window)
    rms = np.sqrt(np.mean(trimmed.astype(np.float64) ** 2, axis=1))

    peak = float(rms.max())
    if peak <= 1e-9:
        return [0.0] * n_windows, rate_hz  # silent clip: flat closed mouth throughout
    normalized = np.clip(rms / peak, 0.0, 1.0)
    return normalized.tolist(), rate_hz


def sample_envelope(envelope, rate_hz, elapsed_s):
    """Envelope value at `elapsed_s` seconds into playback. 0.0 for an
    empty envelope, a negative elapsed time, or once elapsed_s has run
    past the envelope's own length (line finished — closed mouth)."""
    if not envelope or elapsed_s < 0:
        return 0.0
    index = int(elapsed_s * rate_hz)
    if index >= len(envelope):
        return 0.0
    return float(envelope[index])


__all__ = ["DEFAULT_RATE_HZ", "compute_envelope", "sample_envelope"]
