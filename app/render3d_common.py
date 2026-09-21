#!/usr/bin/env python3
"""
render3d_common.py
Shared termgl context/camera/lighting-shader setup for the three termgl 3D
panes: radar_pane.py, knowledge_graph_pane.py, and
avatar_providers/termgl_avatar.py.

Only importable under the /opt/render3d Python 3.11 venv (termgl requires
Python >=3.11 — see Dockerfile's render3d venv comment and
docs/panels.md's "3D rendering (termgl)" section for why it's a SEPARATE
venv from the rest of this app, which runs on the system python3.10).

Originated as a prototype in .claude/prompts/tuber_base_mockups/
render3d_common.py — promoted here with no logic changes.
"""
import numpy as np
import termgl as tgl


def make_context(width, height, double_chars=True):
    ctx = tgl.TGL(width, height)
    ctx.cull_face(tgl.Face.BACK, tgl.Winding.CCW)
    settings = (tgl.Setting.CULL_FACE | tgl.Setting.Z_BUFFER
                | tgl.Setting.OUTPUT_BUFFER | tgl.Setting.PROGRESSIVE)
    if double_chars:
        settings |= tgl.Setting.DOUBLE_CHARS
    ctx.enable(settings)
    return ctx


def make_camera(width, height, fov=1.2, near=0.1, far=10.0):
    camera = np.zeros((4, 4), dtype=np.float32)
    tgl.camera(camera, width, height, fov, near, far)
    return camera


def make_view(rot_x=0.0, rot_y=0.0, rot_z=0.0, dist=3.0):
    """Standard 'orbit camera' transform: rotate then pull back along Z."""
    scale_m = np.zeros((4, 4), dtype=np.float32)
    rotate_m = np.zeros((4, 4), dtype=np.float32)
    translate_m = np.zeros((4, 4), dtype=np.float32)
    tgl.scale(scale_m, 1.0, 1.0, 1.0)
    tgl.rotate(rotate_m, rot_x, rot_y, rot_z)
    tgl.translate(translate_m, 0.0, 0.0, dist)
    return np.matmul(np.matmul(translate_m, scale_m), rotate_m)


class LitPixelShader(tgl.PixelShader):
    """Flat-shaded lighting: intensity from the angle between the triangle
    normal and a fixed light direction, mapped onto a character gradient.
    Same technique as pyTermGL's own demo_teapot.py. Callers set `.trig`
    to the current triangle before each triangle_3d() call (termgl invokes
    pixel_shader() per-pixel within that triangle, with no other way to
    pass triangle-specific data in) and may override `.base_color` per
    mesh (see radar_pane.py using GREEN vs knowledge_graph_pane.py's CYAN).
    """
    trig = None
    light_direction = np.array([0.4, 0.6, 1.0], dtype=np.float32)
    light_direction = light_direction / np.linalg.norm(light_direction)
    base_color = tgl.PixFmt(tgl.Idx(tgl.Color.GREEN, flags=tgl.FmtFlag.BOLD))
    gradient = tgl.gradient_min

    def pixel_shader(self, u, v):
        verts = self.trig["verts"]
        ab = verts[1] - verts[0]
        ac = verts[2] - verts[0]
        cp = np.cross(ab, ac)
        mag = np.linalg.norm(cp)
        if mag < 1e-8:
            light_mul = 0.3
        else:
            dp = np.dot(self.light_direction, cp / mag)
            light_mul = max(0.15, dp * 0.5 + 0.5)
        c = self.gradient.char(int(light_mul * 255))
        return (self.base_color, c)


def project_point(mvp, point):
    """Project a single 3D point through a 4x4 matrix to normalized device
    coords, for drawing 2D overlays (edge lines) alongside 3D triangle_3d()
    meshes — termgl's line()/puts() take screen-space verts, not 3D world
    verts, so callers needing both (knowledge_graph_pane.py's edges) must
    do this projection themselves. Returns None for points behind the
    camera (w<=0) so callers can skip drawing that segment instead of
    plotting a garbage-projected line.
    """
    p4 = np.array([point[0], point[1], point[2], 1.0], dtype=np.float32)
    clip = mvp @ p4
    if clip[3] <= 1e-6:
        return None
    ndc = clip[:3] / clip[3]
    return ndc  # caller maps ndc.xy [-1,1] -> screen pixels


def ndc_to_screen(ndc, width, height):
    """termgl screen coords: x in [0, width), y in [0, height), y top-down."""
    x = (ndc[0] * 0.5 + 0.5) * width
    y = (1.0 - (ndc[1] * 0.5 + 0.5)) * height
    return x, y


# ANSI color codes for write_header_line — matches termgl's indexed 8-color
# palette closely enough for a plain text row (BOLD -> bright variant).
_ANSI_COLOR = {"BLACK": 30, "RED": 31, "GREEN": 32, "YELLOW": 33,
               "BLUE": 34, "PURPLE": 35, "CYAN": 36, "WHITE": 37}


def write_header_line(text, color_name="WHITE", bold=False, row=1):
    """Print a plain-width status/header line via raw ANSI, bypassing
    termgl's puts(). REQUIRED whenever Setting.DOUBLE_CHARS is enabled
    (every 3D pane in this project always enables it — see make_context):
    DOUBLE_CHARS doubles every character termgl draws, puts() included, so
    a header written via ctx.puts() renders as "[[ccooddeerr]]" — unreadable
    doubled text — instead of plain "[coder]". Call this AFTER ctx.flush()
    (so the 3D frame is already drawn) and it repositions the cursor to
    `row` via `\\033[<row>;1H` + clears to end-of-line via `\\033[K`,
    matching the same escape-sequence technique avatar_providers'
    ascii_avatar.py already uses for its own bubble overlay.
    """
    import sys
    code = _ANSI_COLOR.get(color_name, 37)
    bold_code = "1;" if bold else ""
    sys.stdout.write(f"\033[{row};1H\033[K\033[{bold_code}{code}m{text}\033[0m")
    sys.stdout.flush()
