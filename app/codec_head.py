#!/usr/bin/env python3
"""
codec_head.py
Low-poly stylized head in the spirit of MGS2's codec portraits — the
replacement for head_mesh.py's smooth-sphere-plus-blobs approach, which
failed at every resolution (see docs/character_generator.md).

WHY THIS LOOKS DIFFERENT FROM head_mesh.py:

1. RING TOPOLOGY ALIGNED TO FACIAL LANDMARKS. Rings sit exactly at the
   brow, eye line, cheekbone, nose base and mouth, so a feature displaces
   a whole edge loop instead of smearing across an arbitrary tessellation.
   This is how low-poly heads are actually modeled.

2. SUPERELLIPSE CROSS-SECTIONS, not circles. Gives the angular, planar
   skull of a PS2-era model rather than a ball.

3. FLAT SHADING IS THE POINT. Each facet renders as one tone, so the
   planes of the face (forehead, cheek, jaw) separate into readable
   blocks. Smooth normals were actively hiding the structure.

4. DARK MATERIALS FOR FEATURES. MGS2's faces carried their eyes, brows
   and mouth in the TEXTURE, not the silhouette. With no texturing we
   approximate it with per-face material IDs shaded dark — which is what
   makes eyes read as eyes instead of as dents.

Topology stays parameter-independent (no branching on a slider value), so
the morph-target design in docs/avatar_3d_design.md §4 still holds.
"""
import logging

import numpy as np

from character_schema import resolve_params

log = logging.getLogger(__name__)
TRACE = 5


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


# ── Material IDs (index into pixel_raster.CODEC_PALETTE) ──────────────────
MAT_SKIN = 0
MAT_EYE = 1       # eyeball / eye slot — darkest
MAT_BROW = 2      # eyebrow bar
MAT_MOUTH = 3     # mouth line
MAT_HAIR = 4      # cranium cap

#: Segments around the head. 20 keeps the facet count PS2-ish (~600 tris)
#: while giving enough columns to place eyes off-center cleanly.
SEGMENTS = 20

#: Superellipse exponent: 1.0 = circle, lower = boxier. 0.72 reads as a
#: stylized angular skull without becoming a literal cube.
SUPER_E = 0.72

#: Head cross-sections from crown to jaw: (y, width, depth, tag).
#:
#: PROPORTIONS MATTER MORE THAN FEATURES. The first version had a
#: height/width ratio of 0.93 — geometrically a ball — and no amount of
#: eye/brow detail rescues a head that is as wide as it is tall. A real
#: head is ~1.4x taller than wide, and the face occupies only the lower
#: ~55% (the cranium above the brow is a big smooth mass). Widths here are
#: deliberately small relative to the y-span to hit that ratio.
#:
#: The tags are landmarks the feature code looks up by name — keeping the
#: heights in one table is what lets features land exactly on edge loops.
RING_PROFILE = [
    (1.30, 0.20, 0.22, "crown"),
    (1.18, 0.42, 0.46, "skull_top"),
    (1.00, 0.56, 0.62, "skull"),
    (0.78, 0.63, 0.69, "forehead"),
    (0.56, 0.66, 0.72, "forehead_low"),
    (0.38, 0.67, 0.74, "brow"),
    (0.22, 0.66, 0.73, "eye_top"),
    (0.06, 0.65, 0.72, "eye_low"),
    (-0.10, 0.65, 0.71, "cheek"),
    (-0.30, 0.62, 0.68, "nose_base"),
    (-0.48, 0.57, 0.62, "upper_lip"),
    (-0.62, 0.52, 0.57, "mouth"),
    (-0.80, 0.44, 0.49, "chin"),
    (-0.98, 0.32, 0.37, "jaw"),
    (-1.12, 0.18, 0.22, "jaw_base"),
]

RING_TAGS = {tag: i for i, (_, _, _, tag) in enumerate(RING_PROFILE)}
NUM_RINGS = len(RING_PROFILE)


def _superellipse(phi, exponent):
    """Angular cross-section. phi=0 is front-center (+Z), increasing phi
    sweeps toward +X (the character's left)."""
    c, s = np.cos(phi), np.sin(phi)
    x = np.sign(s) * np.abs(s) ** exponent
    z = np.sign(c) * np.abs(c) ** exponent
    return x, z


def _lerp(low, high, t):
    return low + (high - low) * float(t)


def _build_rings(params):
    """The base skull as a (NUM_RINGS, SEGMENTS, 3) grid."""
    _trace("_build_rings(params=%r)", params)
    width_mul = _lerp(0.86, 1.18, params["head_width"])
    taper = _lerp(0.0, 0.30, params["head_taper"])
    jaw_mul = _lerp(0.78, 1.22, params["jaw_width"])

    phi = np.arange(SEGMENTS) * (2.0 * np.pi / SEGMENTS)
    sx, sz = _superellipse(phi, SUPER_E)

    grid = np.zeros((NUM_RINGS, SEGMENTS, 3), dtype=np.float32)
    cheek_i = RING_TAGS["cheek"]
    for i, (y, w, d, _tag) in enumerate(RING_PROFILE):
        # Taper narrows the cranium; jaw_width flares everything below the
        # cheekbone. Both are smooth in i so the silhouette stays clean.
        above = max(0.0, (y - 0.0)) * taper
        below = 1.0 if i >= cheek_i else 0.0
        jaw_f = 1.0 + (jaw_mul - 1.0) * below * min(1.0, (i - cheek_i + 1) / 3.0)

        ring_w = w * width_mul * (1.0 - above) * jaw_f
        ring_d = d * (1.0 - above * 0.7)
        grid[i, :, 0] = sx * ring_w
        grid[i, :, 1] = y
        grid[i, :, 2] = sz * ring_d
    return grid


def _flatten_face(grid):
    """Flatten the frontal planes.

    A revolved profile is still too round to read as a face. Pulling the
    front-facing columns toward a common plane creates the flat forehead,
    cheek and jaw panels that flat shading then separates into distinct
    tones — the core of the codec-portrait look.
    """
    x = grid[:, :, 0]
    z = grid[:, :, 2]
    front = np.clip(z / (np.abs(z).max() + 1e-9), 0.0, None)
    # How far forward this column sits relative to the ring's own front.
    plane = z.max(axis=1, keepdims=True) * 0.93
    blend = (front ** 1.6) * 0.55
    grid[:, :, 2] = z * (1.0 - blend) + plane * blend
    # Slight inward bevel on the cheeks so the face has corners, not a curve.
    grid[:, :, 0] = x * (1.0 - 0.06 * front)
    return grid


def _feature_mask(grid, ring_lo, ring_hi, x_center, x_half, softness=0.35):
    """Smooth 0..1 weight over a rectangular patch of the ring grid.

    Works in GRID space (ring index, segment) rather than world space so a
    feature always covers the same loops regardless of how the sliders
    reshape the head — the low-poly equivalent of an edge-loop selection.
    """
    rings = np.arange(NUM_RINGS)[:, None]
    ring_mid = (ring_lo + ring_hi) * 0.5
    ring_half = max(0.5, (ring_hi - ring_lo) * 0.5)
    r_w = np.clip(1.0 - np.abs(rings - ring_mid) / (ring_half + softness), 0.0, 1.0)

    x = grid[:, :, 0]
    z = grid[:, :, 2]
    x_w = np.clip(1.0 - np.abs(x - x_center) / max(x_half, 1e-6), 0.0, 1.0)
    front = np.clip(z, 0.0, None) / (z.max() + 1e-9)
    return r_w * x_w * (front ** 1.2)


def _sculpt(grid, params):
    """Push the face's defining planes: brow shelf, sockets, nose, mouth."""
    _trace("_sculpt(params=%r)", params)
    eye_x = _lerp(0.26, 0.42, params["eye_spacing"])
    depth = grid[:, :, 2].max()

    # ── Brow shelf: the strongest single cue. A hard forward ledge over the
    # eyes, which flat shading turns into a bright band above a dark one.
    brow = _feature_mask(grid, RING_TAGS["brow"] - 0.4, RING_TAGS["brow"] + 0.4,
                         0.0, 0.62, softness=0.55)
    grid[:, :, 2] += brow * depth * 0.10

    # ── Eye sockets: recessed hard, directly under the shelf.
    for sign in (-1.0, 1.0):
        socket = _feature_mask(grid, RING_TAGS["eye_top"], RING_TAGS["eye_low"],
                               sign * eye_x, 0.30, softness=0.45)
        grid[:, :, 2] -= socket * depth * _lerp(0.16, 0.24, params["eye_size"])

    # ── Nose: built from an explicit vertical profile along the centre
    # columns. An earlier version summed several overlapping masks and
    # produced a flat triangular flap stuck on the front of the face
    # instead of a nose, because the masks stacked additively at the
    # centre line. Here each ring gets ONE target projection, interpolated
    # between named landmarks, so the bridge runs root -> tip -> base and
    # actually shows in profile.
    # Magnitudes are set so the TIP CLEARS THE BROW in world Z: the ring
    # profile narrows toward the jaw, so the nose must overcome both the
    # brow shelf and that taper or the profile silhouette stays flat
    # (measured: brow 0.784 vs tip 0.780 before this was tuned).
    nose_len = _lerp(0.20, 0.38, params["nose_length"])
    nose_profile = {
        RING_TAGS["brow"]: 0.04,          # root, barely proud between the brows
        RING_TAGS["eye_top"]: 0.20,
        RING_TAGS["eye_low"]: 0.46,
        RING_TAGS["cheek"]: 0.80,
        RING_TAGS["nose_base"]: 1.00,     # tip — the lowest and furthest point
        RING_TAGS["upper_lip"]: 0.10,     # falls away sharply under the tip
    }
    half_widths = {
        RING_TAGS["brow"]: 0.11,
        RING_TAGS["eye_top"]: 0.10,
        RING_TAGS["eye_low"]: 0.11,
        RING_TAGS["cheek"]: 0.13,
        RING_TAGS["nose_base"]: 0.16,
        RING_TAGS["upper_lip"]: 0.14,
    }
    x = grid[:, :, 0]
    for ring, amount in nose_profile.items():
        hw = half_widths[ring]
        # Triangular falloff across the bridge width gives the nose flat
        # side planes, which flat shading renders as distinct tones.
        across = np.clip(1.0 - np.abs(x[ring]) / hw, 0.0, 1.0)
        facing = np.clip(grid[ring, :, 2], 0.0, None)
        facing = facing / (facing.max() + 1e-9)
        grid[ring, :, 2] += across * (facing ** 0.5) * depth * nose_len * amount

    # Nostril wings: slight outward flare so the base has width.
    wing_ring = RING_TAGS["nose_base"]
    wing = np.clip(1.0 - np.abs(np.abs(x[wing_ring]) - 0.15) / 0.10, 0.0, 1.0)
    grid[wing_ring, :, 0] += np.sign(x[wing_ring]) * wing * 0.025

    # ── Cheekbones: angular pads that catch the key light.
    for sign in (-1.0, 1.0):
        cheek = _feature_mask(grid, RING_TAGS["cheek"] - 0.5, RING_TAGS["cheek"] + 0.5,
                              sign * _lerp(0.44, 0.60, params["head_width"]), 0.30)
        grid[:, :, 2] += cheek * depth * 0.05
        grid[:, :, 0] += sign * cheek * 0.045

    # ── Mouth recess + chin.
    mouth = _feature_mask(grid, RING_TAGS["mouth"] - 0.4, RING_TAGS["mouth"] + 0.4,
                          0.0, _lerp(0.26, 0.38, params["jaw_width"]))
    grid[:, :, 2] -= mouth * depth * 0.045
    chin = _feature_mask(grid, RING_TAGS["chin"], RING_TAGS["chin"] + 0.6, 0.0, 0.30)
    grid[:, :, 2] += chin * depth * 0.04
    return grid


def _grid_faces(num_rings, segments, offset=0):
    """Quads (as triangle pairs) over the ring grid, wrapping in phi."""
    faces = []
    for i in range(num_rings - 1):
        for j in range(segments):
            j2 = (j + 1) % segments
            a = offset + i * segments + j
            b = offset + i * segments + j2
            c = offset + (i + 1) * segments + j2
            d = offset + (i + 1) * segments + j
            faces.append((a, b, c))
            faces.append((a, c, d))
    return faces


def _cap(grid_verts, ring_index, segments, offset, center_index):
    """Fan-close a ring (crown / jaw base) onto a centre vertex."""
    faces = []
    for j in range(segments):
        j2 = (j + 1) % segments
        a = offset + ring_index * segments + j
        b = offset + ring_index * segments + j2
        faces.append((a, b, center_index))
    return faces


def _quad(verts_list, corners):
    """Append a quad's 4 verts, return its two triangles."""
    base = len(verts_list)
    verts_list.extend(corners)
    return [(base, base + 1, base + 2), (base, base + 2, base + 3)]


def _front_z_at(grid, y_target, x_target):
    """Front-surface Z of the sculpted head near (x_target, y_target).

    Feature quads are placed against this rather than a fraction of the
    head's max depth. The nose pushes the centre columns much further
    forward than the cheeks, so a single global depth put the eye quads
    behind the surface at the centre and in front of it at the edges —
    which is exactly the clipping seen in the first render.
    """
    ys = grid[:, 0, 1]
    ring = int(np.argmin(np.abs(ys - y_target)))
    row = grid[ring]
    col = int(np.argmin(np.abs(row[:, 0] - x_target) + (row[:, 2] < 0) * 1e3))
    return float(row[col, 2])


def _project_to_surface(grid, x, y, offset=0.012):
    """Project a feature-quad corner onto the head's actual front surface.

    WHY NOT A FLAT QUAD AT CONSTANT Z: a flat decal stays flat while the
    head turns, so at three-quarter view the far eye slid off the edge of
    the face and the features read as stickers on a mask. Shaping each
    corner to the local surface wraps the feature around the head, so it
    occludes and foreshortens correctly under rotation.
    """
    return (x, y, _front_z_at(grid, y, x) + offset)


def _surface_quad(verts, faces, mats, grid, corners, material, offset=0.012):
    """Append a quad whose corners are wrapped onto the head surface."""
    shaped = [_project_to_surface(grid, cx, cy, offset) for cx, cy in corners]
    for tri in _quad(verts, shaped):
        faces.append(tri)
        mats.append(material)


def _add_eyes(verts, faces, mats, params, grid):
    """Dark eye slots set into the sockets.

    These are surface-wrapped quads, not spheres: at codec scale an eye is
    a dark rectangle, and a sphere produced a shiny bead that read as a
    bug's eye.
    """
    eye_x = _lerp(0.26, 0.42, params["eye_spacing"])
    half_w = _lerp(0.085, 0.125, params["eye_size"])
    half_h = _lerp(0.045, 0.070, params["eye_size"])
    y_top = RING_PROFILE[RING_TAGS["eye_top"]][0]
    y_low = RING_PROFILE[RING_TAGS["eye_low"]][0]
    y_mid = (y_top + y_low) * 0.5

    for sign in (-1.0, 1.0):
        cx = sign * eye_x
        _surface_quad(verts, faces, mats, grid, [
            (cx - half_w, y_mid + half_h),
            (cx + half_w, y_mid + half_h),
            (cx + half_w, y_mid - half_h),
            (cx - half_w, y_mid - half_h),
        ], MAT_EYE)


def _add_brows(verts, faces, mats, params, grid):
    """Dark brow bars on the shelf — the feature that most strongly says
    'face' at low resolution, and the one MGS2 portraits lean on hardest."""
    eye_x = _lerp(0.26, 0.42, params["eye_spacing"])
    half_w = _lerp(0.13, 0.17, params["eye_size"])
    half_h = 0.035
    y_brow = RING_PROFILE[RING_TAGS["brow"]][0] - 0.03

    for sign in (-1.0, 1.0):
        cx = sign * eye_x
        # Outer end sits slightly lower — a level bar reads as a robot.
        _surface_quad(verts, faces, mats, grid, [
            (cx - half_w, y_brow + half_h + sign * 0.012),
            (cx + half_w, y_brow + half_h - sign * 0.012),
            (cx + half_w, y_brow - half_h - sign * 0.012),
            (cx - half_w, y_brow - half_h + sign * 0.012),
        ], MAT_BROW)


def _add_mouth(verts, faces, mats, params, grid):
    """A dark horizontal slot at the mouth loop."""
    half_w = _lerp(0.13, 0.20, params["jaw_width"])
    half_h = 0.028
    y_mouth = RING_PROFILE[RING_TAGS["mouth"]][0] + 0.02
    _surface_quad(verts, faces, mats, grid, [
        (-half_w, y_mouth + half_h),
        (half_w, y_mouth + half_h),
        (half_w, y_mouth - half_h),
        (-half_w, y_mouth - half_h),
    ], MAT_MOUTH)


def _add_ears(verts, faces, mats, params, grid):
    """Flat angular ear plates. Cones read as horns; a plate reads as an
    ear and costs six triangles."""
    size = _lerp(0.07, 0.15, params["ear_size"])
    y = RING_PROFILE[RING_TAGS["eye_low"]][0]
    x_side = np.abs(grid[RING_TAGS["eye_low"], :, 0]).max()

    for sign in (-1.0, 1.0):
        cx = sign * x_side * 0.99
        corners = [
            (cx, y + size, 0.10),
            (cx + sign * size * 0.55, y + size * 0.35, 0.02),
            (cx + sign * size * 0.45, y - size * 0.75, -0.02),
            (cx, y - size * 0.95, 0.06),
        ]
        for tri in _quad(verts, corners):
            faces.append(tri)
            mats.append(MAT_SKIN)


def _orient_outward(verts, faces, center):
    """Flip any face whose normal points inward.

    The mesh is assembled from several independently-built pieces (rings,
    caps, feature quads) whose winding conventions are easy to get subtly
    wrong. Orienting against the head's own centre afterwards makes
    backface culling reliable without hand-auditing every generator.
    """
    verts = np.asarray(verts, dtype=np.float32)
    faces = np.asarray(faces, dtype=np.int32)
    tri = verts[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    outward = tri.mean(axis=1) - np.asarray(center, dtype=np.float32)
    flip = np.einsum("ij,ij->i", normals, outward) < 0
    faces[flip] = faces[flip][:, ::-1]
    return faces


def build_codec_head(params=None):
    """Build the head. Returns (verts (N,3), faces (M,3), materials (M,)).

    `materials` is what makes the face readable — see MAT_* above and
    pixel_raster.CODEC_PALETTE.
    """
    _trace("build_codec_head(params=%r)", params)
    params = resolve_params(params)

    grid = _sculpt(_flatten_face(_build_rings(params)), params)

    verts = [tuple(v) for v in grid.reshape(-1, 3)]
    faces = _grid_faces(NUM_RINGS, SEGMENTS)
    mats = [MAT_SKIN] * len(faces)

    # Cap the crown and the jaw base so the head is closed.
    crown_c = len(verts)
    verts.append((0.0, RING_PROFILE[0][0] + 0.05, 0.0))
    jaw_c = len(verts)
    verts.append((0.0, RING_PROFILE[-1][0] - 0.06, 0.0))
    for tri in _cap(verts, 0, SEGMENTS, 0, crown_c):
        faces.append(tri)
        mats.append(MAT_HAIR)
    for tri in _cap(verts, NUM_RINGS - 1, SEGMENTS, 0, jaw_c):
        faces.append(tri)
        mats.append(MAT_SKIN)

    # Hair/scalp: tint the upper cranium so the head isn't one uniform
    # mass — codec portraits always separate hair from face, and a bald
    # head reads as a mannequin.
    #
    # Assignment is purely TOPOLOGICAL (ring and segment indices), never
    # by a geometric z threshold: sliders move the surface, so a z-based
    # hairline reassigned 7-11 faces between parameter values and broke
    # the fixed-material invariant that morph targets depend on.
    hair_ring = RING_TAGS["forehead"]
    # Segments run from front-centre (0) outward; the front quarter on
    # each side is face, the rest is scalp.
    front_span = SEGMENTS // 4
    for fi, tri in enumerate(faces):
        if not all(t < NUM_RINGS * SEGMENTS for t in tri):
            continue
        rings_of = [t // SEGMENTS for t in tri]
        segs_of = [t % SEGMENTS for t in tri]
        if max(rings_of) > hair_ring:
            continue
        # Distance around the ring from the front-centre column.
        around = [min(s, SEGMENTS - s) for s in segs_of]
        if min(around) >= front_span:
            mats[fi] = MAT_HAIR

    _add_ears(verts, faces, mats, params, grid)
    _add_brows(verts, faces, mats, params, grid)
    _add_eyes(verts, faces, mats, params, grid)
    _add_mouth(verts, faces, mats, params, grid)

    verts_arr = np.asarray(verts, dtype=np.float32)
    faces_arr = _orient_outward(verts_arr, faces, center=(0.0, 0.0, -0.05))
    mats_arr = np.asarray(mats, dtype=np.int32)

    log.debug("codec head: %d verts, %d faces", len(verts_arr), len(faces_arr))
    return verts_arr, faces_arr, mats_arr


__all__ = ["build_codec_head", "MAT_SKIN", "MAT_EYE", "MAT_BROW",
           "MAT_MOUTH", "MAT_HAIR", "SEGMENTS", "RING_PROFILE"]
