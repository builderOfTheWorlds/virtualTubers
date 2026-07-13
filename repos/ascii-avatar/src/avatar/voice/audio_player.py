"""Non-blocking audio playback with word-timing callbacks."""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

from avatar.voice.base import WordTiming


class AudioPlayer:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(
        self,
        audio: np.ndarray,
        sample_rate: int,
        word_timings: list[WordTiming] | None = None,
        on_word: Callable[[WordTiming], None] | None = None,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        self.stop()
        self._stop_event.clear()
        self._playing = True

        self._thread = threading.Thread(
            target=self._play_thread,
            args=(audio, sample_rate, word_timings or [], on_word, on_complete),
            daemon=True,
        )
        self._thread.start()

    def _play_thread(
        self,
        audio: np.ndarray,
        sample_rate: int,
        word_timings: list[WordTiming],
        on_word: Callable[[WordTiming], None] | None,
        on_complete: Callable[[], None] | None,
    ) -> None:
        try:
            # Start playback
            sd.play(audio, samplerate=sample_rate)

            # Fire word callbacks at the right times
            if on_word and word_timings:
                start_time = time.monotonic()
                for wt in word_timings:
                    if self._stop_event.is_set():
                        break
                    wait = wt.start - (time.monotonic() - start_time)
                    if wait > 0:
                        self._stop_event.wait(timeout=wait)
                    if not self._stop_event.is_set():
                        on_word(wt)

            # Wait for playback to finish
            if not self._stop_event.is_set():
                sd.wait()

            if on_complete and not self._stop_event.is_set():
                on_complete()
        except Exception:
            pass  # Audio device unavailable — graceful degradation
        finally:
            self._playing = False

    def stop(self) -> None:
        self._stop_event.set()
        sd.stop()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._playing = False
