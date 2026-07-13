"""Kokoro TTS engine — local, fast, native phoneme output."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Generator

import numpy as np

from avatar.voice.base import TTSEngine, WordTiming

log = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = Path.home() / ".cache" / "ascii-avatar" / "models"
DEFAULT_VOICE = "af_bella"
SAMPLE_RATE = 24000


class KokoroEngine(TTSEngine):
    """Kokoro-ONNX TTS engine.

    Lazy-loads the model on first synthesis call.
    Falls back to proportional timing if native phoneme output unavailable.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        voice: str = DEFAULT_VOICE,
    ) -> None:
        self._model_dir = Path(model_path) if model_path else DEFAULT_MODEL_DIR
        self._voice = voice
        self._model = None  # Lazy loaded

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    def is_available(self) -> bool:
        model_file = self._model_dir / "kokoro-v1.0.onnx"
        voices_file = self._model_dir / "voices-v1.0.bin"
        return model_file.exists() and voices_file.exists()

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from kokoro_onnx import Kokoro

            model_file = str(self._model_dir / "kokoro-v1.0.onnx")
            voices_file = str(self._model_dir / "voices-v1.0.bin")
            self._model = Kokoro(model_file, voices_file)
            log.info("Kokoro model loaded from %s", self._model_dir)
        except Exception as e:
            log.error(
                "Failed to load Kokoro model: %s. "
                "Run scripts/install.sh to download models.",
                e,
            )
            raise

    def synthesize(self, text: str) -> tuple[np.ndarray, list[WordTiming]]:
        self._load_model()
        assert self._model is not None

        # Kokoro returns (samples, sample_rate) or yields (gs, ps, audio)
        samples, sr = self._model.create(text, voice=self._voice, speed=1.0)
        duration = len(samples) / sr
        timings = self.estimate_word_timings(text, duration)
        return samples, timings

    def stream_synthesize(
        self, text: str
    ) -> Generator[tuple[np.ndarray, WordTiming | None], None, None]:
        self._load_model()
        assert self._model is not None

        try:
            # Try streaming API if available
            for gs, ps, audio in self._model.create(
                text, voice=self._voice, speed=1.0, is_stream=True
            ):
                wt = WordTiming(word=gs, start=0.0, end=0.0) if gs else None
                yield audio, wt
        except TypeError:
            # Fallback to non-streaming
            audio, timings = self.synthesize(text)
            for wt in timings:
                start_sample = int(wt.start * self.sample_rate)
                end_sample = int(wt.end * self.sample_rate)
                chunk = audio[start_sample:end_sample]
                yield chunk, wt

    def estimate_word_timings(
        self, text: str, total_duration: float
    ) -> list[WordTiming]:
        """Estimate word timings proportionally by character count."""
        words = text.split()
        if not words:
            return []

        total_chars = sum(len(w) for w in words)
        if total_chars == 0:
            return []

        timings = []
        current_time = 0.0
        for word in words:
            word_duration = (len(word) / total_chars) * total_duration
            timings.append(WordTiming(
                word=word,
                start=current_time,
                end=current_time + word_duration,
            ))
            current_time += word_duration

        return timings
