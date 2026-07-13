from avatar.voice.piper_engine import PiperEngine


class TestPiperEngine:
    def test_create(self):
        engine = PiperEngine()
        assert engine.sample_rate == 22050

    def test_not_available_without_model(self):
        engine = PiperEngine(model_path="/nonexistent")
        assert engine.is_available() is False
