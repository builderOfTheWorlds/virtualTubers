#!/usr/bin/env python3
"""
mesh3d.py — shared triangle-mesh builders for the termgl 3D pane prototypes
(radar/stats, knowledge graph, avatar). Pure numpy, no termgl import here so
it can be unit-tested without a real terminal / the C extension.

All builders return a numpy array of dtype `termgl.Trig3D`-compatible shape:
a structured array with fields `verts` (3,3 float32), `uv` (3,2 uint8),
`fill` (bool). Callers pass the array straight to `ctx.triangle_3d(...)`.

This is PROTOTYPE code (.claude/prompts/tuber_base_mockups/) — not yet
wired into app/radar_pane.py etc. See docs/panels.md once promoted.
"""
import numpy as np


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


# ── Radar: hexagonal "spike" mesh, one spike per metric ────────────────────
def build_radar_mesh(values, base_r=0.15, max_r=1.0, height=0.12):
    """values: list of 0..1 fractions (one per metric axis, N axes evenly
    spaced around a circle). Builds N triangular spikes radiating out from
    a flat hexagonal base — spike length encodes the metric value, so the
    silhouette reads as a classic radar/spider polygon when viewed from
    above, but renders with real depth/shading via termgl's lighting.
    """
    n = len(values)
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
def build_octahedron(center, r=0.06):
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


def build_node_positions(n, radius=0.8):
    """Evenly distribute n nodes on a sphere (Fibonacci sphere) so a small
    knowledge graph doesn't collapse into a flat circle when rotated —
    real depth separation is exactly the ASCII mockups' label-collision
    problem solved structurally instead of by layout tuning."""
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


def build_graph_mesh(node_positions, edges, node_r=0.05):
    """Returns (node_trigs, edge_segments). node_trigs is ready for
    triangle_3d(); edge_segments is a list of (p0, p1) float32 pairs for
    ctx.line() (termgl 2D line draws AFTER projecting points ourselves —
    see knowledge prototype for the projection step, since termgl's line()
    doesn't take 3D verts directly)."""
    trigs = []
    for pos in node_positions:
        trigs.extend(build_octahedron(pos, r=node_r))
    edge_segments = [(node_positions[a], node_positions[b]) for a, b in edges]
    return np.array(trigs, dtype=TRIG3D_DTYPE), edge_segments


# ── Avatar placeholder: icosahedron (stand-in for a real character mesh) ───
def build_icosahedron(radius=1.0):
    """Regular icosahedron — 20 faces, reads clearly as 'a 3D model' when
    shaded/rotated, and is the traditional placeholder base mesh before a
    real rigged character model is authored (see docs/panels.md's avatar
    section, future work)."""
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
