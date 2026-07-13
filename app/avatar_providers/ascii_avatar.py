#!/usr/bin/env python3
"""
avatar_providers/ascii_avatar.py
Adapter driving the vendored repos/ascii-avatar animation/rendering stack
(upstream https://github.com/Angelopvtac/ascii-avatar, MIT).

We use ONLY its ASCII animation/rendering path:
  - avatar.renderer.AvatarRenderer   (blessed-based frame drawing)
  - avatar.animation.AnimationCompositor  (frame cycling + micro-events)
  - avatar.state_machine.AvatarState      (the 5-state enum)
  - avatar.personas.get_persona           (frame_rate_modifier)

We deliberately never import its event bus (avatar.event_bus, pyzmq),
bridge/ (MCP, Claude hooks), voice/ (TTS), or avatar.agent — those pull in
pyzmq/anthropic/sounddevice, none of which belong in this container.
avatar.main (the upstream CLI entrypoint) is ALSO never imported for the
same reason: it does `from avatar.event_bus import ...` and
`from avatar.voice.base import TTSEngine` at module level, so merely
importing it would drag those heavy deps in.

Frame set: forced to "cyberpunk" regardless of the selected persona's
configured frame set. The "cyberpunk" set (repos/ascii-avatar/src/avatar/
frames/cyberpunk.py) is pure hand-crafted ANSI text with no image
dependency. The other frame sets pull in Pillow and/or numpy at import
time:
  - frames/converter.py   -> `from PIL import Image, ImageDraw` + numpy
    (used by "musetalk" and "portrait")
  - frames/layered.py     -> `from PIL import Image, ImageEnhance`
    (used by "layered2d")
Those deps are intentionally NOT in requirements.txt (only `blessed` was
added for this provider), so those frame sets/loader branches must never
be imported. This matters for personas too: the built-in "ghost" persona
(the default) is configured with frames="musetalk" upstream — we ignore
that field entirely and always request "cyberpunk" from AvatarRenderer.
"""
import os
import sys
import time

from avatar_display import build_bubble_box
from avatar_providers.base import AvatarProvider

# Maps our 7 expressions to the vendored package's 5 AvatarState values.
# Overridable via config `avatar.expression_map`.
DEFAULT_EXPRESSION_MAP = {
    "idle": "idle",
    "thinking": "thinking",
    "typing": "thinking",
    "focused": "thinking",
    "speaking": "speaking",
    "happy": "speaking",
    "frustrated": "error",
}


class AsciiAvatarProviderError(RuntimeError):
    """Raised when the ascii_avatar backend can't be set up (missing
    vendored repo, import failure, terminal init failure, ...). The
    registry (avatar_providers/__init__.py) catches this at construction
    time and falls back to BuiltinProvider."""


def _resolve_repo_src_path(avatar_config):
    """config avatar.ascii_avatar.repo_path > env ASCII_AVATAR_REPO >
    /repos/ascii-avatar/src (container) > <project_root>/repos/ascii-avatar/src
    (local dev, relative to this app dir)."""
    ascii_cfg = (avatar_config or {}).get("ascii_avatar") or {}
    configured = ascii_cfg.get("repo_path")
    if configured:
        return configured

    env_path = os.environ.get("ASCII_AVATAR_REPO")
    if env_path:
        return env_path

    container_path = "/repos/ascii-avatar/src"
    if os.path.isdir(container_path):
        return container_path

    # app/avatar_providers/ascii_avatar.py -> app/ -> project root
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(app_dir)
    return os.path.join(project_root, "repos", "ascii-avatar", "src")


def _make_vendored_avatar_importable(repo_src_path):
    """Put the vendored ascii-avatar package on sys.path.

    Name collision note: our own dispatcher module is app/avatar.py, which
    Python also sees as a top-level module named "avatar" (its directory,
    /app, is on sys.path since it's the entry script). The vendored
    package at `repo_src_path` is ALSO a top-level package named "avatar".
    Whichever one got imported first would shadow the other. Since
    avatar.py itself never does `import avatar` (it runs as __main__), the
    only realistic case is a stale entry left by a previous (failed)
    provider construction attempt — evict any "avatar"/"avatar.*" module
    not loaded from `repo_src_path` before importing, so the vendored
    package always wins here.
    """
    if repo_src_path not in sys.path:
        sys.path.insert(0, repo_src_path)

    normalized_repo = os.path.normcase(os.path.abspath(repo_src_path))
    for mod_name in list(sys.modules):
        if mod_name != "avatar" and not mod_name.startswith("avatar."):
            continue
        mod_file = getattr(sys.modules[mod_name], "__file__", "") or ""
        mod_file = os.path.normcase(os.path.abspath(mod_file)) if mod_file else ""
        if not mod_file.startswith(normalized_repo):
            del sys.modules[mod_name]


class AsciiAvatarProvider(AvatarProvider):
    """Drives the vendored ascii-avatar renderer/animation stack, mapped
    onto our expressions and speech-bubble state."""

    def __init__(self, avatar_config, name, title):
        super().__init__(avatar_config, name, title)
        ascii_cfg = (self.avatar_config.get("ascii_avatar") or {})
        persona_name = ascii_cfg.get("persona", "ghost")
        self.tick_interval_s = ascii_cfg.get("tick_interval_s", 0.1)

        self.expression_map = dict(DEFAULT_EXPRESSION_MAP)
        self.expression_map.update(self.avatar_config.get("expression_map") or {})

        repo_src_path = _resolve_repo_src_path(self.avatar_config)
        print(
            f"[avatar] ascii_avatar: repo_path={repo_src_path!r} persona={persona_name!r}",
            file=sys.stderr,
        )

        try:
            _make_vendored_avatar_importable(repo_src_path)

            import blessed
            from avatar.renderer import AvatarRenderer
            from avatar.animation import AnimationCompositor
            from avatar.state_machine import AvatarState
            from avatar.personas import get_persona

            persona = get_persona(persona_name)  # raises KeyError if unknown

            self._AvatarState = AvatarState
            self._term = blessed.Terminal()

            # Fail fast here (construction time) rather than on first
            # render — a dumb/non-interactive tmux pane can make blessed's
            # fullscreen/terminal init raise, and the registry only
            # catches failures raised out of __init__.
            self._fullscreen_ctx = self._term.fullscreen()
            self._fullscreen_ctx.__enter__()

            self._renderer = AvatarRenderer(
                terminal=self._term,
                frame_set="cyberpunk",  # see module docstring — never persona.frames
                frame_rate_modifier=persona.frame_rate_modifier,
            )
            self._compositor = AnimationCompositor(self._renderer._frames, self._renderer._rates)
        except Exception as exc:
            raise AsciiAvatarProviderError(
                f"ascii_avatar setup failed (repo_path={repo_src_path!r}, "
                f"persona={persona_name!r}): {exc!r}"
            ) from exc

        self._frame_index = 0
        self._next_frame_at = time.monotonic()
        self._last_bubble_row_count = 0

        print(
            f"[avatar] ascii_avatar: ready (frame_set=cyberpunk, persona={persona_name!r}, "
            f"tick_interval_s={self.tick_interval_s})",
            file=sys.stderr,
        )

    def render_tick(self, expression, bubble_lines):
        state_value = self.expression_map.get(expression, "idle")
        try:
            state = self._AvatarState(state_value)
        except ValueError:
            print(
                f"[avatar] ascii_avatar: expression_map maps {expression!r} -> "
                f"unknown state {state_value!r} — using idle",
                file=sys.stderr,
            )
            state = self._AvatarState.IDLE
            state_value = state.value

        # Providers own their own animation pacing: our tick_interval_s
        # (~0.1s) is much faster than most states' natural frame rate, so
        # only advance the frame index once that state's own rate has
        # elapsed since the last advance.
        now = time.monotonic()
        if now >= self._next_frame_at:
            self._frame_index = self._renderer.next_frame_index(state, self._frame_index)
            self._next_frame_at = now + self._compositor.get_frame_rate(state_value)

        frame = self._compositor.get_frame(state_value, self._frame_index)
        if not frame:
            frame = self._renderer.get_current_frame(state, self._frame_index)

        status = self._renderer.format_status_bar(
            state=state,
            connected=True,
            tts_loaded=False,
            last_event=self.title or "",
            time_since_last_event=0,
        )
        self._renderer.render_frame(frame, status)
        self._render_bubble(frame, bubble_lines)

    def _render_bubble(self, frame, bubble_lines):
        """The vendored renderer has no caption/bubble mechanism, so draw
        our own bordered box (same style as BuiltinProvider, via
        avatar_display.build_bubble_box) a couple of rows below the
        animated face and above the status bar row."""
        frame_row_count = frame.count("\n") + 1
        start_row = min(frame_row_count + 2, max(self._term.height - 1, 1))

        box = build_bubble_box(bubble_lines) if bubble_lines else []

        out = []
        # Clear as many rows as the taller of this tick's box or the
        # previous tick's box, so a dismissed bubble doesn't leave a ghost.
        for i in range(max(len(box), self._last_bubble_row_count)):
            out.append(f"\033[{start_row + i};1H\033[K")
            if i < len(box):
                out.append(box[i])
        if out:
            sys.stdout.write("".join(out))
            sys.stdout.flush()
        self._last_bubble_row_count = len(box)
