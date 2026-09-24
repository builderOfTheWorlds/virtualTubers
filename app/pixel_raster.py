#!/usr/bin/env python3
"""
pixel_raster.py
Flat-shaded software rasterizer that renders a head to real PIXELS, in the
style of MGS2's codec portraits, plus a dependency-free PNG writer.

WHY PIXELS AT ALL. stream_supervisor.py streams with
`ffmpeg -f x11grab -i :99` — it captures the X DISPLAY as an image, not
terminal text. tmux/xterm is merely what currently happens to be drawn
there, so any X window placed over the avatar region is streamed at full
pixel resolution. The 55x24 character grid was a self-imposed limit, never
a platform one.

WHY FLAT SHADING. One tone per triangle is the whole PS2-era look: the
planes of the face separate into readable blocks. Smooth (Gouraud) normals
were tested first and actively concealed the facial structure — the head
rendered as an even gradient with no features.

Pure numpy, no Pillow: the PNG encoder here is zlib+struct, so this adds
no dependency to the worker image.
"""
import logging
import struct
import zlib

import numpy as np

log = logging.getLogger(__name__)
TRACE = 5


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


#: Per-material base colors, indexed by codec_head.MAT_*. Values are
#: MONOCHROME WEIGHTS, not RGB — the codec tint is applied afterwards, so
#: one palette serves green (codec), amber, or any per-character accent.
CODEC_PALETTE = np.array([
    1.00,   # MAT_SKIN
    0.10,   # MAT_EYE    — near-black; the eye must win against the skin
    0.16,   # MAT_BROW
    0.18,   # MAT_MOUTH
    0.55,   # MAT_HAIR
], dtype=np.float32)

#: Codec screen tint (the classic green CRT).
TINT_CODEC_GREEN = np.array([0.38, 1.00, 0.52], dtype=np.float32)
TINT_AMBER = np.array([1.00, 0.72, 0.22], dtype=np.float32)

#: The xterm background startup.sh launches the console with
#: (`-bg '#002b36'` — Solarized Dark base03).
#: The avatar is an X window sitting ON TOP of that console, not inside the
#: tmux grid, so anything it leaves unpainted reads as a hard black rectangle
#: cut out of the layout (see the 2026-09-22 screenshot). Compositing the
#: render onto this exact color instead makes the window's edges invisible —
#: the face appears to float in the terminal. Keep in sync with startup.sh.
CONSOLE_BG = np.array([0x00, 0x2B, 0x36], dtype=np.float32) / 255.0

#: Key light, normalized: front, above, slightly to the viewer's left.
#: Codec portraits are lit from the front so the face stays legible; the
#: elevation is what makes the brow shelf cast onto the sockets.
KEY_LIGHT = np.array([-0.35, 0.52, 0.78], dtype=np.float32)
KEY_LIGHT = KEY_LIGHT / np.linalg.norm(KEY_LIGHT)

AMBIENT = 0.26


def make_camera_matrix(width, height, fov=0.95, near=0.1, far=50.0, aspect=None):
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


def make_view_matrix(rot_x=0.0, rot_y=0.0, rot_z=0.0, dist=3.0, pan_y=0.0):
    cx, sx = np.cos(rot_x), np.sin(rot_x)
    cy, sy = np.cos(rot_y), np.sin(rot_y)
    cz, sz = np.cos(rot_z), np.sin(rot_z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float32)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float32)
    m = np.eye(4, dtype=np.float32)
    m[:3, :3] = rz @ ry @ rx
    m[1, 3] = -float(pan_y)
    m[2, 3] = -float(dist)
    return m


def render(verts, faces, materials, width=480, height=420, rot_x=0.06,
           rot_y=0.0, dist=3.05, fov=0.95, pan_y=0.0,
           tint=TINT_CODEC_GREEN, palette=CODEC_PALETTE):
    """Rasterize to an (H, W, 3) float array in 0..1. Flat-shaded."""
    _trace("render(%dx%d, rot_y=%.2f)", width, height, rot_y)
    verts = np.asarray(verts, dtype=np.float32)
    faces = np.asarray(faces, dtype=np.int32)
    materials = np.asarray(materials, dtype=np.int32)

    camera = make_camera_matrix(width, height, fov=fov)
    view = make_view_matrix(rot_x=rot_x, rot_y=rot_y, dist=dist, pan_y=pan_y)
    mvp = camera @ view

    world = verts[faces]
    hom = np.concatenate([verts, np.ones((len(verts), 1), np.float32)], axis=1)
    clip = hom @ mvp.T
    w = clip[:, 3]
    visible = w > 1e-6
    ndc = np.zeros((len(verts), 3), dtype=np.float32)
    ndc[visible] = clip[visible, :3] / w[visible, None]

    sx = (ndc[:, 0] * 0.5 + 0.5) * width
    sy = (1.0 - (ndc[:, 1] * 0.5 + 0.5)) * height
    screen = np.stack([sx, sy, ndc[:, 2]], axis=-1)

    # Flat shading: one intensity per triangle, computed up front.
    normals = np.cross(world[:, 1] - world[:, 0], world[:, 2] - world[:, 0])
    mag = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.maximum(mag, 1e-9)
    lambert = np.clip(normals @ KEY_LIGHT, 0.0, 1.0)
    intensity = AMBIENT + (1.0 - AMBIENT) * lambert
    intensity = intensity * palette[np.clip(materials, 0, len(palette) - 1)]

    frame = np.zeros((height, width), dtype=np.float32)
    zbuf = np.full((height, width), np.inf, dtype=np.float32)

    ok = visible[faces].all(axis=1) & (mag[:, 0] > 1e-9)
    for idx in np.nonzero(ok)[0]:
        _raster(frame, zbuf, screen[faces[idx]], float(intensity[idx]))

    rgb = frame[:, :, None] * tint[None, None, :]
    return np.clip(rgb, 0.0, 1.0)


def _raster(frame, zbuf, tri, value):
    """Z-buffered flat fill of one triangle."""
    height, width = frame.shape
    ax, ay = tri[0, 0], tri[0, 1]
    bx, by = tri[1, 0], tri[1, 1]
    cx, cy = tri[2, 0], tri[2, 1]

    area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if area >= -1e-9:      # screen y is down, so front faces are negative
        return

    min_x = max(0, int(np.floor(min(ax, bx, cx))))
    max_x = min(width - 1, int(np.ceil(max(ax, bx, cx))))
    min_y = max(0, int(np.floor(min(ay, by, cy))))
    max_y = min(height - 1, int(np.ceil(max(ay, by, cy))))
    if min_x > max_x or min_y > max_y:
        return

    ys, xs = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
    px, py = xs + 0.5, ys + 0.5
    w0 = ((bx - ax) * (py - ay) - (px - ax) * (by - ay)) / area
    w1 = ((cx - bx) * (py - by) - (px - bx) * (cy - by)) / area
    w2 = 1.0 - w0 - w1
    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if not inside.any():
        return

    depth = w2 * tri[0, 2] + w0 * tri[1, 2] + w1 * tri[2, 2]
    region = zbuf[min_y:max_y + 1, min_x:max_x + 1]
    write = inside & (depth < region)
    if not write.any():
        return
    region[write] = depth[write]
    frame[min_y:max_y + 1, min_x:max_x + 1][write] = value


# ── Codec screen post-processing ──────────────────────────────────────────
def apply_codec_screen(rgb, scanline_strength=0.30, vignette=0.55,
                       glow=0.35, noise=0.015, seed=0):
    """The CRT treatment: scanlines, bloom, vignette, grain.

    This is doing real work, not decoration — scanlines and bloom are a
    large part of why codec portraits read as characterful rather than as
    untextured PS2 models, and they hide facet stair-stepping for free.
    """
    _trace("apply_codec_screen(glow=%.2f)", glow)
    out = rgb.copy()
    height, width, _ = out.shape

    if glow > 0:
        # Cheap separable box blur as a bloom approximation.
        blurred = out.copy()
        for _ in range(2):
            pad = np.pad(blurred, ((2, 2), (2, 2), (0, 0)), mode="edge")
            blurred = (pad[:-4, 2:-2] + pad[4:, 2:-2] +
                       pad[2:-2, :-4] + pad[2:-2, 4:] + blurred) / 5.0
        out = np.clip(out + blurred * glow, 0.0, 1.0)

    if scanline_strength > 0:
        lines = np.ones(height, dtype=np.float32)
        lines[::2] = 1.0 - scanline_strength
        out *= lines[:, None, None]

    if vignette > 0:
        yy = np.linspace(-1.0, 1.0, height)[:, None]
        xx = np.linspace(-1.0, 1.0, width)[None, :]
        r = np.sqrt(xx ** 2 + yy ** 2) / np.sqrt(2.0)
        out *= np.clip(1.0 - vignette * r ** 2.2, 0.0, 1.0)[:, :, None]

    if noise > 0:
        rng = np.random.default_rng(seed)
        out += rng.normal(0.0, noise, size=(height, width, 1)).astype(np.float32)

    return np.clip(out, 0.0, 1.0)


def composite_on_background(rgb, background=CONSOLE_BG):
    """Blend a codec frame over a flat background color.

    WHY SCREEN BLEND, NOT ALPHA. The renderer has no alpha channel — the
    surround is simply un-drawn pixels left at the clear color (black), and
    apply_codec_screen's glow/vignette/grain deliberately bleed a little
    light into it. A hard "black == transparent" key would therefore clip
    that halo into a visible rectangle edge, which is the exact artifact
    this function exists to remove. A screen blend,
    ``1 - (1 - bg) * (1 - fg)``, maps fg=0 to EXACTLY the background color
    (so the window edge disappears into the console) while leaving bright
    face pixels essentially untouched and letting the CRT bloom fall off
    smoothly into the grey.

    Args:
        rgb: (H,W,3) float array in 0..1 — a rendered, post-processed frame.
        background: (3,) float array in 0..1, or None to skip compositing
            (returns the input unchanged — the old black-surround look).

    Returns:
        (H,W,3) float array in 0..1.
    """
    if background is None:
        return rgb
    bg = np.asarray(background, dtype=np.float32).reshape(1, 1, 3)
    _trace("composite_on_background(bg=%s)", bg.ravel())
    return np.clip(1.0 - (1.0 - bg) * (1.0 - rgb), 0.0, 1.0)


def parse_background(value, default=CONSOLE_BG):
    """Normalize a config-supplied background into a (3,) 0..1 array.

    Accepts what a YAML worker config can plausibly hold: ``None`` (use
    `default`), the strings ``"none"``/``"off"``/``"black"`` (disable
    compositing -> returns None), a ``"#002b36"``/``"002b36"`` hex string,
    or a 3-sequence of either 0..1 floats or 0..255 ints. Anything
    unparseable logs a warning and falls back to `default` rather than
    killing the avatar pane over a cosmetic setting.
    """
    if value is None:
        return default
    try:
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("none", "off", "transparent"):
                return None
            if text == "black":
                return np.zeros(3, dtype=np.float32)
            text = text.lstrip("#")
            if len(text) == 3:
                text = "".join(c * 2 for c in text)
            if len(text) != 6:
                raise ValueError(f"not a 6-digit hex color: {value!r}")
            return np.array(
                [int(text[i:i + 2], 16) for i in (0, 2, 4)],
                dtype=np.float32) / 255.0
        channels = [float(c) for c in value]
        if len(channels) != 3:
            raise ValueError(f"expected 3 channels, got {len(channels)}")
        arr = np.array(channels, dtype=np.float32)
        if arr.max() > 1.0:          # 0..255 ints
            arr = arr / 255.0
        return np.clip(arr, 0.0, 1.0)
    except Exception as exc:  # noqa: BLE001 — cosmetic; never fatal
        log.warning("parse_background(%r) failed (%r); using the default "
                    "console background instead", value, exc)
        return default


def write_png(path, rgb):
    """Write an (H,W,3) 0..1 float array as PNG. No Pillow dependency."""
    buf = (np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8)
    height, width, _ = buf.shape
    raw = b"".join(b"\x00" + buf[y].tobytes() for y in range(height))

    def chunk(tag, data):
        body = struct.pack(">I", len(data)) + tag + data
        return body + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    log.info("wrote %s (%dx%d)", path, width, height)
    return path


__all__ = ["render", "apply_codec_screen", "composite_on_background",
           "parse_background", "write_png", "CODEC_PALETTE",
           "TINT_CODEC_GREEN", "TINT_AMBER", "CONSOLE_BG"]
