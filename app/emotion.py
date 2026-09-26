#!/usr/bin/env python3
"""
emotion.py
The Ekman-6 + neutral facial-emotion vocabulary shared by every layer that
touches a character's emotional state: the LLM narration prompts
(agent.py, campaign/improviser.py), the state file (agent_state.py), and
the mesh-morph recipes that actually move geometry (codec_head.py).

Why Ekman's 6 + neutral rather than a bigger/smaller set: it is the
vocabulary FACS (Facial Action Coding System) research maps cleanly onto a
small number of independent brow/eye/mouth displacements — see
docs/avatar_emotion_design.md for the citations. A bigger set would need
finer geometric control than a 55x24/low-poly codec face can actually
render; a smaller one loses expressions the mesh CAN show (fear vs. anger
read very differently even at this resolution).

Kept dependency-free (no numpy, no LLM client) so it can be imported by
prompt-building code, parsing code, and the mesh layer alike without
pulling in anything heavy.
"""

#: The only valid values for a character's emotional pose. "neutral" is the
#: rest state — every EMOTIONS value has a corresponding (possibly all-zero)
#: recipe in codec_head.EMOTION_RECIPES.
EMOTIONS = ("neutral", "happy", "sad", "angry", "afraid", "surprised", "disgusted")

DEFAULT_EMOTION = "neutral"


def normalize_emotion(value):
    """Coerce `value` to a valid EMOTIONS member, defaulting to "neutral"
    for anything unrecognized (None, empty, typo'd, or a value outside the
    vocabulary) — mirrors character_schema.py's "clamp/degrade, never
    raise" convention for anything read from an LLM reply or a state file,
    since either can hand back garbage and must never crash a pane."""
    if not isinstance(value, str):
        return DEFAULT_EMOTION
    candidate = value.strip().lower()
    return candidate if candidate in EMOTIONS else DEFAULT_EMOTION


def parse_structured_reply(raw):
    """Parse an LLM reply expected to be JSON `{"line": "...", "emotion": "..."}`.

    Returns (line, emotion) — `emotion` is always a valid EMOTIONS member
    (normalize_emotion handles anything unrecognized). Tolerant of the
    common ways a small local model fails to follow a JSON-only
    instruction:
      - code-fenced JSON (```json ... ```)
      - extra prose before/after the JSON object (extracts the first
        {...} span)
      - a bare string with no JSON at all (the whole reply is the line,
        emotion defaults to "neutral")
      - a JSON object missing "emotion" or "line"

    Never raises: this is called from the same call sites that already
    tolerate a flaky LLM (agent.py's `except Exception` around
    llm_client.complete, campaign/improviser.py's ImproviserError path) —
    a parsing failure degrades to "treat the whole reply as the line,
    emotion=neutral" rather than losing the line entirely.
    """
    import json
    import re

    if not isinstance(raw, str) or not raw.strip():
        return "", DEFAULT_EMOTION

    text = raw.strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            obj = None
        if isinstance(obj, dict):
            line = obj.get("line")
            if not isinstance(line, str):
                line = ""
            return line, normalize_emotion(obj.get("emotion"))

    # No parseable JSON object — treat the whole reply as the spoken line.
    return text, DEFAULT_EMOTION


#: Appended to a narration/improv system prompt so the model knows the
#: expected reply shape. Kept short and example-driven — small local
#: models (Ollama) follow a concrete example far more reliably than a
#: schema description.
STRUCTURED_REPLY_INSTRUCTION = (
    "Reply with ONLY a JSON object of the form "
    '{"line": "<what you say, in character>", "emotion": "<one of '
    + ", ".join(EMOTIONS) + '>"}. '
    "Choose the emotion that matches what your character is actually "
    "feeling in this moment — not just \"happy\" by default. No text "
    "before or after the JSON object."
)

__all__ = [
    "EMOTIONS",
    "DEFAULT_EMOTION",
    "normalize_emotion",
    "parse_structured_reply",
    "STRUCTURED_REPLY_INSTRUCTION",
]
