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
# The skull is dense enough to carve recognizable FEATURES into (eye
# sockets, brow ridge, mouth) rather than merely reading as a curved
# silhouette — at 10x14 the sockets spanned barely one vertex each and the
# face rendered as a smooth ball. Still cheap: ~1.2k triangles through a
# per-frame Python loop in termgl_avatar.render_tick().
#
# Changing any of these changes the vertex ORDERING, which invalidates any
# morph targets authored against the old numbers — treat them as a
# versioned part of the mesh contract.
SKULL_RINGS = 22
SKULL_SEGMENTS = 30
EYE_RINGS = 5
EYE_SEGMENTS = 8
CONE_SEGMENTS = 10
NECK_SEGMENTS = 12

#: Light direction the FACE is designed to be read under, normalized.
#: render3d_common.LitPixelShader's stock light is nearly parallel to the
#: view axis, which saturates the entire visible hemisphere at full
#: brightness — the face collapsed onto the ramp's last character and read
#: as a flat blob.
#:
#: This light is ABOVE and slightly in front, and deliberately SYMMETRIC
#: (x=0). A side/raking light looked better in the abstract but washes one
#: cheek bright and the other dark, and that left-right gradient competes
#: with the features for the few ramp levels available — measurably noisier
#: output. Lighting straight down the face's own symmetry plane spends the
#: whole ramp on brow/socket/muzzle relief instead, which is what actually
#: reads as a face. Both the pane and the ASCII preview use this.
FACE_LIGHT_DIRECTION = np.array([0.0, 0.75, 0.66], dtype=np.float32)
FACE_LIGHT_DIRECTION = FACE_LIGHT_DIRECTION / np.linalg.norm(FACE_LIGHT_DIRECTION)


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


def _falloff(distance_sq, radius):
    """Smooth 0..1 bump kernel: 1 at the center, 0 at `radius`, with zero
    slope at both ends.

    Every facial feature below is applied by displacing vertices through
    this kernel rather than by adding or moving geometry. That is what lets
    a face be sculpted while keeping topology bit-identical across all
    slider values (the morph-target invariant, docs/avatar_3d_design.md §4)
    — and the smooth falloff is what stops a displacement from creasing
    into a faceted dent that reads as a rendering artifact.
    """
    t = np.clip(1.0 - distance_sq / (radius * radius), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)      # smoothstep


def _sculpt_face(verts, params):
    """Carve facial structure into the shaped skull.

    A sphere with features stuck ON it reads as a ball with bumps; a face
    reads through what is carved INTO it — recessed eye sockets, an
    overhanging brow, a flattened facial plane, a mouth line. Under a
    raking light those hollows generate the light/dark gradients a viewer
    decodes as a face, which is the only signal that survives a 55x24
    flat-shaded render.

    FEATURES ARE FEW, WIDE AND DEEP ON PURPOSE. Flat shading gives every
    triangle its own tone, so a displacement that varies faster than the
    triangle spacing turns into speckle rather than shape — narrow or
    overlapping bumps measurably raise the neighbouring-character change
    rate and read as noise. Each feature here is therefore broad (low
    spatial frequency) and displaces far enough to survive a 9-level ramp.

    Pure vertex displacement: no vertex is added, removed, or reordered.
    """
    _trace("_sculpt_face(params=%r)", params)
    v = verts.copy()
    x, y, z = v[:, 0], v[:, 1], v[:, 2]

    # Only the front hemisphere is a face; everything else is cranium. The
    # squared weight keeps the displacement from creasing at the silhouette
    # edge, where a linear falloff leaves a visible seam.
    front = np.clip(z, 0.0, None) ** 2

    eye_x = _lerp(0.26, 0.44, params["eye_spacing"])
    eye_y = 0.10

    # ── Flatten the facial plane. A sphere's front is too round to read as
    # a face; real faces are comparatively flat from brow to chin. This is
    # one broad displacement, so it costs no sharpness anywhere.
    face_plane = _falloff(x * x * 0.62 + (y - 0.02) ** 2 * 0.42, 1.30) * front
    v[:, 2] -= face_plane * 0.20

    # ── Eye sockets: the single most important feature, and the one that
    # must survive downsampling — a viewer reads two dark hollows as eyes
    # before anything else. Deep and wide: a parameter sweep scoring
    # eye-band-vs-cheek-band contrast against speckle put the usable depth
    # far higher than looked reasonable on paper, because a shallow socket
    # simply lands on the same ramp character as the cheek beside it.
    socket_r = _lerp(0.40, 0.50, params["eye_size"])
    socket_depth = _lerp(0.30, 0.40, params["eye_size"])
    for sign in (-1.0, 1.0):
        d_sq = (x - sign * eye_x) ** 2 * 0.80 + (y - eye_y) ** 2 * 1.70
        v[:, 2] -= _falloff(d_sq, socket_r) * front * socket_depth

    # ── Brow ridge: overhangs the sockets and catches the overhead light,
    # which is what makes the hollows beneath read as shadowed sockets
    # rather than as dents.
    brow_y = eye_y + _lerp(0.20, 0.27, params["eye_size"])
    brow = _falloff((y - brow_y) ** 2 * 3.4 + x * x * 0.26, 0.74) * front
    v[:, 2] += brow * 0.20

    # ── Muzzle: the whole nose/mouth/chin block pushed forward as ONE
    # broad mass. Individually-placed cheek and chin bumps interfered with
    # each other and speckled; a single mass reads as facial structure and
    # stays smooth.
    muzzle = _falloff(x * x * 1.05 + (y + 0.30) ** 2 * 1.25, 0.90) * front
    v[:, 2] += muzzle * 0.105

    # ── Mouth: a broad, shallow horizontal shadow under the muzzle. At this
    # resolution a mouth-shaped hole reads as damage; a soft band reads as
    # a mouth.
    mouth_w = _lerp(0.30, 0.46, params["jaw_width"])
    mouth = _falloff((x / mouth_w) ** 2 * 0.10 + (y + 0.36) ** 2 * 6.0, 0.42) * front
    v[:, 2] -= mouth * 0.075

    log.debug("sculpted face: socket_depth=%.3f", socket_depth)
    return v


def _surface_z(verts, x_query, y_query, radius=0.16, default=0.70):
    """Front-surface Z of the sculpted skull near (x_query, y_query).

    Features are attached using this rather than a hardcoded constant: the
    sculpt moves the facial plane back and hollows the sockets, so a fixed
    anchor that was correct for a plain sphere leaves the nose floating in
    front of the face (a detached cone reads as a rendering glitch, and the
    seam speckles). Querying the real surface keeps attachments welded
    however the sliders reshape the head.
    """
    d_sq = (verts[:, 0] - x_query) ** 2 + (verts[:, 1] - y_query) ** 2
    near = (d_sq < radius * radius) & (verts[:, 2] > 0.0)
    if not near.any():
        return default
    return float(verts[near, 2].max())


def _place_eyes(params, skull_width, skull_verts=None):
    """Two eyeballs seated in the sculpted sockets. Returns (verts, faces).

    Kept small and set deep: the SOCKET does the visual work at 55x24 (a
    dark hollow under a lit brow is what reads as an eye), while the
    eyeball only needs to catch a highlight inside it. An eyeball large
    enough to be legible on its own fills the socket and undoes it.
    """
    base_v, base_f = _uv_sphere(EYE_RINGS, EYE_SEGMENTS)
    radius = _lerp(0.085, 0.135, params["eye_size"])
    offset_x = _lerp(0.26, 0.44, params["eye_spacing"])
    y = 0.10

    verts = []
    faces = []
    for sign in (-1.0, 1.0):
        v = base_v * radius
        v[:, 2] *= 0.62        # flattened; a full sphere overflows the socket
        v[:, 0] += sign * offset_x
        v[:, 1] += y
        # Sit just proud of the socket FLOOR, wherever the sculpt put it.
        if skull_verts is not None:
            floor = _surface_z(skull_verts, sign * offset_x, y, radius=0.13)
        else:
            floor = 0.62
        v[:, 2] += floor - radius * 0.32
        faces.append(base_f + len(base_v) * len(verts))
        verts.append(v)
    return np.concatenate(verts, axis=0), np.concatenate(faces, axis=0)


def _place_nose(params, skull_verts=None):
    """A wedge-shaped nose rooted in the face, pointing +Z.

    Anchored to the ACTUAL sculpted surface: the facial plane is flattened
    and set back by _sculpt_face, so a hardcoded Z left the nose hovering
    in front of the face as a detached cone.
    """
    base_v, base_f = _cone(CONE_SEGMENTS)
    length = _lerp(0.16, 0.42, params["nose_length"])
    radius = _lerp(0.11, 0.16, params["nose_length"])
    y = -0.06

    # Cone is built apex-at-+Y; rotate so the apex points +Z and squash it
    # horizontally — a round cone reads as a ball on the face, a wedge
    # reads as a nose.
    v = base_v.copy()
    v = np.stack([v[:, 0] * radius * 0.80,
                  -v[:, 2] * radius,
                  v[:, 1] * length], axis=-1)
    v[:, 1] += y
    # Root the base ring slightly INSIDE the surface so the join is welded
    # rather than a visible floating seam.
    root = _surface_z(skull_verts, 0.0, y, radius=0.20) if skull_verts is not None else 0.70
    v[:, 2] += root - 0.06
    # Tilt the apex down a little; a horizontal nose reads as a spike.
    v[:, 1] -= (v[:, 2] - (root - 0.06)) * 0.20
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
    skull_v = _sculpt_face(skull_v, params)
    skull_width = _lerp(0.62, 1.12, params["head_width"])

    parts = [(skull_v, skull_f)]
    parts.append(_place_eyes(params, skull_width, skull_verts=skull_v))
    parts.append(_place_nose(params, skull_verts=skull_v))
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
