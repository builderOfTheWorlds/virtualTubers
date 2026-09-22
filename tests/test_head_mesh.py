#!/usr/bin/env python3
"""
tests/test_head_mesh.py
Covers Backend A of the character generator (app/head_mesh.py) plus the
indexed->Trig3D converter it depends on (mesh3d.trigs_from_indexed).

The centerpiece is TOPOLOGY STABILITY: docs/avatar_3d_design.md §4 makes
expression morph targets conditional on vertex ordering being identical
across every slider setting, and §4 explicitly calls for stress-testing it
early because a builder that branches geometry on a parameter value breaks
morphing in a way that is very hard to debug later. Those tests exist to
fail loudly the moment someone adds a conditional vertex.
"""
import itertools

import numpy as np
import pytest

from character_schema import PARAM_DEFAULTS, SLIDER_DEFAULTS
from head_mesh import (
    SKULL_RINGS,
    SKULL_SEGMENTS,
    build_head_mesh,
    build_head_params_mesh,
    lerp_verts,
)
from mesh3d import TRIG3D_DTYPE, trigs_from_indexed

#: The skull is emitted first, as a full (rings+1) x (segments+1) grid —
#: see head_mesh.build_head_params_mesh's part ordering.
SKULL_VERT_COUNT = (SKULL_RINGS + 1) * (SKULL_SEGMENTS + 1)


# ── The parameter space we assert topology over ───────────────────────────
#: Each slider at its extremes and midpoint — the settings most likely to
#: trip a conditional-geometry bug.
EXTREMES = (0.0, 0.5, 1.0)


def _one_slider_variations():
    """Yield params dicts with a single slider pushed to each extreme."""
    for key in SLIDER_DEFAULTS:
        for value in EXTREMES:
            yield {key: value}


def _all_sliders_variations():
    """All sliders simultaneously at min, mid, and max."""
    for value in EXTREMES:
        yield {key: value for key in SLIDER_DEFAULTS}


# ── trigs_from_indexed ────────────────────────────────────────────────────
def test_trigs_from_indexed_returns_trig3d_dtype():
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    faces = np.array([[0, 1, 2]], dtype=np.int32)
    trigs = trigs_from_indexed(verts, faces)

    assert trigs.dtype == TRIG3D_DTYPE
    assert len(trigs) == 1
    assert trigs["fill"].all()
    np.testing.assert_allclose(trigs[0]["verts"], verts)


def test_trigs_from_indexed_gathers_shared_verts():
    """Two faces sharing vertices must both resolve to real coordinates —
    this is the whole point of the indexed form."""
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=np.float32)
    faces = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int32)
    trigs = trigs_from_indexed(verts, faces)

    assert len(trigs) == 2
    np.testing.assert_allclose(trigs[1]["verts"][0], verts[1])
    np.testing.assert_allclose(trigs[1]["verts"][2], verts[2])


def test_trigs_from_indexed_empty_faces_returns_empty_array():
    verts = np.zeros((3, 3), dtype=np.float32)
    trigs = trigs_from_indexed(verts, np.zeros((0, 3), dtype=np.int32))
    assert len(trigs) == 0
    assert trigs.dtype == TRIG3D_DTYPE


def test_trigs_from_indexed_raises_on_out_of_range_index():
    # Loud failure by design: silently dropping the face would produce a
    # subtly holed mesh that's far harder to trace back to its cause.
    verts = np.zeros((3, 3), dtype=np.float32)
    faces = np.array([[0, 1, 7]], dtype=np.int32)
    with pytest.raises(IndexError, match="out of range"):
        trigs_from_indexed(verts, faces)


@pytest.mark.parametrize("bad_verts", [
    np.zeros((3, 2), dtype=np.float32),
    np.zeros((3,), dtype=np.float32),
])
def test_trigs_from_indexed_rejects_malformed_verts(bad_verts):
    with pytest.raises(ValueError, match=r"verts must be \(N,3\)"):
        trigs_from_indexed(bad_verts, np.array([[0, 1, 2]], dtype=np.int32))


def test_trigs_from_indexed_rejects_non_triangle_faces():
    verts = np.zeros((4, 3), dtype=np.float32)
    with pytest.raises(ValueError, match=r"faces must be \(M,3\)"):
        trigs_from_indexed(verts, np.array([[0, 1, 2, 3]], dtype=np.int32))


# ── Basic mesh construction ───────────────────────────────────────────────
def test_build_head_mesh_returns_trig3d_array():
    mesh = build_head_mesh()
    assert mesh.dtype == TRIG3D_DTYPE
    assert len(mesh) > 0
    assert mesh["fill"].all()


def test_build_head_mesh_accepts_preset_name():
    assert len(build_head_mesh("chadwick")) == len(build_head_mesh())


def test_build_head_mesh_accepts_partial_params():
    """A partial dict is the normal agent call shape — missing sliders take
    their defaults rather than raising."""
    assert len(build_head_mesh({"eye_size": 0.9})) > 0


def test_build_head_mesh_produces_finite_coordinates():
    verts = build_head_mesh("chadwick")["verts"]
    assert np.isfinite(verts).all()


def test_build_head_mesh_scale_scales_coordinates():
    small = build_head_params_mesh(None)[0]
    scaled = build_head_mesh(None, scale=2.0)["verts"]
    assert scaled.max() == pytest.approx(small.max() * 2.0, rel=1e-5)


def test_build_head_is_roughly_centered_and_head_sized():
    """Guards the camera framing: termgl_avatar renders at a fixed distance,
    so a head that drifts off-origin or balloons in scale would silently
    render half out of frame."""
    verts, _ = build_head_params_mesh("chadwick")
    center = (verts.max(axis=0) + verts.min(axis=0)) / 2.0
    extent = verts.max(axis=0) - verts.min(axis=0)

    assert np.abs(center).max() < 0.35, f"head off-center: {center}"
    assert (extent < 4.0).all(), f"head too large for the camera: {extent}"
    assert (extent > 0.5).all(), f"head suspiciously flat: {extent}"


def test_skull_is_taller_than_wide():
    """Silhouette sanity check on the SKULL specifically — the ears
    deliberately protrude far enough that the full mesh is about as wide as
    it is tall (that flare is what makes a character distinguishable at
    55x24), so the proportion claim only holds for the cranium itself."""
    verts, _ = build_head_params_mesh(None)
    skull = verts[:SKULL_VERT_COUNT]
    extent = skull.max(axis=0) - skull.min(axis=0)
    assert extent[1] > extent[0], f"skull is not taller than wide: {extent}"


# ── TOPOLOGY STABILITY (docs/avatar_3d_design.md §4) ──────────────────────
BASE_VERTS, BASE_FACES = build_head_params_mesh(None)


@pytest.mark.parametrize("params", list(_one_slider_variations()))
def test_topology_is_stable_for_single_slider_extremes(params):
    """Vertex COUNT and face TABLE must not depend on any slider value."""
    verts, faces = build_head_params_mesh(params)
    assert verts.shape == BASE_VERTS.shape, f"vertex count changed for {params}"
    np.testing.assert_array_equal(faces, BASE_FACES,
                                  err_msg=f"face table changed for {params}")


@pytest.mark.parametrize("params", list(_all_sliders_variations()))
def test_topology_is_stable_with_all_sliders_at_extremes(params):
    verts, faces = build_head_params_mesh(params)
    assert verts.shape == BASE_VERTS.shape
    np.testing.assert_array_equal(faces, BASE_FACES)


@pytest.mark.parametrize("combo", list(itertools.product(EXTREMES, repeat=3)))
def test_topology_is_stable_across_slider_combinations(combo):
    """Pairwise/triple interactions between the sliders most likely to
    interact geometrically (skull shaping and the features attached to it)."""
    head_width, jaw_width, ear_size = combo
    verts, faces = build_head_params_mesh({
        "head_width": head_width,
        "jaw_width": jaw_width,
        "ear_size": ear_size,
    })
    assert verts.shape == BASE_VERTS.shape
    np.testing.assert_array_equal(faces, BASE_FACES)


def test_accent_color_does_not_affect_geometry():
    """accent_color is a shading parameter; it must never move a vertex."""
    a, _ = build_head_params_mesh({"accent_color": "RED"})
    b, _ = build_head_params_mesh({"accent_color": "CYAN"})
    np.testing.assert_array_equal(a, b)


def test_sliders_actually_change_geometry():
    """The flip side of stability: identical topology must NOT mean an
    identical mesh, or the sliders would be decorative."""
    narrow, _ = build_head_params_mesh({"head_width": 0.0})
    wide, _ = build_head_params_mesh({"head_width": 1.0})
    assert not np.allclose(narrow, wide)
    assert wide[:, 0].max() > narrow[:, 0].max()


@pytest.mark.parametrize("slider", sorted(SLIDER_DEFAULTS))
def test_every_slider_has_a_visible_effect(slider):
    """Each slider must move geometry between its extremes — catches a
    slider that was added to the schema but never wired into the builder."""
    low, _ = build_head_params_mesh({slider: 0.0})
    high, _ = build_head_params_mesh({slider: 1.0})
    assert not np.allclose(low, high), f"slider {slider!r} has no geometric effect"


def test_build_is_deterministic():
    """Same params in, byte-identical mesh out — an agent comparing two
    iterations must be seeing its own change, not generator noise."""
    a, fa = build_head_params_mesh("chadwick")
    b, fb = build_head_params_mesh("chadwick")
    np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(fa, fb)


# ── Morph-target blending ─────────────────────────────────────────────────
def test_lerp_verts_endpoints_and_midpoint():
    a, _ = build_head_params_mesh({"jaw_width": 0.0})
    b, _ = build_head_params_mesh({"jaw_width": 1.0})

    np.testing.assert_allclose(lerp_verts(a, b, 0.0), a, atol=1e-6)
    np.testing.assert_allclose(lerp_verts(a, b, 1.0), b, atol=1e-6)
    np.testing.assert_allclose(lerp_verts(a, b, 0.5), (a + b) / 2.0, atol=1e-6)


def test_lerp_verts_clamps_t():
    a, _ = build_head_params_mesh({"jaw_width": 0.0})
    b, _ = build_head_params_mesh({"jaw_width": 1.0})
    np.testing.assert_allclose(lerp_verts(a, b, 5.0), b, atol=1e-6)
    np.testing.assert_allclose(lerp_verts(a, b, -5.0), a, atol=1e-6)


def test_lerp_verts_rejects_mismatched_topology():
    a, _ = build_head_params_mesh(None)
    with pytest.raises(ValueError, match="share topology"):
        lerp_verts(a, a[:-1], 0.5)


def test_lerped_pose_still_matches_the_shared_face_table():
    """The end-to-end morph-target contract: blend two poses, feed the
    result through the SHARED face table, get a valid mesh."""
    a, faces = build_head_params_mesh({"jaw_width": 0.0})
    b, _ = build_head_params_mesh({"jaw_width": 1.0})
    trigs = trigs_from_indexed(lerp_verts(a, b, 0.5), faces)

    assert len(trigs) == len(faces)
    assert np.isfinite(trigs["verts"]).all()


# ── Schema/builder agreement ──────────────────────────────────────────────
def test_builder_consumes_exactly_the_documented_schema():
    """Every documented parameter is accepted by the builder — catches the
    schema and Backend A drifting apart."""
    assert len(build_head_mesh(dict(PARAM_DEFAULTS))) > 0
