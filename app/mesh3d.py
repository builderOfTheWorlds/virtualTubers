#!/usr/bin/env python3
"""
mesh3d.py
Pure-numpy triangle-mesh builders shared by the termgl-based 3D panes
(radar_pane.py, knowledge_graph_pane.py, avatar_providers/termgl_avatar.py).
No termgl import here (intentional) so mesh construction is unit-testable
without the C extension / a real terminal.

Originated as a prototype in .claude/prompts/tuber_base_mockups/mesh3d.py —
promoted here unchanged except for this docstring. See docs/panels.md's
"3D rendering (termgl)" section for the overall design.

Every builder returns (or contributes to) a numpy structured array matching
termgl.Trig3D's layout: fields `verts` (3,3 float32), `uv` (3,2 uint8),
`fill` (bool). Callers pass the array straight to `ctx.triangle_3d(...)`.
"""
import logging

import numpy as np

log = logging.getLogger(__name__)
TRACE = 5


def _trace(msg, *args):
    """TRACE-level logging (CLAUDE.md's log strategy). Guarded so the
    per-frame mesh calls pay no formatting cost when TRACE is off."""
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)



def make_trig3d_dtype():
    """Matches termgl.Trig3D's structured dtype (verts/uv/fill) without
    importing termgl, so mesh-building is testable in plain numpy."""
    return np.dtype([
        ("verts", np.float32, (3, 3)),
        ("uv", np.uint8, (3, 2)),
        ("fill", np.bool_),
    ])


TRIG3D_DTYPE = make_trig3d_dtype()


def _trig(v0, v1, v2, uv=((0, 0), (255, 0), (0, 255))):
    t = np.zeros((), dtype=TRIG3D_DTYPE)
    t["verts"] = np.array([v0, v1, v2], dtype=np.float32)
    t["uv"] = np.array(uv, dtype=np.uint8)
    t["fill"] = True
    return t


# ── Indexed geometry: (verts, faces) -> Trig3D array ──────────────────────
# The builders above each emit independent triangles, which is fine for
# static meshes. A MORPHABLE mesh (docs/avatar_3d_design.md §4) cannot work
# that way: lerping between two expression poses requires both poses to
# share one vertex array and one face table, so vertex i means the same
# anatomical point in every pose. build_head_mesh therefore works in
# indexed (verts, faces) form and converts once at the end via trigs_from_indexed.


def trigs_from_indexed(verts, faces):
    """Convert an indexed mesh to the flat Trig3D array termgl consumes.

    verts: (N,3) float array. faces: (M,3) int array/sequence of indices.
    Vectorized rather than looped — a head is ~600 triangles and this runs
    per-frame when morph targets are lerped, so the per-triangle Python
    loop the other builders use would be the wrong shape here.

    Raises IndexError on an out-of-range face index rather than silently
    skipping it (unlike build_graph_mesh's defensive edge handling): a bad
    index here is a generator bug, not malformed external data, and
    silently dropping faces would produce a subtly holed head that's far
    harder to diagnose than a stack trace.
    """
    _trace("trigs_from_indexed(verts=%s, faces=%s)",
           np.shape(verts), np.shape(faces))
    verts = np.asarray(verts, dtype=np.float32)
    faces = np.asarray(faces, dtype=np.int32)

    if faces.size == 0:
        return np.zeros((0,), dtype=TRIG3D_DTYPE)
    if verts.ndim != 2 or verts.shape[1] != 3:
        raise ValueError(f"verts must be (N,3), got {verts.shape}")
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError(f"faces must be (M,3), got {faces.shape}")
    if faces.max() >= len(verts) or faces.min() < 0:
        raise IndexError(
            f"face index out of range: faces span [{faces.min()}, {faces.max()}] "
            f"but there are only {len(verts)} verts"
        )

    trigs = np.zeros((len(faces),), dtype=TRIG3D_DTYPE)
    trigs["verts"] = verts[faces]          # (M,3,3) gather — one indexing op
    trigs["uv"] = np.array([(0, 0), (255, 0), (0, 255)], dtype=np.uint8)
    trigs["fill"] = True
    return trigs



# ── Radar: hexagonal "spike" mesh, one spike per metric ────────────────────
def build_radar_mesh(values, base_r=0.2, max_r=1.3, height=0.5):
    """values: list of 0..1 fractions (one per metric axis, N axes evenly
    spaced around a circle). Builds N triangular spikes radiating out from
    a flat hexagonal base — spike length encodes the metric value, so the
    silhouette reads as a classic radar/spider polygon when viewed from
    above, but renders with real depth/shading via termgl's lighting.

    Empty `values` returns an empty (0,)-shaped array rather than raising —
    callers (radar_pane.py) must tolerate a metrics file with zero/partial
    fields per its existing defensive-loading contract.
    """
    n = len(values)
    if n == 0:
        return np.zeros((0,), dtype=TRIG3D_DTYPE)
    trigs = []
    center = np.array([0, 0, 0], dtype=np.float32)
    for i, frac in enumerate(values):
        frac = max(0.02, min(1.0, frac))
        a0 = 2 * np.pi * i / n
        a1 = 2 * np.pi * (i + 1) / n
        tip_r = base_r + (max_r - base_r) * frac
        base0 = np.array([base_r * np.cos(a0), base_r * np.sin(a0), 0], dtype=np.float32)
        base1 = np.array([base_r * np.cos(a1), base_r * np.sin(a1), 0], dtype=np.float32)
        mid_a = (a0 + a1) / 2
        tip = np.array([tip_r * np.cos(mid_a), tip_r * np.sin(mid_a), height * frac], dtype=np.float32)
        # top faces of the spike (base0->base1->tip, and base1/base0 slants)
        trigs.append(_trig(base0, base1, tip))
        trigs.append(_trig(center, base0, base1))
        # side walls for a slight 3D "raised" look
        tip_low = tip.copy()
        tip_low[2] = 0
        trigs.append(_trig(base0, tip_low, tip))
        trigs.append(_trig(tip_low, base1, tip))
    return np.array(trigs, dtype=TRIG3D_DTYPE)


# ── Knowledge graph: small octahedra at node positions + line edges ────────
def build_octahedron(center, r=0.07):
    """6-vertex octahedron (looks like a small diamond/gem) — used as the
    'node' marker in the 3D knowledge graph. Cheap (8 triangles) so N nodes
    stays fast."""
    cx, cy, cz = center
    pts = {
        "+x": (cx + r, cy, cz), "-x": (cx - r, cy, cz),
        "+y": (cx, cy + r, cz), "-y": (cx, cy - r, cz),
        "+z": (cx, cy, cz + r), "-z": (cx, cy, cz - r),
    }
    faces = [
        ("+x", "+y", "+z"), ("+y", "-x", "+z"), ("-x", "-y", "+z"), ("-y", "+x", "+z"),
        ("+y", "+x", "-z"), ("-x", "+y", "-z"), ("-y", "-x", "-z"), ("+x", "-y", "-z"),
    ]
    return [_trig(pts[a], pts[b], pts[c]) for a, b, c in faces]


def build_node_positions(n, radius=0.9):
    """Evenly distribute n nodes on a sphere (Fibonacci sphere) so a small
    knowledge graph doesn't collapse into a flat circle when rotated — real
    depth separation via termgl's Z-buffer avoids node/label collisions
    that a flat 2D layout hits once more than a handful of nodes are shown.
    n<=0 returns an empty list (caller renders an empty-graph placeholder).
    """
    if n <= 0:
        return []
    positions = []
    golden = np.pi * (3 - np.sqrt(5))
    for i in range(n):
        y = 1 - (i / max(1, n - 1)) * 2
        r_at_y = np.sqrt(max(0.0, 1 - y * y))
        theta = golden * i
        x = np.cos(theta) * r_at_y
        z = np.sin(theta) * r_at_y
        positions.append((x * radius, y * radius, z * radius))
    return positions


def build_graph_mesh(node_positions, edges, node_r=0.07):
    """Returns (node_trigs, edge_segments). node_trigs is ready for
    triangle_3d(); edge_segments is a list of (p0, p1) float32-tuple pairs
    for the caller to project + draw as 2D lines (termgl's line() takes
    screen-space verts, not 3D world verts — see render3d_common.project_point).
    `edges` entries are (i, j) index pairs into node_positions; any edge
    referencing an out-of-range index is silently skipped (defensive —
    matches the project's house style of never crashing a pane process on
    malformed/placeholder data).
    """
    trigs = []
    for pos in node_positions:
        trigs.extend(build_octahedron(pos, r=node_r))
    n = len(node_positions)
    edge_segments = [
        (node_positions[a], node_positions[b])
        for a, b in edges
        if 0 <= a < n and 0 <= b < n
    ]
    if not trigs:
        return np.zeros((0,), dtype=TRIG3D_DTYPE), edge_segments
    return np.array(trigs, dtype=TRIG3D_DTYPE), edge_segments


# ── Avatar placeholder: icosahedron (stand-in for a real character mesh) ───
def build_icosahedron(radius=1.0):
    """Regular icosahedron — 20 faces, reads clearly as 'a 3D model' when
    shaded/rotated. Placeholder base mesh until a real rigged character
    model is authored (future work — see docs/panels.md's avatar section)."""
    phi = (1 + np.sqrt(5)) / 2
    raw_verts = [
        (-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
        (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
        (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1),
    ]
    norm = radius / np.sqrt(1 + phi * phi)
    verts = [tuple(c * norm for c in v) for v in raw_verts]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    return np.array([_trig(verts[a], verts[b], verts[c]) for a, b, c in faces],
                     dtype=TRIG3D_DTYPE)
