"""Piper TTS engine — ultra-lightweight fallback. GPL-3.0 licensed."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Generator

import numpy as np

from avatar.voice.base import TTSEngine, WordTiming

log = logging.getLogger(__name__)

SAMPLE_RATE = 22050


class PiperEngine(TTSEngine):
    """Piper TTS fallback engine. Requires piper-tts package and model file."""

    def __init__(self, model_path: str | Path | None = None) -> None:
        self._model_path = Path(model_path) if model_path else None
        self._voice = None

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    def is_available(self) -> bool:
        if self._model_path is None:
            return False
        try:
            import piper  # noqa: F401
            return self._model_path.exists()
        except ImportError:
            return False

    def _load(self):
        if self._voice is not None:
            return
        from piper import PiperVoice
        self._voice = PiperVoice.load(str(self._model_path))

    def synthesize(self, text: str) -> tuple[np.ndarray, list[WordTiming]]:
        self._load()
        assert self._voice is not None
        audio_bytes = b""
        for chunk in self._voice.synthesize_stream_raw(text):
            audio_bytes += chunk
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        duration = len(audio) / self.sample_rate
        timings = self._estimate_timings(text, duration)
        return audio, timings

    def stream_synthesize(
        self, text: str
    ) -> Generator[tuple[np.ndarray, WordTiming | None], None, None]:
        audio, timings = self.synthesize(text)
        for wt in timings:
            start = int(wt.start * self.sample_rate)
            end = int(wt.end * self.sample_rate)
            yield audio[start:end], wt

    def _estimate_timings(self, text: str, duration: float) -> list[WordTiming]:
        words = text.split()
        if not words:
            return []
        total = sum(len(w) for w in words)
        if total == 0:
            return []
        timings, t = [], 0.0
        for w in words:
            d = (len(w) / total) * duration
            timings.append(WordTiming(word=w, start=t, end=t + d))
            t += d
        return timings
