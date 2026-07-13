from avatar.personas import Persona, get_persona, list_personas, DEFAULT_PERSONA


class TestPersona:
    def test_ghost_exists(self):
        p = get_persona("ghost")
        assert p.name == "ghost"
        assert p.frames == "layered2d"
        assert p.voice_engine == "kokoro"

    def test_oracle_exists(self):
        p = get_persona("oracle")
        assert p.voice_engine == "kokoro"

    def test_spectre_exists(self):
        p = get_persona("spectre")
        assert p.voice_engine == "elevenlabs"

    def test_unknown_persona_raises(self):
        import pytest
        with pytest.raises(KeyError):
            get_persona("nonexistent")

    def test_list_personas(self):
        names = list_personas()
        assert "ghost" in names
        assert "oracle" in names
        assert "spectre" in names

    def test_default_persona(self):
        assert DEFAULT_PERSONA == "ghost"

    def test_frame_rate_modifier(self):
        ghost = get_persona("ghost")
        oracle = get_persona("oracle")
        assert ghost.frame_rate_modifier == 1.0
        assert oracle.frame_rate_modifier < 1.0  # sage = slower
