#!/usr/bin/env python3
"""
avatar_providers/termgl_avatar.py
3D avatar provider: renders a shaded, rotating termgl mesh (see
docs/panels.md's "3D rendering (termgl)" section) instead of the flat
ASCII box face (BuiltinProvider) or the vendored 2D animation stack
(AsciiAvatarProvider).

STATE OF THIS PROVIDER: the mesh is a placeholder icosahedron (mesh3d.
build_icosahedron) — there is no rigged/expression-driven character model
yet. Each of our 7 expressions maps only to a rotation-speed + color
change (see EXPRESSION_STYLE below), not a distinct pose/animation, until
a real character mesh is authored (future work). This still proves the
full render loop shape a future rigged mesh would use — swap
`build_icosahedron()` for a real mesh loader and the rest of this file is
unchanged.

Requires termgl, which needs Python >=3.11 (see Dockerfile's /opt/render3d
venv) — unlike the rest of this app's system python3.10. The registry
(avatar_providers/__init__.py) already catches ANY exception raised out of
a provider's __init__ and falls back to BuiltinProvider, so running this
under an interpreter without termgl installed degrades gracefully (an
ImportError here is caught there) rather than crashing the avatar pane.
"""
import sys

from avatar_display import build_bubble_box
from avatar_providers.base import AvatarProvider

WIDTH = 55
HEIGHT = 24
FOV = 1.2
VIEW_ROT_X = 0.4
VIEW_DIST = 3.0

# expression -> (rotation speed multiplier, termgl color name). Faster
# rotation reads as "more active/animated" for thinking/speaking states;
# color shift gives a cheap-but-visible expression cue until a real rigged
# mesh can drive actual pose changes.
EXPRESSION_STYLE = {
    "idle": (1.0, "YELLOW"),
    "thinking": (1.8, "CYAN"),
    "typing": (1.5, "YELLOW"),
    "speaking": (2.2, "GREEN"),
    "frustrated": (2.5, "RED"),
    "happy": (2.0, "GREEN"),
    "focused": (1.2, "YELLOW"),
}


class TermglAvatarProvider(AvatarProvider):
    """Rotating shaded 3D mesh avatar. tick_interval_s is fast (matches the
    prototype's smooth-rotation cadence) since the whole point is visible
    motion, unlike BuiltinProvider's static face."""

    tick_interval_s = 0.05

    def __init__(self, avatar_config, name, title):
        super().__init__(avatar_config, name, title)

        # Import here (not at module level) so a Python <3.11 interpreter
        # never even attempts to import termgl until this provider is
        # actually selected — keeps the registry's fallback-on-any-
        # exception behavior working cleanly (avatar_providers/__init__.py).
        import numpy as np
        import termgl as tgl
        from mesh3d import build_icosahedron
        from render3d_common import make_context, make_camera, make_view, LitPixelShader, project_point, ndc_to_screen, write_header_line

        self._np = np
        self._tgl = tgl
        self._make_view = make_view
        self._LitPixelShader = LitPixelShader
        self._project_point = project_point
        self._ndc_to_screen = ndc_to_screen
        self._write_header_line = write_header_line

        termgl_cfg = (self.avatar_config.get("termgl_avatar") or {})
        self._angle = 0.0
        self._angle_speed_base = termgl_cfg.get("angle_speed", 0.08)

        self._ctx = make_context(WIDTH, HEIGHT)
        self._camera = make_camera(WIDTH, HEIGHT, fov=FOV)
        self._mesh = build_icosahedron(radius=termgl_cfg.get("radius", 1.0))
        self._last_bubble_row_count = 0

        print(
            f"[avatar] termgl_avatar: ready ({WIDTH}x{HEIGHT}, placeholder icosahedron mesh)",
            file=sys.stderr,
        )

    def render_tick(self, expression, bubble_lines):
        tgl = self._tgl
        np = self._np
        speed_mul, color_name = EXPRESSION_STYLE.get(expression, EXPRESSION_STYLE["idle"])

        view = self._make_view(rot_x=VIEW_ROT_X, rot_y=self._angle, rot_z=0.0, dist=VIEW_DIST)
        vertex_shader = tgl.VertexShaderSimple(np.matmul(self._camera, view))
        pixel_shader = self._LitPixelShader()
        pixel_shader.base_color = tgl.PixFmt(tgl.Idx(getattr(tgl.Color, color_name), flags=tgl.FmtFlag.BOLD))
        for trig in self._mesh:
            pixel_shader.trig = trig
            self._ctx.triangle_3d(trig, vertex_shader, pixel_shader)

        label = f"{self.name} [{expression}]"
        self._ctx.flush()
        self._ctx.clear(tgl.Buffer.FRAME | tgl.Buffer.Z | tgl.Buffer.OUTPUT)

        # Written AFTER flush(), via plain ANSI rather than ctx.puts() — see
        # write_header_line's docstring (DOUBLE_CHARS doubles puts() text too).
        self._write_header_line(label, color_name="WHITE")

        self._render_bubble(bubble_lines)
        self._angle += self._angle_speed_base * speed_mul

    def _render_bubble(self, bubble_lines):
        """No caption/bubble mechanism in termgl itself — draw our own
        bordered box (same style as BuiltinProvider/AsciiAvatarProvider,
        via avatar_display.build_bubble_box) below the rendered mesh."""
        box = build_bubble_box(bubble_lines) if bubble_lines else []
        start_row = HEIGHT + 1

        out = []
        for i in range(max(len(box), self._last_bubble_row_count)):
            out.append(f"\033[{start_row + i};1H\033[K")
            if i < len(box):
                out.append(box[i])
        if out:
            sys.stdout.write("".join(out))
            sys.stdout.flush()
        self._last_bubble_row_count = len(box)
