#!/usr/bin/env python3
"""Tests for the MGS2-style codec head (app/codec_head.py).

These encode the failures found while building it. Each assertion here
corresponds to something that visibly broke a render, so they are cheap
regression guards rather than coverage padding.
"""
import numpy as np
import pytest

from codec_head import (
    MAT_BROW,
    MAT_EYE,
    MAT_HAIR,
    MAT_MOUTH,
    MAT_SKIN,
    RING_PROFILE,
    RING_TAGS,
    SEGMENTS,
    build_codec_head,
)
from character_schema import PARAM_DEFAULTS


def _extent(verts):
    return verts.max(axis=0) - verts.min(axis=0)


def test_build_returns_consistent_arrays():
    verts, faces, mats = build_codec_head("chadwick")
    assert verts.ndim == 2 and verts.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 3
    assert len(mats) == len(faces)
    assert faces.max() < len(verts), "face index out of range"


def test_head_is_taller_than_wide():
    """The first version had H/W = 0.93 — a ball. No amount of feature
    detail rescues that, so the ratio is asserted directly."""
    width, height, _depth = _extent(build_codec_head("chadwick")[0])
    ratio = height / width
    assert 1.25 < ratio < 1.85, f"head H/W = {ratio:.2f}, not head-shaped"


def test_nose_breaks_the_profile_silhouette():
    """A nose that does not clear the brow in world Z leaves the profile
    flat — measured at 0.780 vs a 0.784 brow before this was fixed."""
    verts = build_codec_head("chadwick")[0]
    brow_y = RING_PROFILE[RING_TAGS["brow"]][0]
    nose_y = RING_PROFILE[RING_TAGS["nose_base"]][0]

    def front_z_near(y):
        band = verts[np.abs(verts[:, 1] - y) < 0.09]
        return band[:, 2].max()

    assert front_z_near(nose_y) > front_z_near(brow_y) + 0.04


def test_every_material_is_present():
    """Eyes/brows/mouth carry the face at low resolution. If a material
    vanishes the head silently degrades to a blank mask."""
    mats = build_codec_head("chadwick")[2]
    counts = np.bincount(mats, minlength=5)
    for mat, name in ((MAT_SKIN, "skin"), (MAT_EYE, "eye"),
                      (MAT_BROW, "brow"), (MAT_MOUTH, "mouth"),
                      (MAT_HAIR, "hair")):
        assert counts[mat] > 0, f"no {name} faces"


def test_features_sit_in_front_of_the_head_centre():
    """Feature quads are wrapped onto the surface; if that regresses they
    sink into the skull and disappear."""
    verts, faces, mats = build_codec_head("chadwick")
    for mat in (MAT_EYE, MAT_BROW, MAT_MOUTH):
        centroids = verts[faces[mats == mat]].mean(axis=1)
        assert (centroids[:, 2] > 0.2).all(), f"material {mat} not on the face"


def test_eyes_are_above_the_mouth_and_below_the_brow():
    verts, faces, mats = build_codec_head("chadwick")

    def mid_y(mat):
        return verts[faces[mats == mat]].mean(axis=1)[:, 1].mean()

    assert mid_y(MAT_BROW) > mid_y(MAT_EYE) > mid_y(MAT_MOUTH)


def test_eyes_are_symmetric_about_the_centre_line():
    verts, faces, mats = build_codec_head("chadwick")
    xs = verts[faces[mats == MAT_EYE]].mean(axis=1)[:, 0]
    assert abs(xs.mean()) < 0.02, "eyes are off-centre as a pair"
    assert (xs > 0).any() and (xs < 0).any(), "both eyes on one side"


#: Sliders that are expected to change the MESH. `accent_color` is a
#: rendering property and `build` drives body/shoulder mass rather than the
#: head, so neither belongs in the geometry assertions below.
GEOMETRIC_SLIDERS = sorted(
    set(PARAM_DEFAULTS) - {"accent_color", "build"}
)


@pytest.mark.parametrize("slider", sorted(PARAM_DEFAULTS))
def test_topology_is_stable_across_every_slider(slider):
    """Morph targets (avatar_3d_design.md §4) require a fixed vertex count,
    face table AND material table regardless of parameter values.

    The material table is the easy one to get wrong: assigning the hairline
    by a geometric z threshold reassigned 7-11 faces between slider values
    and silently broke morphability.
    """
    base_v, base_f, base_m = build_codec_head({slider: 0.0})
    high_v, high_f, high_m = build_codec_head({slider: 1.0})
    assert base_v.shape == high_v.shape
    assert np.array_equal(base_f, high_f)
    assert np.array_equal(base_m, high_m), "materials shifted with a slider"


@pytest.mark.parametrize("slider", GEOMETRIC_SLIDERS)
def test_sliders_actually_change_the_mesh(slider):
    """A slider wired to nothing is worse than no slider — it makes the
    agent iteration loop chase a parameter that cannot help."""
    low = build_codec_head({slider: 0.0})[0]
    high = build_codec_head({slider: 1.0})[0]
    assert not np.allclose(low, high), f"{slider} has no effect on the mesh"


def test_faces_are_wound_outward():
    """Backface culling drops inward-wound triangles; _orient_outward
    exists because the pieces are built by separate generators."""
    verts, faces, _mats = build_codec_head("chadwick")
    tri = verts[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    outward = tri.mean(axis=1) - np.array([0.0, 0.0, -0.05], dtype=np.float32)
    dots = np.einsum("ij,ij->i", normals, outward)
    fraction = (dots > 0).mean()
    assert fraction > 0.95, f"only {fraction:.0%} of faces point outward"


def test_mesh_stays_in_the_ps2_polygon_budget():
    """Codec portraits are low-poly by design, and the pane re-renders
    every tick — a blown budget shows up as a dropped frame rate."""
    faces = build_codec_head("chadwick")[1]
    assert len(faces) < 1200, f"{len(faces)} faces is past the codec budget"


def test_ring_grid_matches_the_declared_profile():
    verts = build_codec_head("chadwick")[0]
    ring_verts = len(RING_PROFILE) * SEGMENTS
    assert len(verts) > ring_verts, "feature geometry missing"
