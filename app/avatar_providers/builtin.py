#!/usr/bin/env python3
"""
avatar_providers/builtin.py
The original static ASCII box-face renderer (formerly avatar.py's
render()), extracted verbatim into the AvatarProvider contract. This is
the always-available fallback provider — no external deps beyond
wcwidth, which is already required by the rest of avatar.py.
"""
import os
import sys

from avatar_display import build_bubble_box
from avatar_providers.base import AvatarProvider

DEFAULT_EXPRESSIONS = {
    "idle":       {"eyes": "◉  ◉", "mouth": "╰───╯"},
    "thinking":   {"eyes": "⊙  ⊙", "mouth": "─────"},
    "typing":     {"eyes": "◉  ◉", "mouth": "╰───╯"},
    "speaking":   {"eyes": "◕  ◕", "mouth": "╰▾──╯"},
    "frustrated": {"eyes": "◕  ◕", "mouth": "╭───╮"},
    "happy":      {"eyes": "◉  ◉", "mouth": "╰▾▾▾╯"},
    "focused":    {"eyes": "◔  ◔", "mouth": "─────"},
}


class BuiltinProvider(AvatarProvider):
    """Static box face + speech bubble. Same layout/behavior as the
    original avatar.py: per-worker `avatar.expressions` config overrides
    the default 7-expression set."""

    tick_interval_s = 0.5

    def __init__(self, avatar_config, name, title):
        super().__init__(avatar_config, name, title)
        self.expressions = (self.avatar_config.get("expressions") or DEFAULT_EXPRESSIONS)

    def render_tick(self, expression, bubble_lines):
        face = self.expressions.get(expression, DEFAULT_EXPRESSIONS["idle"])
        _render(self.name, self.title, expression, face["eyes"], face["mouth"], bubble_lines)


def _render(name, title, expression, eyes, mouth, bubble_lines=None):
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

    box = build_bubble_box(bubble_lines)

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
