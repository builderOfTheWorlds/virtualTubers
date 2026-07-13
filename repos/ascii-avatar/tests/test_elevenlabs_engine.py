import os
import pytest
from avatar.voice.elevenlabs_engine import ElevenLabsEngine


class TestElevenLabsEngine:
    def test_create(self):
        engine = ElevenLabsEngine(voice_id="test-voice")
        assert engine.sample_rate == 24000

    def test_not_available_without_key(self):
        # Temporarily remove key if set
        key = os.environ.pop("ELEVENLABS_API_KEY", None)
        try:
            engine = ElevenLabsEngine(voice_id="test")
            assert engine.is_available() is False
        finally:
            if key:
                os.environ["ELEVENLABS_API_KEY"] = key
