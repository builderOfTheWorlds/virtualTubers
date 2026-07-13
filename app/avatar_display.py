#!/usr/bin/env python3
"""
avatar_display.py
Tiny display helpers shared by app/avatar.py (the dispatcher) and every
app/avatar_providers/* backend.

These live in their own flat module — NOT in avatar.py itself — because
avatar_providers/ascii_avatar.py needs to import the vendored
repos/ascii-avatar package, which is *also* a top-level module named
"avatar" (see avatar_providers/ascii_avatar.py for how that name collision
with our own app/avatar.py is handled). Keeping display helpers out of
"avatar" means providers never need to reach into that name at all.
"""
try:
    from wcwidth import wcswidth
except ImportError:  # pragma: no cover - dependency always present in-container
    wcswidth = None


def display_width(s):
    """Terminal cell width of `s`, wcwidth-aware (falls back to len())."""
    if wcswidth is not None:
        w = wcswidth(s)
        if w is not None and w >= 0:
            return w
    return len(s)


def build_bubble_box(bubble_lines):
    """Word-wrapped `bubble_lines` -> a bordered speech-bubble box, as a
    list of display-ready lines. `bubble_lines` must be non-empty."""
    box_width = max(display_width(line) for line in bubble_lines)
    box = [f"╭{'─' * (box_width + 2)}╮"]
    for line in bubble_lines:
        pad = box_width - display_width(line)
        box.append(f"│ {line}{' ' * pad} │")
    box.append(f"╰{'─' * (box_width + 2)}╯")
    return box
