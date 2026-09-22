#!/usr/bin/env python3
"""
avatar_providers/codec_avatar.py
Live-rendered 3D avatar provider using the MGS2-codec-style head
(codec_head.py) and the GPU-first rasterizer (gl_raster.py, falling back to
pixel_raster.py). Replaces termgl_avatar.py's character-grid ANSI rendering
for characters using this pipeline: the codec renderer produces real
PIXELS, not terminal characters, so — per docs/character_generator.md's
finding that stream_supervisor.py's ffmpeg captures the whole X DISPLAY,
not the tmux pane text — this provider opens its own small window
positioned over the avatar region instead of writing into the tmux grid.

LIVE, not pre-rendered: every tick calls gl_raster.render_with_fallback()
fresh (see FrameSource.render_frame below) — no cached turntable. That was
an explicit choice (docs/avatar_3d_design.md discussion, 2026-09-22): a
cached turntable would have made framerate a non-issue at the cost of
expressions being a fixed pose set; live rendering keeps that door open at
the cost of needing render_frame() to comfortably clear the stream's fps
target every tick, which gl_raster.render() does (see
tests/test_gl_raster.py's realtime-floor test and
docs/gl_raster_benchmark.md).

WHY TWO CLASSES. `FrameSource` is pure numpy in, numpy frame out — no
display, no window, fully unit-testable without a real X server (Windows
dev box included). `CodecAvatarProvider` is the thin AvatarProvider adapter
that owns a pygame window and blits FrameSource's output into it; it is the
only part of this file that needs a real display and is exercised by hand /
in the worker container, not by the unit suite.
"""
import logging
import sys

import numpy as np

from avatar_providers.base import AvatarProvider

log = logging.getLogger(__name__)

#: Pane pixel size. Matches the resolution codec_final.png was proven out
#: at (docs/character_generator.md) — the render call already known to
#: read as a face, not a re-tuned one.
WIDTH = 560
HEIGHT = 700

#: expression -> (rotation speed multiplier, tint override or None).
#: `frustrated` forces amber regardless of accent_color — same "danger"
#: cue termgl_avatar.py uses (EXPRESSION_STYLE's RED for frustrated) — so
#: it reads as an emotional state change even on an accent-colored face.
#: Every other expression uses the character's own accent_color (see
#: _resolve_tint) so the face keeps a stable identity on stream, matching
#: termgl_avatar.py's `_resolve_accent_color` behavior for this renderer.
EXPRESSION_STYLE = {
    "idle": (1.0, None),
    "thinking": (1.8, None),
    "typing": (1.5, None),
    "speaking": (2.2, None),
    "frustrated": (2.5, "amber"),
    "happy": (2.0, None),
    "focused": (1.2, None),
}


def _detect_truecolor_visual_id():
    """Best-effort: the X server's default TrueColor visual id (e.g.
    "0x21"), via `xdpyinfo`. Returns None (not an exception) on any
    failure — no X server (local dev/CI), xdpyinfo missing, or unparsable
    output all just mean "let SDL pick its own default", same as before
    this fix existed. See the SDL_VIDEO_X11_VISUALID comment in
    CodecAvatarProvider.__init__ for why this specific visual matters."""
    import re
    import subprocess
    try:
        out = subprocess.run(
            ["xdpyinfo"], capture_output=True, text=True, timeout=3)
        if out.returncode != 0:
            return None
        match = re.search(r"default visual id:\s*(0x[0-9a-fA-F]+)", out.stdout)
        return match.group(1) if match else None
    except Exception as exc:  # noqa: BLE001 — cosmetic, falls back to SDL's default
        log.warning("codec_avatar: could not auto-detect the X server's "
                   "default visual (%r); leaving SDL_VIDEO_X11_VISUALID "
                   "unset (SDL's own default pick may be wrong on some "
                   "servers — see 2026-09-22 gx10 black-window bug)", exc)
        return None

#: character_schema.ACCENT_COLORS name -> pixel_raster tint (0..1 RGB
#: float array). Reuses the same 8 names as termgl_avatar/character_schema
#: so a preset's accent_color means the same thing everywhere; RGB picks
#: match render3d_common.py's ANSI mapping in spirit (danger=red,
#: caution=yellow, ...) rather than copying its terminal color values,
#: since those are tuned for 16-color terminals, not a CRT-tinted photo.
ACCENT_TINTS = {
    "GREEN": None,  # None == pixel_raster.TINT_CODEC_GREEN, the classic look
    "AMBER": "amber",
    "YELLOW": np.array([0.95, 0.85, 0.25], dtype=np.float32),
    "CYAN": np.array([0.30, 0.90, 0.95], dtype=np.float32),
    "RED": np.array([1.00, 0.35, 0.30], dtype=np.float32),
    "PURPLE": np.array([0.72, 0.45, 1.00], dtype=np.float32),
    "WHITE": np.array([0.92, 0.95, 0.92], dtype=np.float32),
    "BLUE": np.array([0.35, 0.55, 1.00], dtype=np.float32),
    "BLACK": np.array([0.55, 0.58, 0.55], dtype=np.float32),  # too dark to
    # render literal black through a lambert shade — falls back to a dim
    # neutral grey so the face is still visible.
}


class FrameSource:
    """Pure render step: character params in, a fresh RGB frame out.

    No window, no pygame import — safe to construct and call in a unit
    test or a headless CI box (gl_raster.render_with_fallback already
    degrades to the CPU path when no GPU context exists).
    """

    def __init__(self, character_params, width=WIDTH, height=HEIGHT,
                view_dist=3.25, angle_speed=0.03):
        from codec_head import build_codec_head
        from character_schema import resolve_params
        self.width = width
        self.height = height
        self.view_dist = view_dist
        self.angle_speed_base = angle_speed
        self.angle = 0.0
        self.verts, self.faces, self.materials = build_codec_head(character_params)
        self.last_backend = None
        # Resolved once at construction (not per-frame) — accent_color is
        # fixed for a character's lifetime, same as termgl_avatar.py's
        # _resolve_accent_color. resolve_params is the same schema
        # normalizer PRESETS entries go through, so this works whether
        # character_params is a preset name or an inline slider dict.
        try:
            self.accent_color = resolve_params(character_params)["accent_color"]
        except Exception as exc:  # noqa: BLE001 — cosmetic, never fatal
            log.warning("codec_avatar: could not resolve accent_color (%r), "
                       "defaulting to GREEN", exc)
            self.accent_color = "GREEN"

    def render_frame(self, expression):
        """Advance rotation and render one frame. Returns an (H,W,3) 0..1
        float array. Also returns which backend rendered it (gpu/cpu) —
        surfaced so a worker's logs show a GPU-less box degrading instead
        of silently running the slower path forever."""
        from pixel_raster import TINT_AMBER, TINT_CODEC_GREEN, apply_codec_screen
        import gl_raster

        speed_mul, forced_tint = EXPRESSION_STYLE.get(
            expression, EXPRESSION_STYLE["idle"])
        tint_choice = forced_tint or ACCENT_TINTS.get(self.accent_color)
        if tint_choice is None:
            tint = TINT_CODEC_GREEN
        elif isinstance(tint_choice, str):  # "amber" sentinel
            tint = TINT_AMBER
        else:
            tint = tint_choice

        img, backend = gl_raster.render_with_fallback(
            self.verts, self.faces, self.materials,
            width=self.width, height=self.height,
            rot_y=self.angle, dist=self.view_dist, tint=tint,
        )
        img = apply_codec_screen(img)

        if backend != self.last_backend:
            log.info("codec_avatar: rendering on %s%s", backend.upper(),
                    " (fell back from GPU)" if self.last_backend == "gpu" else "")
            self.last_backend = backend

        self.angle += self.angle_speed_base * speed_mul
        return img, backend


class CodecAvatarProvider(AvatarProvider):
    """Live pixel-rendered 3D avatar. Opens a small pygame window instead
    of writing into the tmux character grid — see module docstring.

    Configured under `avatar.codec_avatar` (kept separate from
    `avatar.termgl_avatar` — this is a different renderer with a different
    output mechanism, not a drop-in option on the same block):

        avatar:
          provider: codec_avatar
          codec_avatar:
            character_params: chadwick
            # window_pos is OPTIONAL — auto-detected via pane_geometry.py
            # (tmux pane cell rect + xterm window pixel rect) at startup so
            # the window tracks tuber_base.yaml's percentage-split layout
            # instead of a hardcoded offset that drifts if the layout,
            # screen resolution, or font size ever changes. Set it
            # explicitly only to override detection (e.g. local testing
            # with no real tmux/X session).
            window_pos: [0, 0]
    """

    tick_interval_s = 1.0 / 30.0  # target the stream's 30fps

    def __init__(self, avatar_config, name, title):
        super().__init__(avatar_config, name, title)

        cfg = (self.avatar_config.get("codec_avatar") or {})
        character_params = cfg.get("character_params")
        if character_params is None:
            raise ValueError(
                "codec_avatar requires avatar.codec_avatar.character_params "
                "(a preset name or slider dict) — there is no placeholder "
                "mesh for this provider, unlike termgl_avatar's icosahedron")

        width, height, window_pos = self._resolve_geometry(cfg)

        self._source = FrameSource(
            character_params, width=width, height=height,
            view_dist=cfg.get("view_dist", 3.25),
            angle_speed=cfg.get("angle_speed", 0.03),
        )

        import os
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{window_pos[0]},{window_pos[1]}"
        # Tells gl_raster.is_available() this process is about to own an
        # SDL/pygame video window on this X display, so it refuses GPU
        # rendering entirely rather than risk the crash/silent-software-
        # fallback interaction between an in-process GL context and that
        # window (see gl_raster.is_available()'s docstring — root-caused
        # 2026-09-22 on gx10 after GPU passthrough + forcing EGL still
        # didn't produce a real GPU-rendered, visible window). Set BEFORE
        # gl_raster is imported by anything, so the very first
        # is_available() call anywhere in this process sees it.
        os.environ["AVATAR_HAS_PYGAME_WINDOW"] = "1"
        # SDL_VIDEO_X11_VISUALID pins the exact X visual SDL creates the
        # window with. Plain depth=24 (tried first, 2026-09-22) is NOT
        # enough: gx10's Xvfb offers BOTH a TrueColor and a DirectColor
        # visual at depth 24, and SDL's default pick landed on the
        # DirectColor one. DirectColor requires its own installed
        # colormap to translate pixel values on read; this Xvfb only
        # supports ONE installed colormap at a time, and the xterm/tmux
        # window already owns it — so our window's colormap silently
        # never gets installed, and every external reader (ffmpeg
        # x11grab, the actual stream capture) sees solid black. Only
        # pygame's OWN in-process readback (surfarray.array3d) looked
        # correct, which is why this passed local dev-machine testing and
        # even manual in-process debugging before the actual X-level
        # capture was checked. Root-caused and confirmed fixed via a live
        # ssh session against the deployed container, 2026-09-22: pinning
        # the exact visual ID xdpyinfo reports as this server's default
        # (TrueColor, matching what the already-working xterm window
        # uses) makes external readers see the real rendered frame.
        # `visual_id` is configurable (not hardcoded to gx10's 0x21) since
        # a different X server could assign a different id for the same
        # TrueColor visual — auto-detect it there instead of hardcoding.
        visual_id = cfg.get("sdl_x11_visualid") or _detect_truecolor_visual_id()
        if visual_id:
            os.environ["SDL_VIDEO_X11_VISUALID"] = visual_id
        import pygame
        pygame.display.init()
        self._pygame = pygame
        # noframe: an undecorated window, same reasoning as startup.sh's
        # borderless xterm — a title bar/border would inset the capture
        # and leave a gap between this window and the rest of the layout.
        self._screen = pygame.display.set_mode(
            (width, height), pygame.NOFRAME)
        pygame.display.set_caption(f"{name} avatar")

        print(
            f"[avatar] codec_avatar: ready ({width}x{height}, "
            f"{len(self._source.faces)} triangles, window at {window_pos})",
            file=sys.stderr,
        )

    def _resolve_geometry(self, cfg):
        """(width, height, window_pos). Auto-detects the pane's actual
        on-screen pixel rect via pane_geometry.py when `window_pos` isn't
        explicitly configured, so the window lines up under whatever the
        layout currently computes rather than a stale hardcoded offset —
        see the class docstring. `width`/`height` from config/the pane
        detection are honored when explicitly set; auto-detection sizes
        the window to the pane's actual pixel size when available, so a
        configured width/height acts as a fallback/override, not the
        default source of truth.

        Retries detection for a few seconds: startup.sh launches every
        pane's process (including this one, via `tmux send-keys` inside
        build_layout.py's emitted script) BEFORE it creates/resizes the
        xterm window and runs `tmux set -g window-size latest;
        refresh-client` (startup.sh steps 5 vs 6) — so a one-shot read at
        provider __init__ time reliably races that window setup and reads
        a still-forming or nonexistent window (observed on gx10,
        2026-09-22: detected rect came back 1x0). Retrying gives that
        ~1-3s of startup time to finish before falling back.
        """
        import time

        configured_pos = cfg.get("window_pos")
        width = cfg.get("width")
        height = cfg.get("height")

        if configured_pos is None:
            from pane_geometry import detect_pane_rect
            detected = None
            deadline = time.monotonic() + cfg.get("geometry_retry_s", 5.0)
            attempt = 0
            while True:
                attempt += 1
                candidate = detect_pane_rect()
                if candidate is not None and candidate[2] >= 32 and candidate[3] >= 32:
                    detected = candidate
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
            if detected is not None:
                x, y, det_width, det_height = detected
                width = width or det_width
                height = height or det_height
                return width or WIDTH, height or HEIGHT, (x, y)
            print(
                f"[avatar] codec_avatar: could not auto-detect a stable pane "
                f"geometry after {attempt} attempt(s) (no tmux/X session, "
                f"xdotool unavailable, or the window never settled) — "
                f"falling back to window_pos=(0,0); set "
                f"avatar.codec_avatar.window_pos explicitly to override",
                file=sys.stderr,
            )
            return width or WIDTH, height or HEIGHT, (0, 0)

        return width or WIDTH, height or HEIGHT, tuple(configured_pos)

    def render_tick(self, expression, bubble_lines):
        import numpy as np
        img, _backend = self._source.render_frame(expression)
        pixels = (np.clip(img, 0.0, 1.0) * 255).astype("uint8")
        # pygame surfarray is (W,H,3); our frames are (H,W,3).
        surf = self._pygame.surfarray.make_surface(pixels.transpose(1, 0, 2))
        self._screen.blit(surf, (0, 0))
        self._pygame.display.flip()
        # Bubble captions have no home in this pixel window yet — the
        # tmux-grid bordered box (avatar_display.build_bubble_box) that
        # termgl_avatar.py draws assumes a character grid underneath it,
        # which this provider does not have. Tracked as follow-up, not
        # silently dropped: logged once so it's visible in worker logs
        # rather than only discoverable by noticing captions never render.
        if bubble_lines and not getattr(self, "_warned_no_bubble", False):
            log.warning("codec_avatar: bubble/caption rendering not yet "
                       "implemented for the pixel-window path")
            self._warned_no_bubble = True
