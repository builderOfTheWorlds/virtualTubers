"""Persona system — bundles frame set, voice, color, and personality."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    name: str
    frames: str
    voice_engine: str  # "kokoro" | "elevenlabs" | "piper"
    voice_id: str
    accent_color: str
    personality: str  # "minimal" | "sage" | "glitch"
    frame_rate_modifier: float


PERSONAS: dict[str, Persona] = {
    "ghost": Persona(
        name="ghost",
        frames="musetalk",
        voice_engine="kokoro",
        voice_id="af_bella",
        accent_color="cyan",
        personality="minimal",
        frame_rate_modifier=1.0,
    ),
    "oracle": Persona(
        name="oracle",
        frames="cyberpunk",
        voice_engine="kokoro",
        voice_id="bf_emma",
        accent_color="amber",
        personality="sage",
        frame_rate_modifier=0.8,
    ),
    "spectre": Persona(
        name="spectre",
        frames="cyberpunk",
        voice_engine="elevenlabs",
        voice_id="",
        accent_color="green",
        personality="glitch",
        frame_rate_modifier=1.3,
    ),
}

DEFAULT_PERSONA = "ghost"


def get_persona(name: str) -> Persona:
    return PERSONAS[name]


def list_personas() -> list[str]:
    return list(PERSONAS.keys())
