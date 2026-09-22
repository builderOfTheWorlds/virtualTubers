#!/usr/bin/env python3
"""
ascii_raster.py
A small pure-numpy triangle rasterizer that reproduces what termgl draws,
as a plain character grid — no termgl, no TTY, no C extension.

WHY THIS EXISTS. termgl's `TGL()` constructor talks to a real terminal and
fails outright when there isn't one (OSError under a pipe, a Windows
console, or any non-tty), so it cannot be used to render a frame to a
string for an AI agent to read. The character-generator iteration loop
(docs/avatar_3d_design.md §5 step 3) needs exactly that: build a mesh from
sliders, render it, hand the result back to the agent as text, repeat.

FIDELITY. This deliberately mirrors render3d_common.py's pipeline rather
than inventing its own: same camera/view matrices, same flat-shaded
normal-dot-light intensity, same `max(0.15, dp*0.5+0.5)` floor, same
brightness->character ramp ordering, same backface culling and Z-buffer.
It is a PREVIEW of what the pane will show, accurate at the silhouette and
shading level that termgl's 55x24 flat output can express — not a
pixel-exact emulator of termgl's internals. Ground truth is always the
real pane in the container.
"""
import logging

import numpy as np

log = logging.getLogger(__name__)
TRACE = 5


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


#: termgl's `gradient_min` ramp, dark -> bright. Matches the ordering
#: LitPixelShader uses via `gradient.char(int(light_mul * 255))`.
GRADIENT_MIN = " .:;ox%#@"

#: Light direction, normalized — copied from LitPixelShader so preview
#: shading matches the pane's.
LIGHT_DIRECTION = np.array([0.4, 0.6, 1.0], dtype=np.float32)
LIGHT_DIRECTION = LIGHT_DIRECTION / np.linalg.norm(LIGHT_DIRECTION)


def make_camera_matrix(width, height, fov=1.2, near=0.1, far=10.0, aspect=None):
    """Perspective projection matrix equivalent to termgl's `tgl.camera()`.

    `aspect` defaults to width/height, which is correct when DOUBLE_CHARS
    is on (two terminal columns per rendered pixel => square pixels). With
    DOUBLE_CHARS off, a character cell is ~2x taller than wide, so the
    caller must pass aspect = (width/2)/height to avoid a stretched face.
    """
    if aspect is None:
        aspect = width / float(height)
    f = 1.0 / np.tan(fov * 0.5)
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2.0 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def make_view_matrix(rot_x=0.0, rot_y=0.0, rot_z=0.0, dist=3.0):
    """Orbit transform: rotate, then translate away along -Z. Mirrors
    render3d_common.make_view()'s rotate-then-translate ordering."""
    cx, sx = np.cos(rot_x), np.sin(rot_x)
    cy, sy = np.cos(rot_y), np.sin(rot_y)
    cz, sz = np.cos(rot_z), np.sin(rot_z)

    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float32)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float32)
    rot = rz @ ry @ rx

    m = np.eye(4, dtype=np.float32)
    m[:3, :3] = rot
    m[2, 3] = -float(dist)
    return m


def render_mesh(verts, faces, width=55, height=24, fov=1.2, dist=3.0,
                rot_x=0.4, rot_y=0.0, rot_z=0.0, gradient=GRADIENT_MIN,
                double_chars=True):
    """Rasterize an indexed mesh to a list of `height` strings.

    Returns plain text (no ANSI): the accent color is a single uniform
    attribute of the whole mesh in termgl's flat-shaded model, so it adds
    nothing to a silhouette/shading judgement and would only be noise in
    an agent's context.

    Each output row is `width` characters when double_chars is False, or
    `width * 2` when it's True (every rendered pixel occupies two terminal
    columns — termgl's DOUBLE_CHARS setting, which every 3D pane in this
    project enables; see docs/panels.md).
    """
    _trace("render_mesh(verts=%s, faces=%s, %dx%d)",
           np.shape(verts), np.shape(faces), width, height)
    verts = np.asarray(verts, dtype=np.float32)
    faces = np.asarray(faces, dtype=np.int32)

    aspect = width / float(height) if double_chars else (width * 0.5) / float(height)
    camera = make_camera_matrix(width, height, fov=fov, aspect=aspect)
    view = make_view_matrix(rot_x=rot_x, rot_y=rot_y, rot_z=rot_z, dist=dist)

    # World-space triangle corners, kept for lighting: LitPixelShader
    # computes its normal from the UNPROJECTED verts, so preview shading
    # must do the same or the light would move with the camera.
    world = verts[faces]                                    # (M,3,3)

    mvp = camera @ view
    homogeneous = np.concatenate(
        [verts, np.ones((len(verts), 1), dtype=np.float32)], axis=1)
    clip = homogeneous @ mvp.T                              # (N,4)

    w = clip[:, 3]
    visible = w > 1e-6
    ndc = np.zeros((len(verts), 3), dtype=np.float32)
    ndc[visible] = clip[visible, :3] / w[visible, None]

    # Screen space: x right, y DOWN (termgl's convention, see
    # render3d_common.ndc_to_screen).
    sx = (ndc[:, 0] * 0.5 + 0.5) * width
    sy = (1.0 - (ndc[:, 1] * 0.5 + 0.5)) * height
    screen = np.stack([sx, sy, ndc[:, 2]], axis=-1)

    frame = np.zeros((height, width), dtype=np.int16)       # 0 = background
    zbuf = np.full((height, width), np.inf, dtype=np.float32)

    # A triangle with any vertex behind the camera is dropped rather than
    # clipped: at this mesh scale and camera distance the head is always
    # fully in front of the near plane, and a real clipper would be a lot
    # of code for geometry that never occurs here.
    ok = visible[faces].all(axis=1)
    drawn = 0
    for tri_idx in np.nonzero(ok)[0]:
        if _raster_triangle(frame, zbuf, screen[faces[tri_idx]],
                            world[tri_idx], gradient):
            drawn += 1

    log.debug("rasterized %d/%d triangles into %dx%d", drawn, len(faces), width, height)

    rows = []
    for y in range(height):
        row = "".join(gradient[i] if i else " " for i in frame[y])
        rows.append(_double(row) if double_chars else row)
    return rows


def _raster_triangle(frame, zbuf, tri_screen, tri_world, gradient):
    """Z-buffered scanline fill of one triangle. Returns True if drawn."""
    height, width = frame.shape

    ax, ay = tri_screen[0, 0], tri_screen[0, 1]
    bx, by = tri_screen[1, 0], tri_screen[1, 1]
    cx, cy = tri_screen[2, 0], tri_screen[2, 1]

    # Signed area in screen space. y points DOWN, which flips the sign of
    # the cross product relative to a y-up convention — so a CCW-wound
    # front face lands here as NEGATIVE area. Cull the positive ones,
    # matching ctx.cull_face(Face.BACK, Winding.CCW).
    area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if area >= -1e-9:
        return False

    intensity = _face_intensity(tri_world)
    char_idx = 1 + int(intensity * (len(gradient) - 1.001))
    char_idx = max(1, min(len(gradient) - 1, char_idx))

    min_x = max(0, int(np.floor(min(ax, bx, cx))))
    max_x = min(width - 1, int(np.ceil(max(ax, bx, cx))))
    min_y = max(0, int(np.floor(min(ay, by, cy))))
    max_y = min(height - 1, int(np.ceil(max(ay, by, cy))))
    if min_x > max_x or min_y > max_y:
        return False

    ys, xs = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
    px = xs + 0.5
    py = ys + 0.5

    # Barycentric coordinates via edge functions, normalized by the signed
    # area so they sum to 1 inside the triangle.
    w0 = ((bx - ax) * (py - ay) - (px - ax) * (by - ay)) / area
    w1 = ((cx - bx) * (py - by) - (px - bx) * (cy - by)) / area
    w2 = 1.0 - w0 - w1
    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if not inside.any():
        return False

    depth = (w2 * tri_screen[0, 2] + w0 * tri_screen[1, 2] + w1 * tri_screen[2, 2])
    region_z = zbuf[min_y:max_y + 1, min_x:max_x + 1]
    write = inside & (depth < region_z)
    if not write.any():
        return False

    region_z[write] = depth[write]
    frame[min_y:max_y + 1, min_x:max_x + 1][write] = char_idx
    return True


def _face_intensity(tri_world):
    """Flat-shade intensity — same formula as LitPixelShader.pixel_shader,
    including its degenerate-triangle fallback and its 0.15 floor (which
    keeps unlit faces visible as texture rather than holes)."""
    ab = tri_world[1] - tri_world[0]
    ac = tri_world[2] - tri_world[0]
    cp = np.cross(ab, ac)
    mag = np.linalg.norm(cp)
    if mag < 1e-8:
        return 0.3
    dp = float(np.dot(LIGHT_DIRECTION, cp / mag))
    return max(0.15, dp * 0.5 + 0.5)


def _double(row):
    """Repeat every character — termgl's DOUBLE_CHARS, which makes pixels
    square given that a terminal cell is about twice as tall as wide."""
    return "".join(c * 2 for c in row)


def render_turntable(verts, faces, frames=4, **kwargs):
    """Render `frames` evenly-spaced views around the Y axis.

    A single view can hide real problems — a face that looks fine head-on
    can be badly wrong in profile, and the pane rotates continuously, so
    every angle is on screen eventually. Returns a list of row-lists.
    """
    _trace("render_turntable(frames=%d)", frames)
    kwargs.pop("rot_y", None)
    return [
        render_mesh(verts, faces, rot_y=2.0 * np.pi * i / frames, **kwargs)
        for i in range(frames)
    ]
