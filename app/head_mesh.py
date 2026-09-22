#!/usr/bin/env python3
"""
head_mesh.py
Backend A of the parametric character generator (docs/avatar_3d_design.md
§3): turns a flat 0..1 slider dict (character_schema.py) into a head mesh,
in pure numpy, with no termgl import — same rule as mesh3d.py, so the whole
generator is unit-testable without the C extension or a real terminal.

TOPOLOGY IS FIXED AND PARAMETER-INDEPENDENT. Every primitive below is
generated at a constant ring/segment resolution and then *deformed* by the
sliders; no builder ever adds, removes, or reorders a vertex based on a
parameter value. This is a hard invariant, not an implementation detail:
expression morph targets (docs/avatar_3d_design.md §4) lerp vertex arrays
pairwise, so vertex i must mean the same anatomical point for every
possible slider setting. tests/test_head_mesh.py asserts it across the
parameter space. If you add a feature here, add it unconditionally and
scale it to zero when it should be absent — never branch geometry on a
slider.

Design target (§2): the render is 55x24 flat-shaded characters with one
accent color, so this builds for SILHOUETTE AND PROPORTION only. Surface
detail below a few characters wide is invisible and not worth vertices.
"""
import logging

import numpy as np

from character_schema import resolve_params
from mesh3d import TRIG3D_DTYPE, trigs_from_indexed

log = logging.getLogger(__name__)
TRACE = 5


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


# ── Fixed resolution constants ────────────────────────────────────────────
# Tuned for the 55x24 target: enough segments that the silhouette reads as
# curved, few enough that the per-frame Python loop over triangles in
# termgl_avatar.render_tick() stays cheap. Changing any of these changes the
# vertex ORDERING, which invalidates any morph targets authored against the
# old numbers — treat them as a versioned part of the mesh contract.
SKULL_RINGS = 10
SKULL_SEGMENTS = 14
EYE_RINGS = 4
EYE_SEGMENTS = 6
CONE_SEGMENTS = 8
NECK_SEGMENTS = 10


def _uv_sphere(rings, segments):
    """Unit UV sphere as (verts, faces), outward-facing with CCW winding
    (render3d_common.make_context culls BACK faces with Winding.CCW).

    Poles are included as full rings of coincident vertices rather than
    single vertices. That costs a handful of degenerate (zero-area)
    triangles at each cap — which rasterize to nothing and are explicitly
    tolerated by LitPixelShader's `mag < 1e-8` guard — and buys a perfectly
    regular (rings+1) x (segments+1) vertex grid, which keeps indexing
    trivial and topology identical across every primitive instance.
    """
    theta = np.linspace(0.0, np.pi, rings + 1)          # 0 = +Y pole
    phi = np.linspace(0.0, 2.0 * np.pi, segments + 1)   # wraps: last == first
    t, p = np.meshgrid(theta, phi, indexing="ij")

    verts = np.stack([
        np.sin(t) * np.cos(p),
        np.cos(t),
        np.sin(t) * np.sin(p),
    ], axis=-1).reshape(-1, 3).astype(np.float32)

    faces = _grid_faces(rings, segments)
    return verts, faces


def _grid_faces(rings, segments):
    """Triangulate an (rings+1) x (segments+1) vertex grid stored row-major.

    Winding is chosen so that, for a grid whose rows run +Y pole -> -Y pole
    and whose columns run counter-clockwise when seen from +Y, the resulting
    normals point outward. _outward_fraction() in the tests pins this down.
    """
    stride = segments + 1
    i = np.arange(rings)[:, None]
    j = np.arange(segments)[None, :]

    top_left = (i * stride + j).ravel()
    top_right = (i * stride + j + 1).ravel()
    bottom_left = ((i + 1) * stride + j).ravel()
    bottom_right = ((i + 1) * stride + j + 1).ravel()

    tri_a = np.stack([top_left, top_right, bottom_left], axis=-1)
    tri_b = np.stack([top_right, bottom_right, bottom_left], axis=-1)
    return np.concatenate([tri_a, tri_b], axis=0).astype(np.int32)


def _cone(segments):
    """Unit cone as (verts, faces): apex at +Y, unit-radius base ring at
    y=0, plus a base cap. Used for ears and the nose — reoriented by the
    caller's transform, never regenerated at a different resolution."""
    phi = np.linspace(0.0, 2.0 * np.pi, segments + 1)
    ring = np.stack([np.cos(phi), np.zeros_like(phi), np.sin(phi)], axis=-1)
    apex = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)
    center = np.array([[0.0, 0.0, 0.0]], dtype=np.float32)
    verts = np.concatenate([ring, apex, center], axis=0).astype(np.float32)

    apex_idx = len(ring)
    center_idx = apex_idx + 1
    j = np.arange(segments)
    side = np.stack([j + 1, j, np.full(segments, apex_idx)], axis=-1)
    cap = np.stack([j, j + 1, np.full(segments, center_idx)], axis=-1)
    faces = np.concatenate([side, cap], axis=0).astype(np.int32)
    return verts, faces


def _cylinder(segments):
    """Unit cylinder as (verts, faces): radius 1, spanning y=0 (top) to
    y=-1 (bottom), with a bottom cap. Used for the neck."""
    phi = np.linspace(0.0, 2.0 * np.pi, segments + 1)
    top = np.stack([np.cos(phi), np.zeros_like(phi), np.sin(phi)], axis=-1)
    bottom = np.stack([np.cos(phi), -np.ones_like(phi), np.sin(phi)], axis=-1)
    cap_center = np.array([[0.0, -1.0, 0.0]], dtype=np.float32)
    verts = np.concatenate([top, bottom, cap_center], axis=0).astype(np.float32)

    stride = segments + 1
    j = np.arange(segments)
    side_a = np.stack([j + 1, j + stride, j], axis=-1)
    side_b = np.stack([j + 1 + stride, j + stride, j + 1], axis=-1)
    center_idx = 2 * stride
    cap = np.stack([j + 1 + stride, np.full(segments, center_idx), j + stride], axis=-1)
    faces = np.concatenate([side_a, side_b, cap], axis=0).astype(np.int32)
    return verts, faces


def _lerp(low, high, t):
    """Map a 0..1 slider onto a real-world range. Every slider->geometry
    mapping in this module goes through here, so the full set of ranges is
    readable in one place below."""
    return low + (high - low) * float(t)


def _shape_skull(verts, params):
    """Deform the unit sphere into a head: width, vertical taper, jaw flare.

    Operates in place on a copy — a pure vertex-position transform, which
    is exactly what keeps topology parameter-independent.
    """
    _trace("_shape_skull(params=%r)", params)
    v = verts.copy()
    y = v[:, 1]

    width = _lerp(0.62, 1.12, params["head_width"])
    depth = 0.88

    # Taper narrows the CRANIUM (y>0) as head_taper rises — an egg/oval
    # skull at 1.0, a blocky one at 0.0.
    upper = np.clip(y, 0.0, None)
    taper = 1.0 - _lerp(0.0, 0.45, params["head_taper"]) * upper

    # Jaw flares the lower half (y<0). Centered on 0.5 so the slider reads
    # as "narrower/wider than neutral" in both directions.
    lower = np.clip(-y, 0.0, None)
    jaw = 1.0 + (params["jaw_width"] - 0.5) * 0.85 * lower

    v[:, 0] *= width * taper * jaw
    v[:, 2] *= depth * taper * (1.0 + (params["jaw_width"] - 0.5) * 0.35 * lower)

    # Flatten the back of the skull slightly (z<0) so the head reads as a
    # head and not a ball when rotated side-on — the rotation is constant
    # in termgl_avatar, so the profile view is on screen half the time.
    back = v[:, 2] < 0
    v[back, 2] *= 0.86
    return v


def _place_eyes(params, skull_width):
    """Two eye spheres on the +Z face. Returns (verts, faces).

    Eyes are barely a couple of characters wide at 55x24, so these exist
    mainly to break up the front silhouette and to give morph targets
    something to move later — not to be legible as eyes on their own.
    """
    base_v, base_f = _uv_sphere(EYE_RINGS, EYE_SEGMENTS)
    radius = _lerp(0.09, 0.20, params["eye_size"])
    offset_x = _lerp(0.20, 0.46, params["eye_spacing"]) * skull_width
    y = 0.14
    # Far enough forward that the eye spheres break the skull's surface
    # instead of sitting inside it (the shaped sphere reaches z≈0.88 at
    # this height, and an eye is only ~0.1-0.2 across).
    z = 0.80

    verts = []
    faces = []
    for sign in (-1.0, 1.0):
        v = base_v * radius
        v[:, 0] += sign * offset_x
        v[:, 1] += y
        v[:, 2] += z
        faces.append(base_f + len(base_v) * len(verts))
        verts.append(v)
    return np.concatenate(verts, axis=0), np.concatenate(faces, axis=0)


def _place_nose(params):
    """A cone laid on its side pointing +Z (out of the face)."""
    base_v, base_f = _cone(CONE_SEGMENTS)
    length = _lerp(0.14, 0.50, params["nose_length"])
    radius = 0.13

    # Cone is built apex-at-+Y; rotate -90° about X so the apex points +Z.
    v = base_v.copy()
    v = np.stack([v[:, 0] * radius, -v[:, 2] * radius, v[:, 1] * length], axis=-1)
    v[:, 1] += -0.04
    # Base ring sits ON the skull surface (the shaped sphere reaches z≈0.88
    # at this height), so the whole cone length protrudes. Anchoring it any
    # further back buries the nose inside the skull and it vanishes from the
    # profile silhouette entirely — which is the one view where a nose reads.
    v[:, 2] += 0.80
    return v.astype(np.float32), base_f


def _place_ears(params, skull_width):
    """Two cones pointing ±X off the sides of the skull."""
    base_v, base_f = _cone(CONE_SEGMENTS)
    size = _lerp(0.10, 0.34, params["ear_size"])
    length = size * 1.25
    y = 0.06

    verts = []
    faces = []
    for sign in (-1.0, 1.0):
        v = base_v.copy()
        # Apex +Y -> apex ±X, with the base ring flattened against the skull.
        v = np.stack([
            sign * v[:, 1] * length,
            v[:, 0] * size,
            v[:, 2] * size,
        ], axis=-1)
        v[:, 0] += sign * skull_width * 0.92
        v[:, 1] += y
        faces.append(base_f + len(base_v) * len(verts))
        verts.append(v.astype(np.float32))
    return np.concatenate(verts, axis=0), np.concatenate(faces, axis=0)


def _place_neck(params):
    """A cylinder below the skull. `build` reads as overall heft: a thin
    neck on a light build, a thick one on a heavy build — the cheapest
    body cue available when only the head is in frame."""
    base_v, base_f = _cylinder(NECK_SEGMENTS)
    radius = _lerp(0.20, 0.46, params["build"])
    length = _lerp(0.30, 0.50, params["build"])

    v = base_v.copy()
    v[:, 0] *= radius
    v[:, 2] *= radius * 0.85
    v[:, 1] *= length
    v[:, 1] += -0.72
    return v.astype(np.float32), base_f


def build_head_params_mesh(params):
    """Build the head in INDEXED form: returns (verts (N,3), faces (M,3)).

    This is the form morph targets need (docs/avatar_3d_design.md §4) —
    two poses of the same character share `faces` exactly and differ only
    in `verts`, so lerping is a single numpy expression. Use
    build_head_mesh() for the flat Trig3D array termgl consumes.
    """
    _trace("build_head_params_mesh(params=%r)", params)
    params = resolve_params(params)

    skull_v, skull_f = _uv_sphere(SKULL_RINGS, SKULL_SEGMENTS)
    skull_v = _shape_skull(skull_v, params)
    skull_width = _lerp(0.62, 1.12, params["head_width"])

    parts = [(skull_v, skull_f)]
    parts.append(_place_eyes(params, skull_width))
    parts.append(_place_nose(params))
    parts.append(_place_ears(params, skull_width))
    parts.append(_place_neck(params))

    verts = []
    faces = []
    offset = 0
    for part_v, part_f in parts:
        verts.append(np.asarray(part_v, dtype=np.float32))
        faces.append(np.asarray(part_f, dtype=np.int32) + offset)
        offset += len(part_v)

    all_verts = np.concatenate(verts, axis=0)
    all_faces = np.concatenate(faces, axis=0)
    log.debug("built head: %d verts, %d faces", len(all_verts), len(all_faces))
    return all_verts, all_faces


def build_head_mesh(params=None, scale=1.0):
    """Public Backend A entry point: slider dict -> TRIG3D_DTYPE array.

    Drop-in replacement for mesh3d.build_icosahedron() in
    avatar_providers/termgl_avatar.py. `params` may be a full slider dict,
    a partial one (missing sliders take their defaults), a preset name, or
    None for the neutral default face — see character_schema.resolve_params.
    """
    _trace("build_head_mesh(params=%r, scale=%s)", params, scale)
    verts, faces = build_head_params_mesh(params)
    if scale != 1.0:
        verts = verts * float(scale)
    trigs = trigs_from_indexed(verts, faces)
    _trace("build_head_mesh -> %d trigs", len(trigs))
    return trigs


def lerp_verts(verts_a, verts_b, t):
    """Blend two poses of the SAME topology (morph targets, §4).

    Raises ValueError on a shape mismatch — silently broadcasting two
    different-topology meshes would produce a scrambled face rather than
    an obvious failure.
    """
    a = np.asarray(verts_a, dtype=np.float32)
    b = np.asarray(verts_b, dtype=np.float32)
    if a.shape != b.shape:
        raise ValueError(
            f"morph targets must share topology; got {a.shape} vs {b.shape}"
        )
    t = float(np.clip(t, 0.0, 1.0))
    return (a * (1.0 - t) + b * t).astype(np.float32)


__all__ = [
    "build_head_mesh",
    "build_head_params_mesh",
    "lerp_verts",
    "TRIG3D_DTYPE",
]
