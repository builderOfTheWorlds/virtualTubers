#!/usr/bin/env python3
"""
avatar.py
Draws an ASCII face + speech bubble in the terminal, driven by the small
local JSON state file `app/agent_state.py` writes (see
docs/VTuber_AI_Dev_Team_Concept.md §13.3). Polls that file on a short
timer instead of an inter-process socket.
"""
import os
import sys
import time
import argparse
import textwrap

from message_bus import load_worker_config
from agent_state import resolve_state_path, read_state

try:
    from wcwidth import wcswidth
except ImportError:  # pragma: no cover - dependency always present in-container
    wcswidth = None

DEFAULT_EXPRESSIONS = {
    "idle":       {"eyes": "◉  ◉", "mouth": "╰───╯"},
    "thinking":   {"eyes": "⊙  ⊙", "mouth": "─────"},
    "typing":     {"eyes": "◉  ◉", "mouth": "╰───╯"},
    "speaking":   {"eyes": "◕  ◕", "mouth": "╰▾──╯"},
    "frustrated": {"eyes": "◕  ◕", "mouth": "╭───╮"},
    "happy":      {"eyes": "◉  ◉", "mouth": "╰▾▾▾╯"},
    "focused":    {"eyes": "◔  ◔", "mouth": "─────"},
}

# Safety net: if the agent dies mid "thinking" (no bubble to time out), don't
# leave the avatar stuck mid-expression forever — settle back to idle.
STALE_AFTER_S = 30
POLL_INTERVAL_S = 0.5


def display_width(s):
    """Terminal cell width of `s`, wcwidth-aware (falls back to len())."""
    if wcswidth is not None:
        w = wcswidth(s)
        if w is not None and w >= 0:
            return w
    return len(s)


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


def render(name, title, expression, eyes, mouth, bubble_lines=None):
    os.system("clear")
    face = [
        "╭───────────╮",
        f"│  {eyes}  │",
        "│     ▾     │",
        f"│  {mouth}  │",
        "╰───────────╯",
        f"[ {name:^9} ]",
        f"[ {expression:^9} ]",
    ]

    if not bubble_lines:
        print("\n".join("  " + line for line in face))
        sys.stdout.flush()
        return

    box_width = max(display_width(line) for line in bubble_lines)
    box = [f"╭{'─' * (box_width + 2)}╮"]
    for line in bubble_lines:
        pad = box_width - display_width(line)
        box.append(f"│ {line}{' ' * pad} │")
    box.append(f"╰{'─' * (box_width + 2)}╯")

    # Anchor the bubble beside the face's eye row so it reads left-to-right,
    # face first, like a real speech bubble pointing at the speaker.
    anchor = 1
    out_lines = []
    for i, face_line in enumerate(face):
        box_idx = i - anchor
        extra = "   " + box[box_idx] if 0 <= box_idx < len(box) else ""
        out_lines.append("  " + face_line + extra)
    print("\n".join(out_lines))
    sys.stdout.flush()


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
    expressions = avatar_config.get("expressions") or DEFAULT_EXPRESSIONS
    bubble_duration_s = avatar_config.get("bubble_duration_s", 6)
    bubble_width = avatar_config.get("bubble_width", 32)

    state_path = resolve_state_path(agent_config)
    print(f"[avatar] watching state file={state_path}", file=sys.stderr)

    while True:
        state = read_state(state_path)
        expression, bubble = resolve_display(state, time.time(), bubble_duration_s)
        face = expressions.get(expression, DEFAULT_EXPRESSIONS["idle"])
        bubble_lines = wrap_bubble(bubble, bubble_width) if bubble else None

        render(name, title, expression, face["eyes"], face["mouth"], bubble_lines)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
