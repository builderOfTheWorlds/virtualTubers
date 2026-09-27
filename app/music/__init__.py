"""
music — emotion-driven background score for the roundtable show.

A campaign defines ONE central theme (theme.py: a motif + chord progression
written in scale degrees). The engine (engine.py) plays that theme live,
re-harmonized and re-orchestrated per scene mood (mood_map.py: GEMS mood ->
tempo/mode/register/articulation/brightness/density, after Gabrielsson &
Lindström 2010 and CMERS, Livingstone et al. 2010). Everything it plays can
be recorded to Postgres (music_store.py / recorder.py).

Exports are LAZY (PEP 562 __getattr__): `from music.control import
MusicControl` must stay cheap and numpy-free, because message-api imports it
to write GM overrides and that image has no audio stack.

Design + research notes: .claude/prompts/background_music_plan.md,
docs/music_engine.md.
"""
import importlib

_EXPORTS = {
    "MOODS": "music.mood_map", "MoodParams": "music.mood_map",
    "blend_moods": "music.mood_map", "mood_params": "music.mood_map",
    "mood_for_emotion": "music.mood_map",
    "Theme": "music.theme", "ThemeError": "music.theme", "load_theme": "music.theme",
    "theme_from_dict": "music.theme", "theme_to_dict": "music.theme",
    "MusicEngine": "music.engine",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'music' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
