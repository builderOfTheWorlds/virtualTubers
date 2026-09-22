#!/usr/bin/env python3
"""
gl_raster.py
GPU-accelerated counterpart to pixel_raster.py: same head data
(codec_head.build_codec_head's verts/faces/materials), same flat-shaded
MGS2-codec look, same render() call shape — but rasterized by the GPU via
moderngl's STANDALONE context instead of a pure-numpy scanline fill.

WHY. pixel_raster.render() benchmarks at ~12-14 fps at pane resolution
(docs/character_generator.md) — below the stream's 30fps target and a
non-starter for live (per-frame) rendering. A standalone moderngl context
needs no window, no X server, and no GLX extension flag on Xvfb: it's an
EGL/offscreen GL context, confirmed >2000fps at the same resolution and
triangle count on a desktop RTX 3080 (see docs/gl_raster_benchmark.md).

WHERE THIS RUNS. Local dev/testing only for now — the worker containers
this eventually targets do not have a GPU attached (see that same doc for
the open question of provisioning one there). `is_available()` gates every
entry point so a GPU-less environment (CI, a worker without passthrough)
falls back to pixel_raster.render() instead of crashing; see
`render_with_fallback()`.

Kept numerically aligned with pixel_raster.py on purpose: same camera/view
matrix math, same KEY_LIGHT/AMBIENT/CODEC_PALETTE constants (imported
directly, not duplicated), same flat-per-triangle lambert shading model.
Two renderers of the same scene description should look the same; if they
diverge, that is a bug in one of them, not an intentional style difference.
"""
import logging

import numpy as np

from pixel_raster import (
    AMBIENT,
    CODEC_PALETTE,
    KEY_LIGHT,
    TINT_CODEC_GREEN,
    make_camera_matrix,
    make_view_matrix,
)

log = logging.getLogger(__name__)
TRACE = 5


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


_VERT_SHADER = """
#version 330
uniform mat4 mvp;
in vec3 in_pos;
in vec3 in_normal;
in float in_mat;
flat out vec3 v_normal;
flat out float v_mat;
void main() {
    gl_Position = mvp * vec4(in_pos, 1.0);
    v_normal = in_normal;
    v_mat = in_mat;
}
"""

# `flat in` on both varyings is what makes this FLAT (one value per
# triangle, from its provoking vertex) rather than smoothly interpolated —
# the same shading model pixel_raster.render() computes per-triangle before
# rasterizing. palette/key_light/ambient are uniforms, not baked into the
# shader text, so CODEC_PALETTE/KEY_LIGHT/AMBIENT stay the single source of
# truth in pixel_raster.py.
_FRAG_SHADER = """
#version 330
flat in vec3 v_normal;
flat in float v_mat;
out vec4 f_color;
uniform vec3 tint;
uniform vec3 key_light;
uniform float ambient;
uniform float palette[8];
void main() {
    float lambert = max(dot(normalize(v_normal), key_light), 0.0);
    float intensity = (ambient + (1.0 - ambient) * lambert);
    int idx = int(v_mat + 0.5);
    intensity *= palette[idx];
    f_color = vec4(tint * intensity, 1.0);
}
"""

#: Module-level context cache — creating a standalone GL context is
#: expensive relative to a frame render; the live avatar path renders many
#: frames per process lifetime and must not pay context-creation cost each
#: time. Lazily created on first use, one per process.
_ctx = None
_prog = None
_available = None

#: Per-mesh GPU object cache, keyed by id(verts)/id(faces)/id(materials) +
#: (width, height). The head mesh is completely static for a character's
#: whole process lifetime (codec_avatar.FrameSource builds verts/faces
#: once at construction; only camera rotation changes per frame) so there
#: is no reason to allocate a fresh VAO/buffers/FBO on every render() call.
#: Diagnosed on gx10 (aarch64, llvmpipe software GL, no real GPU attached
#: to the container, 2026-09-22): per-frame create+release of GL objects
#: under Mesa's software rasterizer leaked ~4MB/frame (34GB RSS in under
#: 5 minutes at 30fps) and correlated with the renderer eventually
#: producing a solid black window with no exception raised anywhere to
#: catch — consistent with llvmpipe's internal state degrading under that
#: allocation churn, not a one-off fluke. Caching by array identity is
#: safe because FrameSource never replaces verts/faces/materials after
#: __init__; a differently-shaped or brand new mesh (different id()) just
#: gets its own cache entry, so nothing goes stale across characters.
_mesh_cache = {}


def _get_mesh_gpu_objects(ctx, prog, verts, faces, materials, width, height):
    key = (id(verts), id(faces), id(materials), width, height)
    cached = _mesh_cache.get(key)
    if cached is not None:
        return cached

    tri_verts = verts[faces]                                   # (M,3,3)
    tri_normals = np.cross(tri_verts[:, 1] - tri_verts[:, 0],
                           tri_verts[:, 2] - tri_verts[:, 0])   # (M,3)
    mag = np.linalg.norm(tri_normals, axis=1, keepdims=True)
    tri_normals = tri_normals / np.maximum(mag, 1e-9)
    tri_normals = np.repeat(tri_normals, 3, axis=0)             # (M*3,3)
    tri_mats = np.repeat(materials.astype(np.float32), 3)       # (M*3,)

    pos_buf = ctx.buffer(tri_verts.reshape(-1, 3).astype("f4").tobytes())
    normal_buf = ctx.buffer(tri_normals.astype("f4").tobytes())
    mat_buf = ctx.buffer(tri_mats.astype("f4").tobytes())
    vao = ctx.vertex_array(prog, [
        (pos_buf, "3f", "in_pos"),
        (normal_buf, "3f", "in_normal"),
        (mat_buf, "1f", "in_mat"),
    ])
    fbo = ctx.simple_framebuffer((width, height))

    entry = (vao, pos_buf, normal_buf, mat_buf, fbo)
    _mesh_cache[key] = entry
    return entry


def is_available():
    """True if a standalone GL context can be created here. Caches its
    result (an unavailable GPU doesn't become available mid-process) so
    every render_with_fallback() call after the first is free."""
    global _available
    if _available is None:
        try:
            _ensure_context()
            _available = True
        except Exception as exc:  # noqa: BLE001 — any GL/driver failure means "no"
            log.warning("gl_raster: no GPU context available (%r); "
                       "callers should fall back to pixel_raster", exc)
            _available = False
    return _available


def _ensure_context():
    global _ctx, _prog
    if _ctx is not None:
        return _ctx, _prog
    import moderngl
    ctx = moderngl.create_context(standalone=True)
    prog = ctx.program(vertex_shader=_VERT_SHADER, fragment_shader=_FRAG_SHADER)
    _ctx, _prog = ctx, prog
    log.info("gl_raster: GPU context ready (%s)", ctx.info.get("GL_RENDERER"))
    return _ctx, _prog


def render(verts, faces, materials, width=480, height=420, rot_x=0.06,
          rot_y=0.0, dist=3.05, fov=0.95, pan_y=0.0,
          tint=TINT_CODEC_GREEN, palette=CODEC_PALETTE):
    """Rasterize to an (H, W, 3) float array in 0..1, GPU-accelerated.

    Same signature and same output contract as pixel_raster.render() —
    apply_codec_screen()/write_png() work unchanged on the result.
    """
    _trace("render(%dx%d, rot_y=%.2f)", width, height, rot_y)
    import moderngl

    ctx, prog = _ensure_context()
    verts = np.asarray(verts, dtype=np.float32)
    faces = np.asarray(faces, dtype=np.int32)
    materials = np.asarray(materials, dtype=np.int32)

    # Buffers/VAO/FBO are cached per-mesh (see _get_mesh_gpu_objects) and
    # reused across every frame of a character's lifetime — only the
    # camera/tint uniforms change per call. Do NOT go back to allocating
    # these fresh every frame: that leaked ~4MB/frame under gx10's
    # llvmpipe software GL and correlated with the renderer eventually
    # going solid-black (see _mesh_cache's docstring above).
    vao, pos_buf, normal_buf, mat_buf, fbo = _get_mesh_gpu_objects(
        ctx, prog, verts, faces, materials, width, height)

    camera = make_camera_matrix(width, height, fov=fov)
    view = make_view_matrix(rot_x=rot_x, rot_y=rot_y, dist=dist, pan_y=pan_y)
    mvp = (camera @ view).astype("f4")

    padded_palette = list(palette) + [0.0] * (8 - len(palette))

    fbo.use()
    # DEPTH_TEST only — NOT CULL_FACE. Backface culling depends on this GL
    # implementation agreeing with pixel_raster.py's on which winding
    # direction counts as "front" (ctx.front_face = "ccw" assumes a Y-up
    # NDC / CCW-front convention). That held on the RTX 3080/mesa desktop
    # this was developed against, but broke silently on gx10 (aarch64,
    # different GPU/driver stack, 2026-09-22): every frame came back
    # near-black regardless of rotation — the signature of EVERY triangle
    # being (wrongly) treated as back-facing and culled, not an occasional
    # glitch. The Z-buffer alone is enough to get correct hidden-surface
    # removal on a closed mesh like this head — it doesn't depend on any
    # winding convention, so it can't silently disagree across drivers.
    # Costs a little overdraw (back faces get rasterized and z-tested, then
    # discarded), not correctness, on hardware that's already ~200x over
    # the realtime floor (docs/gl_raster_benchmark.md).
    ctx.enable(moderngl.DEPTH_TEST)
    fbo.clear(0.0, 0.0, 0.0, 1.0)
    prog["mvp"].write(mvp.T.tobytes())
    prog["tint"].value = tuple(float(c) for c in tint)
    prog["key_light"].value = tuple(float(c) for c in KEY_LIGHT)
    prog["ambient"].value = float(AMBIENT)
    prog["palette"].value = padded_palette
    vao.render(moderngl.TRIANGLES)

    raw = fbo.read(components=3, dtype="f4")
    rgb = np.frombuffer(raw, dtype=np.float32).reshape(height, width, 3)
    rgb = np.flipud(rgb).copy()  # GL's readback origin is bottom-left

    return np.clip(rgb, 0.0, 1.0)


def render_with_fallback(verts, faces, materials, **kwargs):
    """render() on GPU if available, else pixel_raster.render() on CPU.

    The one entry point termgl_avatar.py / character_preview.py should
    actually call — callers should not need to know or care which backend
    ran, only that they got a same-shaped RGB frame back.
    """
    if is_available():
        try:
            img = render(verts, faces, materials, **kwargs)
            # Sanity check, not just a happy-path try/except: a GPU frame
            # that renders without raising but comes back near-black is
            # STILL a failure, not a valid "dark" frame — the codec look
            # has no all-black pose (fbo.clear(0,0,0,1) is the failure
            # state itself: nothing got drawn). Diagnosed on gx10
            # (aarch64, different GPU/driver stack than the RTX 3080 this
            # was validated on, 2026-09-22): a plausible winding-order or
            # depth-state mismatch across GL implementations silently
            # culls every triangle, leaving the clear color on screen with
            # no exception raised anywhere to catch. render() succeeding
            # is necessary but not sufficient — this is the second check.
            if img.mean() < 0.01:
                log.warning(
                    "gl_raster.render() returned a near-black frame "
                    "(mean=%.4f) — treating as a GPU render failure (likely "
                    "a winding/depth-state mismatch on this GPU/driver, not "
                    "a real all-black pose) and falling back to pixel_raster",
                    img.mean())
            else:
                return img, "gpu"
        except Exception as exc:  # noqa: BLE001 — never let a GL hiccup drop a frame
            log.warning("gl_raster.render() failed mid-run (%r); "
                       "falling back to pixel_raster for this frame", exc)
    from pixel_raster import render as cpu_render
    return cpu_render(verts, faces, materials, **kwargs), "cpu"


__all__ = ["render", "render_with_fallback", "is_available"]
