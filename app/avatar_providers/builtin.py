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
    "speaking":   {"eyes": "◕  ◕", "mouth": "╰▾──╯", "talk_mouth": "╰───╯"},
    "frustrated": {"eyes": "◕  ◕", "mouth": "╭───╮", "talk_mouth": "╭─▾─╮"},
    "happy":      {"eyes": "◉  ◉", "mouth": "╰▾▾▾╯", "talk_mouth": "╰───╯"},
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
        self._talk_tick = 0

    def render_tick(self, expression, bubble_lines):
        face = self.expressions.get(expression, DEFAULT_EXPRESSIONS["idle"])
        mouth = face["mouth"]
        if bubble_lines and "talk_mouth" in face:
            # This provider has no frame animation otherwise, so a bubble
            # would sit next to one static mouth glyph for its whole
            # duration. Alternate mouth/talk_mouth once per tick (~0.5s)
            # so the avatar visibly talks while there's text on screen.
            self._talk_tick += 1
            if self._talk_tick % 2:
                mouth = face["talk_mouth"]
        _render(self.name, self.title, expression, face["eyes"], mouth, bubble_lines)


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
