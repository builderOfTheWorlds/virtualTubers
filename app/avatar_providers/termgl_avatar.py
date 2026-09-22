#!/usr/bin/env python3
"""
avatar_providers/termgl_avatar.py
3D avatar provider: renders a shaded, rotating termgl mesh (see
docs/panels.md's "3D rendering (termgl)" section) instead of the flat
ASCII box face (BuiltinProvider) or the vendored 2D animation stack
(AsciiAvatarProvider).

STATE OF THIS PROVIDER: the mesh is either a GENERATED PARAMETRIC HEAD
(when `avatar.termgl_avatar.character_params` is configured — see
head_mesh.py and docs/character_generator.md) or, by default, the
placeholder icosahedron (mesh3d.build_icosahedron). Expressions still map
only to a rotation-speed change (see EXPRESSION_STYLE below), not a
distinct pose: morph-target support is designed (docs/avatar_3d_design.md
§4) and the generator already guarantees the stable topology it needs, but
the per-expression poses themselves are not built yet. A character's
configured `accent_color` overrides the per-expression color so a face
keeps a stable identity on stream.

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
# Placeholder icosahedron sits at the original distance; a generated head is
# taller than it is wide and needs slightly more room to avoid clipping its
# crown/neck at the widest slider settings. Either is overridable per worker
# via `avatar.termgl_avatar.view_dist`.
DEFAULT_VIEW_DIST = 3.0
HEAD_VIEW_DIST = 2.4
VIEW_DIST = DEFAULT_VIEW_DIST  # back-compat for anything importing the old name

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

        # character_params (a slider dict or a preset name) selects the
        # generated parametric head; its ABSENCE keeps the old placeholder
        # icosahedron. That default matters: the other workers still run
        # this provider with no character of their own, and must not start
        # rendering a generic default face just because this code landed.
        self._character_params = termgl_cfg.get("character_params")
        self._mesh, self._mesh_kind = self._build_mesh(
            self._character_params, termgl_cfg.get("radius", 1.0), build_icosahedron)
        self._accent_color = self._resolve_accent_color(self._character_params)
        self._view_dist = termgl_cfg.get("view_dist", DEFAULT_VIEW_DIST
                                         if self._mesh_kind == "icosahedron"
                                         else HEAD_VIEW_DIST)
        self._last_bubble_row_count = 0

        print(
            f"[avatar] termgl_avatar: ready ({WIDTH}x{HEIGHT}, {self._mesh_kind} mesh, "
            f"{len(self._mesh)} triangles)",
            file=sys.stderr,
        )

    def _build_mesh(self, character_params, radius, build_icosahedron):
        """Return (mesh, kind). Falls back to the placeholder icosahedron if
        the configured character params are unusable.

        Deliberately does NOT let a bad `character_params` block propagate:
        the registry (avatar_providers/__init__.py) would catch it and drop
        the whole provider back to the flat-ASCII BuiltinProvider, so one
        typo'd slider would silently cost this worker its 3D pane entirely.
        A placeholder mesh with a loud stderr line degrades far less.
        """
        if character_params is None:
            return build_icosahedron(radius=radius), "icosahedron"

        try:
            from head_mesh import build_head_mesh
            mesh = build_head_mesh(character_params)
            if len(mesh) == 0:
                raise ValueError("generated head mesh is empty")
            return mesh, "parametric head"
        except Exception as exc:  # noqa: BLE001 — see docstring
            print(
                f"[avatar] termgl_avatar: could not build parametric head "
                f"({exc!r}) — falling back to placeholder icosahedron",
                file=sys.stderr,
            )
            return build_icosahedron(radius=radius), "icosahedron (head build failed)"

    def _resolve_accent_color(self, character_params):
        """The character's own accent color, or None to keep the stock
        per-expression palette.

        Resolution is best-effort and never raises: a face that renders in
        the default yellow is a cosmetic miss, while an exception here
        would cost the worker its whole 3D pane (see _build_mesh).
        """
        if character_params is None:
            return None
        try:
            from character_schema import resolve_params
            return resolve_params(character_params)["accent_color"]
        except Exception as exc:  # noqa: BLE001 — cosmetic, never fatal
            print(f"[avatar] termgl_avatar: could not resolve accent_color ({exc!r})",
                  file=sys.stderr)
            return None


    def render_tick(self, expression, bubble_lines):
        tgl = self._tgl
        np = self._np
        speed_mul, color_name = EXPRESSION_STYLE.get(expression, EXPRESSION_STYLE["idle"])
        # A character's configured accent_color overrides the stock
        # per-expression color so the face keeps a stable identity on
        # stream; the expression still drives ROTATION SPEED, which stays
        # readable either way. Without character_params this is None and
        # the original color-swap behaviour is unchanged.
        if self._accent_color is not None:
            color_name = self._accent_color

        view = self._make_view(rot_x=VIEW_ROT_X, rot_y=self._angle, rot_z=0.0,
                               dist=self._view_dist)
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
