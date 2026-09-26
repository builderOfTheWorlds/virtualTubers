#!/usr/bin/env python3
"""
avatar_providers/base.py
Contract every avatar rendering backend implements. See
avatar_providers/__init__.py for how a provider is selected/constructed,
and app/avatar.py for the dispatcher loop that drives it.
"""


class AvatarProvider:
    """One instance per avatar pane process, constructed once at startup
    and reused for the life of the process.

    The dispatcher (app/avatar.py) owns state-file polling, deciding the
    current expression/bubble text (resolve_display), and word-wrapping
    the bubble (wrap_bubble). A provider owns everything about *drawing*
    a frame — including its own internal animation timing/frame cycling
    between render_tick() calls.
    """

    #: Seconds the dispatcher should sleep between render_tick() calls.
    #: Subclasses should override with their own natural cadence.
    tick_interval_s = 0.5

    def __init__(self, avatar_config, name, title):
        self.avatar_config = avatar_config or {}
        self.name = name
        self.title = title

    def render_tick(self, expression, bubble_lines, mouth_open=0.0, emotion="neutral"):
        """Draw one frame to the terminal.

        expression: our expression key (e.g. "idle", "thinking", "speaking").
        bubble_lines: list[str] of already word-wrapped speech-bubble
            lines, or None/[] when no bubble is currently shown.
        mouth_open: 0..1, how open the mouth should be THIS tick — audio-
            envelope-driven when real synthesized audio exists, a fixed-
            rate heuristic oscillation otherwise (see avatar.resolve_mouth_open).
            Always 0.0 with no bubble shown. A provider with no morphable
            mesh (e.g. BuiltinProvider's ASCII glyphs) may ignore this and
            keep its own talk_mouth-alternation behavior — the parameter
            exists so providers that CAN morph geometry (codec_avatar) have
            a value to morph with, not as a hard requirement.
        emotion: one of emotion.EMOTIONS — the character's current
            emotional pose, independent of `expression`'s activity cue.
            Defaults to "neutral"; a provider with no emotional-pose
            geometry may ignore it.
        """
        raise NotImplementedError
