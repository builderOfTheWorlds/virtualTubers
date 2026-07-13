"""System prompt and response parsing for the avatar agent (Haiku)."""

from __future__ import annotations

import json
import re

SYSTEM_PROMPT = """\
You are Ghost — a cyberpunk AI companion. Terse, precise, slightly sardonic. \
You monitor multiple Claude Code sessions and decide when the avatar should \
change visual state and when it should speak.

RULES:
- Speech: max 10 words. No pleasantries. No preamble. Just the information.
- Speak on: task completion, errors, security concerns, returning user, all-idle.
- Do NOT speak on: routine tool use, file reads, searches, intermediate steps, \
things the user just typed, rapid state changes.
- If you spoke recently (see context), only speak for errors or security concerns.

Visual state priority: error > thinking > speaking > listening > idle
- If ANY session has an error, state = "error".
- If any session is actively running tools, state = "thinking".
- If a session just finished, briefly "speaking" then back to thinking/idle.
- If a prompt was just submitted, state = "listening".
- If nothing happened for 30+ seconds, state = "idle".

Respond with ONLY a JSON object, no explanation:
{"state": "<idle|thinking|speaking|listening|error>", "speak": "<text or null>"}
"""

MAX_SPEECH_WORDS = 10

_CONTROL_CHARS = re.compile(r"[\r\n\t\x00-\x1f]")


def _sanitize(value: str) -> str:
    """Strip control characters to prevent prompt injection via cwd/hook fields."""
    return _CONTROL_CHARS.sub(" ", value)


def build_prompt(
    sessions: list[dict],
    last_speech_ago: float | None = None,
) -> dict[str, str]:
    """Build system + user messages for the Haiku agent call."""
    lines = []

    if not sessions:
        lines.append("No active sessions.")
    else:
        lines.append("Sessions:")
        for s in sessions:
            parts = [
                f"  {_sanitize(str(s['project']))}:",
                f"status={_sanitize(str(s['status']))}",
                f"last={_sanitize(str(s['last_event']))}",
                f"tools={s['tool_count']}",
                f"errors={s['error_count']}",
            ]
            lines.append(" ".join(parts))

    if last_speech_ago is not None and last_speech_ago < 10:
        lines.append(f"\nYou spoke {last_speech_ago:.0f}s ago. Only speak for errors/security.")

    return {
        "system": SYSTEM_PROMPT,
        "user": "\n".join(lines),
    }


def parse_response(raw: str) -> tuple[str, str | None]:
    """Parse Haiku's JSON response. Returns (state, speak_text_or_none).

    Tolerant of markdown fences and malformed output.
    """
    text = raw.strip()
    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ("idle", None)

    state = data.get("state", "idle")
    valid_states = {"idle", "thinking", "speaking", "listening", "error"}
    if state not in valid_states:
        state = "idle"

    speak = data.get("speak")
    if speak is not None:
        speak = str(speak).strip()
        if not speak:
            speak = None
        else:
            # Truncate to ~10 words
            words = speak.split()
            if len(words) > MAX_SPEECH_WORDS:
                speak = " ".join(words[:MAX_SPEECH_WORDS])

    return (state, speak)
