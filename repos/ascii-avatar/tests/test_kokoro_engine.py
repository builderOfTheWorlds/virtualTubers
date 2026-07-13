import pytest
from avatar.voice.kokoro_engine import KokoroEngine


class TestKokoroEngine:
    def test_create(self):
        engine = KokoroEngine()
        assert engine.sample_rate == 24000

    def test_is_available_without_model(self):
        engine = KokoroEngine(model_path="/nonexistent/path")
        assert engine.is_available() is False

    def test_estimate_word_timings(self):
        engine = KokoroEngine()
        timings = engine.estimate_word_timings("hello world", total_duration=1.0)
        assert len(timings) == 2
        assert timings[0].word == "hello"
        assert timings[1].word == "world"
        assert timings[0].start == pytest.approx(0.0)
        assert timings[1].end == pytest.approx(1.0, abs=0.01)

    def test_estimate_preserves_order(self):
        engine = KokoroEngine()
        timings = engine.estimate_word_timings(
            "one two three four", total_duration=2.0
        )
        for i in range(len(timings) - 1):
            assert timings[i].end <= timings[i + 1].start + 0.001

    def test_estimate_empty_text(self):
        engine = KokoroEngine()
        timings = engine.estimate_word_timings("", total_duration=1.0)
        assert timings == []

    @pytest.mark.skipif(
        not KokoroEngine().is_available(),
        reason="Kokoro model not installed",
    )
    def test_synthesize_real(self):
        engine = KokoroEngine()
        audio, timings = engine.synthesize("Hello world")
        assert len(audio) > 0
        assert len(timings) >= 1
