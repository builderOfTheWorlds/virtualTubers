"""Abstract TTS engine interface and shared types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator

import numpy as np


@dataclass
class WordTiming:
    word: str
    start: float  # seconds
    end: float    # seconds

    @property
    def duration(self) -> float:
        return self.end - self.start


class TTSEngine(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> tuple[np.ndarray, list[WordTiming]]:
        """Synthesize text to audio array + word timings."""
        ...

    @abstractmethod
    def stream_synthesize(
        self, text: str
    ) -> Generator[tuple[np.ndarray, WordTiming | None], None, None]:
        """Stream synthesis — yields (audio_chunk, optional word_timing)."""
        ...

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this engine is ready (model loaded, API key present, etc)."""
        ...
