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
import textwrap

from message_bus import load_worker_config
from agent_state import resolve_state_path, read_state
from avatar_display import display_width  # re-exported for callers/tests

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
    """Decide (expression, bubble_text) from the raw state dict.

    - No/unreadable state -> idle, no bubble.
    - A bubble is shown only while fresh (age <= bubble_duration_s); once it
      expires, expressions that only make sense *with* a bubble (speaking,
      frustrated) revert to idle too.
    - A bubble-less expression (e.g. "thinking" during a long LLM call)
      persists until superseded, unless it goes stale (agent likely died).
    """
    if not state:
        return "idle", None

    expression = state.get("expression") or "idle"
    bubble = state.get("bubble")
    age = now - state.get("updated_at", 0)

    if bubble:
        if age > bubble_duration_s:
            bubble = None
            if expression in ("speaking", "frustrated"):
                expression = "idle"
    elif age > stale_after_s:
        expression = "idle"

    return expression, bubble


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
        expression, bubble = resolve_display(state, time.time(), bubble_duration_s)
        bubble_lines = wrap_bubble(bubble, bubble_width) if bubble else None

        provider.render_tick(expression, bubble_lines)
        time.sleep(getattr(provider, "tick_interval_s", DEFAULT_POLL_INTERVAL_S))


if __name__ == "__main__":
    main()
