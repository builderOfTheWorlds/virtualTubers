import numpy as np
import pytest

from avatar.voice.base import TTSEngine, WordTiming
from avatar.voice.audio_player import AudioPlayer


class TestWordTiming:
    def test_create(self):
        wt = WordTiming(word="hello", start=0.0, end=0.5)
        assert wt.word == "hello"

    def test_duration(self):
        wt = WordTiming(word="hello", start=0.1, end=0.6)
        assert wt.duration == pytest.approx(0.5)


class TestAudioPlayer:
    def test_create(self):
        player = AudioPlayer()
        assert player.is_playing is False

    def test_play_silence(self):
        player = AudioPlayer()
        # 0.1s of silence at 24000 Hz
        audio = np.zeros(2400, dtype=np.float32)
        player.play(audio, sample_rate=24000)
        assert player.is_playing is True
        player.stop()

    def test_stop_when_not_playing(self):
        player = AudioPlayer()
        player.stop()  # Should not raise

    def test_word_callbacks(self):
        player = AudioPlayer()
        callbacks = []
        timings = [
            WordTiming("hello", 0.0, 0.05),
            WordTiming("world", 0.05, 0.1),
        ]
        audio = np.zeros(2400, dtype=np.float32)  # 0.1s
        player.play(
            audio,
            sample_rate=24000,
            word_timings=timings,
            on_word=lambda wt: callbacks.append(wt.word),
        )
        import time
        time.sleep(0.3)
        player.stop()
        # Callbacks should have fired
        assert "hello" in callbacks
