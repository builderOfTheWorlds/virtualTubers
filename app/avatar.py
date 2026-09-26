#!/usr/bin/env python3
"""
avatar.py
Thin dispatcher: polls the small local JSON state file `agent_state.py`
writes (see docs/VTuber_AI_Dev_Team_Concept.md §13.3), resolves the
current expression + speech bubble, and hands one frame off to a
pluggable AvatarProvider (avatar_providers/) each tick. Polls the state
file on a short timer instead of an inter-process socket.

Rendering behavior itself lives in avatar_providers/*.py — see
avatar_providers/__init__.py for provider selection/fallback.
"""
import os
import sys
import time
import argparse
import logging
import textwrap

from message_bus import load_worker_config
from agent_state import resolve_state_path, read_state
from avatar_display import display_width  # re-exported for callers/tests
from emotion import normalize_emotion
from audio_envelope import sample_envelope

# Providers (codec_avatar.py, gl_raster.py, pane_geometry.py) log via the
# standard `logging` module, not print() — without this, those log.warning/
# log.info calls are invisible in the deployed pane's tmux output (no root
# handler configured means the stdlib's "handler of last resort" silently
# swallows everything but a bare WARNING+ one-liner with no context).
# Diagnosed 2026-09-22: a GPU/GL failure on gx10 (solid black codec_avatar
# window, no visible error) turned out to be logged and just never shown —
# this call is what makes that kind of failure debuggable from `docker logs`
# / the tmux pane instead of requiring a live shell to re-run the module.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)

# Safety net: if the agent dies mid "thinking" (no bubble to time out), don't
# leave the avatar stuck mid-expression forever — settle back to idle.
STALE_AFTER_S = 30

# Fallback poll interval if a provider doesn't set tick_interval_s.
DEFAULT_POLL_INTERVAL_S = 0.5


def wrap_bubble(text, width):
    """Word-wrap `text` to `width` display columns. Returns a non-empty list of lines."""
    if not text:
        return []
    return textwrap.wrap(text, width=width) or [text]


def resolve_display(state, now, bubble_duration_s, stale_after_s=STALE_AFTER_S):
    """Decide (expression, bubble_text, emotion) from the raw state dict.

    - No/unreadable state -> idle, no bubble, neutral emotion.
    - A bubble is shown only while fresh (age <= bubble_duration_s); once it
      expires, expressions that only make sense *with* a bubble (speaking,
      frustrated) revert to idle too.
    - A bubble-less expression (e.g. "thinking" during a long LLM call)
      persists until superseded, unless it goes stale (agent likely died).
    - `emotion` is independent of expression/bubble lifetime: it is the
      character's last-reported emotional pose (write_state's `emotion`
      field), normalized to a valid emotion.EMOTIONS member (defaulting to
      "neutral" for a missing/unrecognized value — including every state
      file written before this field existed). It does NOT go stale/revert
      the way expression does — a character that was angry a beat ago
      stays looking angry until it reports a new emotion or a fresh
      "neutral", matching how a real performer's expression doesn't snap
      back to blank between lines of the same emotional beat.
    """
    if not state:
        return "idle", None, "neutral"

    expression = state.get("expression") or "idle"
    bubble = state.get("bubble")
    emotion = normalize_emotion(state.get("emotion"))
    age = now - state.get("updated_at", 0)

    if bubble:
        if age > bubble_duration_s:
            bubble = None
            if expression in ("speaking", "frustrated"):
                expression = "idle"
    elif age > stale_after_s:
        expression = "idle"

    return expression, bubble, emotion


#: Heuristic mouth-cycle rate (Hz) used only when a bubble is showing but
#: the state has no real audio_envelope to sample — matches the "cheap,
#: proven UX" fallback the other two providers already use (alternating a
#: mouth glyph roughly once per tick), see docs/avatar_providers.md.
HEURISTIC_MOUTH_HZ = 4.5


def resolve_mouth_open(state, now, bubble_present):
    """0..1 mouth-openness for THIS tick.

    Prefers the real audio envelope (audio_envelope.compute_envelope's
    output, written into the state file by whoever started playback —
    replay.py's Performer, campaign/renderer.py's SceneRenderer) when one
    is present and playback has actually started. Falls back to a fixed-
    rate sine oscillation for as long as a bubble is shown but no audio
    data exists (agent.py's team workers, which never synthesize real
    audio) — the same heuristic-cycling UX builtin.py/ascii_avatar.py
    already use, just applied to a continuous morph value instead of an
    alternating glyph.

    Returns 0.0 (closed) with no bubble at all.
    """
    if not bubble_present:
        return 0.0

    envelope = (state or {}).get("audio_envelope")
    started_at = (state or {}).get("audio_started_at")
    if envelope and started_at is not None:
        rate_hz = (state or {}).get("audio_rate_hz") or 1
        return sample_envelope(envelope, rate_hz, now - started_at)

    # No real audio for this line — heuristic cycling.
    import math
    return 0.5 + 0.5 * math.sin(now * HEURISTIC_MOUTH_HZ * 2 * math.pi)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/worker.yaml")
    args = parser.parse_args()

    config = {}
    if os.path.exists(args.config):
        config = load_worker_config(args.config) or {}
    agent_config = config.get("agent", {})
    avatar_config = config.get("avatar", {})

    name = os.environ.get("AGENT_NAME") or avatar_config.get("name", "WORKER")
    title = os.environ.get("AGENT_TITLE") or avatar_config.get("title", "Agent")
    bubble_duration_s = avatar_config.get("bubble_duration_s", 6)
    bubble_width = avatar_config.get("bubble_width", 32)

    state_path = resolve_state_path(agent_config)
    print(f"[avatar] watching state file={state_path}", file=sys.stderr)

    from avatar_providers import load_provider
    provider = load_provider(avatar_config, name, title)

    while True:
        state = read_state(state_path)
        now = time.time()
        expression, bubble, emotion = resolve_display(state, now, bubble_duration_s)
        bubble_lines = wrap_bubble(bubble, bubble_width) if bubble else None
        mouth_open = resolve_mouth_open(state, now, bool(bubble_lines))

        provider.render_tick(expression, bubble_lines, mouth_open=mouth_open, emotion=emotion)
        time.sleep(getattr(provider, "tick_interval_s", DEFAULT_POLL_INTERVAL_S))


if __name__ == "__main__":
    main()
