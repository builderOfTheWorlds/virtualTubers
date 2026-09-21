#!/usr/bin/env python3
"""
render3d_common.py — shared termgl context/camera setup for the three pane
prototypes (radar, knowledge graph, avatar). Prototype code, not yet wired
into app/*.py — see docs/panels.md once promoted out of
.claude/prompts/tuber_base_mockups/.
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


def make_camera(width, height, fov=1.3, near=0.1, far=10.0):
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
    Same technique as pyTermGL's own demo_teapot.py."""
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
    coords, for drawing 2D overlays (edge lines, labels) alongside 3D
    triangle_3d() meshes — termgl's line()/puts() take screen-space verts,
    not 3D world verts, so callers needing both (e.g. knowledge graph edges)
    must do this projection themselves.
    """
    p4 = np.array([point[0], point[1], point[2], 1.0], dtype=np.float32)
    clip = mvp @ p4
    if clip[3] == 0:
        return None
    ndc = clip[:3] / clip[3]
    return ndc  # caller maps ndc.xy [-1,1] -> screen pixels
