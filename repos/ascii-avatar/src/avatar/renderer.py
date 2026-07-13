"""Terminal renderer for ASCII avatar using blessed.

Supports both character-based frames (density, halfblock, braille) and
pixel-perfect frames (sixel).  Sixel frames are DCS escape sequences
printed directly — the terminal interprets them as inline images.
"""

from __future__ import annotations

import re
import sys
import time
from typing import Any

from avatar.frames import load_frame_set
from avatar.state_machine import AvatarState

ANSI_ESCAPE = re.compile(r"\033\[[0-9;]*m")

# Sixel frames start with DCS (ESC P) or tmux passthrough (ESC P tmux;)
_SIXEL_PREFIX = ("\033P", "\033Ptmux;")

# Synchronized output mode — prevents flicker by buffering terminal writes
_SYNC_BEGIN = "\033[?2026h"
_SYNC_END = "\033[?2026l"


def _is_sixel_frame(frame: str) -> bool:
    """Check whether a frame string is a sixel escape sequence."""
    return frame.startswith(_SIXEL_PREFIX)


class AvatarRenderer:
    """Renders ASCII art frames to a terminal.

    Args:
        terminal: A blessed Terminal instance (or fake for testing).
        frame_set: Name of the frame set to load.
        frame_rate_modifier: Multiplier on base frame rates (from persona).
        charset: Rendering charset override (passed to frame loader).
    """

    def __init__(
        self,
        terminal: Any,
        frame_set: str = "cyberpunk",
        frame_rate_modifier: float = 1.0,
        charset: str | None = None,
    ) -> None:
        self._term = terminal
        self._frames, self._rates = load_frame_set(
            frame_set, charset=charset,
        )
        self._modifier = frame_rate_modifier
        self._supports_color = getattr(terminal, "number_of_colors", 0) >= 256

        # Detect if we're rendering sixel frames (check first idle frame)
        idle = self._frames.get("idle", [])
        self._is_sixel = bool(idle) and _is_sixel_frame(idle[0])

    def get_current_frame(
        self,
        state: AvatarState,
        frame_index: int,
        mouth_frame_override: int | None = None,
    ) -> str:
        """Return the frame string for *state* and *frame_index*.

        Args:
            state: Current avatar state.
            frame_index: Animation cycle index (used when no override).
            mouth_frame_override: When provided and state is SPEAKING, use
                this index directly instead of *frame_index*.  Supplied by
                :class:`~avatar.frames.mouth_sync.MouthSync`.
        """
        frames = self._frames.get(state.value, self._frames["idle"])
        if not frames:
            frames = self._frames["idle"]
        if mouth_frame_override is not None and state == AvatarState.SPEAKING:
            idx = mouth_frame_override % len(frames)
        else:
            idx = frame_index % len(frames)
        frame = frames[idx]
        if not self._supports_color and not self._is_sixel:
            frame = ANSI_ESCAPE.sub("", frame)
        return frame

    def next_frame_index(self, state: AvatarState, current_index: int) -> int:
        frames = self._frames.get(state.value, self._frames["idle"])
        if not frames:
            return 0
        return (current_index + 1) % len(frames)

    def get_frame_rate(self, state: AvatarState) -> float:
        base = self._rates.get(state.value, 0.8)
        return base * self._modifier

    def format_status_bar(
        self,
        state: AvatarState,
        connected: bool,
        tts_loaded: bool,
        last_event: str = "",
        time_since_last_event: float | None = None,
    ) -> str:
        if time_since_last_event is None:
            conn = "○ waiting"
        elif time_since_last_event > 60:
            conn = "● connected (stale)"
        else:
            conn = "● connected"
        tts = "♪ TTS" if tts_loaded else "♪ no TTS"
        mode = "SIXEL" if self._is_sixel else "ASCII"
        return f" {state.value.upper()} │ {conn} │ {tts} │ {mode} │ last: {last_event} "

    def render_frame(self, frame: str, status_bar: str) -> None:
        """Render a frame and status bar to the terminal.

        Uses synchronized output mode (CSI ?2026h / ?2026l) to buffer
        the entire frame write and flip atomically — eliminates flicker
        even at high frame rates.

        For sixel frames, the escape sequence is written directly to
        stdout (bypassing normal print buffering) so the terminal can
        interpret the inline image.
        """
        with self._term.hidden_cursor():
            if self._is_sixel:
                # Move to top-left and write sixel frame — do NOT clear()
                # as that erases the image before the next frame renders.
                sys.stdout.write("\033[H")  # cursor home
                sys.stdout.write(frame)
                # Status bar at bottom
                y = self._term.height
                sys.stdout.write(f"\033[{y};1H")
                sys.stdout.write(status_bar[:self._term.width])
                sys.stdout.write("\033[K")
                sys.stdout.flush()
            else:
                # Build the entire output as a single buffer, wrapped in
                # synchronized output mode for flicker-free rendering
                buf = []
                buf.append(_SYNC_BEGIN)
                buf.append(self._term.home)
                # Use cursor positioning per-row instead of clear() to
                # avoid full-screen flash
                lines = frame.split("\n")
                for i, line in enumerate(lines):
                    buf.append(f"\033[{i + 1};1H")  # move to row i+1, col 1
                    buf.append(line)
                    buf.append("\033[K")  # clear rest of line

                # Status bar at bottom
                y = self._term.height
                buf.append(f"\033[{y};1H")
                buf.append(status_bar[:self._term.width])
                buf.append("\033[K")
                buf.append(_SYNC_END)

                sys.stdout.write("".join(buf))
                sys.stdout.flush()
