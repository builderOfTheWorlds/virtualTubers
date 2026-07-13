"""ElevenLabs TTS engine — cloud, opt-in, requires API key."""

from __future__ import annotations

import logging
import os
from typing import Generator

import numpy as np

from avatar.voice.base import TTSEngine, WordTiming

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000


class ElevenLabsEngine(TTSEngine):
    """ElevenLabs cloud TTS. Requires ELEVENLABS_API_KEY env var."""

    def __init__(self, voice_id: str = "") -> None:
        self._voice_id = voice_id
        self._client = None

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    def is_available(self) -> bool:
        return bool(os.environ.get("ELEVENLABS_API_KEY"))

    def _get_client(self):
        if self._client is None:
            try:
                from elevenlabs import ElevenLabs

                self._client = ElevenLabs(
                    api_key=os.environ["ELEVENLABS_API_KEY"]
                )
            except ImportError:
                log.error("elevenlabs package not installed. pip install elevenlabs")
                raise
            except KeyError:
                log.error("ELEVENLABS_API_KEY not set")
                raise
        return self._client

    def synthesize(self, text: str) -> tuple[np.ndarray, list[WordTiming]]:
        client = self._get_client()
        response = client.text_to_speech.convert(
            text=text,
            voice_id=self._voice_id,
            output_format="pcm_24000",
        )
        # Collect audio bytes
        audio_bytes = b"".join(response)
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        duration = len(audio) / self.sample_rate

        # Estimate timings (ElevenLabs streaming API has real timestamps,
        # but the simple convert endpoint does not)
        timings = self._estimate_timings(text, duration)
        return audio, timings

    def stream_synthesize(
        self, text: str
    ) -> Generator[tuple[np.ndarray, WordTiming | None], None, None]:
        # For streaming with timestamps, use the websocket API
        # Fallback: synthesize full then yield chunks
        audio, timings = self.synthesize(text)
        for wt in timings:
            start = int(wt.start * self.sample_rate)
            end = int(wt.end * self.sample_rate)
            yield audio[start:end], wt

    def _estimate_timings(
        self, text: str, duration: float
    ) -> list[WordTiming]:
        words = text.split()
        if not words:
            return []
        total_chars = sum(len(w) for w in words)
        if total_chars == 0:
            return []
        timings = []
        t = 0.0
        for word in words:
            d = (len(word) / total_chars) * duration
            timings.append(WordTiming(word=word, start=t, end=t + d))
            t += d
        return timings
