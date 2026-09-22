#!/usr/bin/env python3
"""
tests/test_ascii_raster.py
Covers the pure-numpy preview rasterizer (app/ascii_raster.py).

STATUS: ascii_raster.py is retained as a standalone module (still fully
correct and tested here) but is no longer used by character_preview.py,
which switched to the codec pixel pipeline (codec_head.py + gl_raster.py /
pixel_raster.py, see docs/character_generator.md and
tests/test_character_preview.py). These tests exercise ascii_raster.py
directly rather than through the CLI.

The rasterizer's contract is deliberately modest — it previews SILHOUETTE
AND SHADING, not a pixel-exact termgl emulator — so these tests assert
structural properties (frame shape, something got drawn, culling works,
sliders visibly change the output) rather than exact character art, which
would be brittle against any harmless tuning of the ramp or camera.
"""
import numpy as np
import pytest

from ascii_raster import (
    GRADIENT_MIN,
    make_camera_matrix,
    make_view_matrix,
    render_mesh,
    render_turntable,
)
from head_mesh import build_head_params_mesh

VERTS, FACES = build_head_params_mesh("chadwick")


def _ink(rows):
    """Count non-blank characters — 'how much got drawn'."""
    return sum(len(row.strip()) for row in rows)


# ── Matrices ──────────────────────────────────────────────────────────────
def test_camera_matrix_is_a_perspective_projection():
    m = make_camera_matrix(55, 24)
    assert m.shape == (4, 4)
    # The perspective divide hinges on this: w' = -z.
    assert m[3, 2] == -1.0
    assert m[3, 3] == 0.0


def test_view_matrix_translates_along_z():
    m = make_view_matrix(dist=3.0)
    assert m.shape == (4, 4)
    assert m[2, 3] == pytest.approx(-3.0)


def test_view_matrix_rotation_is_orthonormal():
    rot = make_view_matrix(rot_x=0.4, rot_y=1.1, rot_z=0.2)[:3, :3]
    np.testing.assert_allclose(rot @ rot.T, np.eye(3), atol=1e-5)


# ── Frame shape ───────────────────────────────────────────────────────────
def test_render_mesh_returns_correct_row_count():
    assert len(render_mesh(VERTS, FACES, width=55, height=24)) == 24


def test_double_chars_doubles_row_width():
    doubled = render_mesh(VERTS, FACES, width=55, height=24, double_chars=True)
    plain = render_mesh(VERTS, FACES, width=55, height=24, double_chars=False)
    assert all(len(r) == 110 for r in doubled)
    assert all(len(r) == 55 for r in plain)


@pytest.mark.parametrize("width,height", [(55, 24), (40, 20), (110, 48)])
def test_render_mesh_honors_arbitrary_dimensions(width, height):
    rows = render_mesh(VERTS, FACES, width=width, height=height, double_chars=False)
    assert len(rows) == height
    assert all(len(r) == width for r in rows)


# ── Something actually gets drawn ─────────────────────────────────────────
def test_render_mesh_draws_the_head():
    assert _ink(render_mesh(VERTS, FACES)) > 100


def test_render_uses_only_gradient_characters():
    rows = render_mesh(VERTS, FACES)
    assert set("".join(rows)) <= set(GRADIENT_MIN)


def test_render_produces_shading_variation():
    """More than one gradient level must appear, or the lighting is broken
    and the head reads as a flat blob."""
    chars = set("".join(render_mesh(VERTS, FACES)).replace(" ", ""))
    assert len(chars) >= 3, f"expected shading variation, got {chars}"


def test_empty_mesh_renders_a_blank_frame():
    rows = render_mesh(np.zeros((0, 3), dtype=np.float32),
                       np.zeros((0, 3), dtype=np.int32))
    assert _ink(rows) == 0


def test_mesh_behind_the_camera_renders_blank():
    """Geometry behind the near plane is dropped, not garbage-projected."""
    verts = VERTS + np.array([0, 0, 50], dtype=np.float32)
    assert _ink(render_mesh(verts, FACES, dist=3.0)) == 0


def test_backfaces_are_culled():
    """A single triangle must draw from one side and vanish from the other —
    proof that culling is active and matches termgl's Face.BACK/Winding.CCW
    setup. Tested on a lone triangle rather than the head: the head is a
    CLOSED surface, so reversing its winding just renders the inside of the
    skull, which has the same silhouette and would hide a culling bug.
    """
    tri = np.array([[-0.5, -0.5, 0.0], [0.5, -0.5, 0.0], [0.0, 0.5, 0.0]],
                   dtype=np.float32)
    front = np.array([[0, 1, 2]], dtype=np.int32)
    back = np.array([[0, 2, 1]], dtype=np.int32)

    front_ink = _ink(render_mesh(tri, front, dist=2.0))
    back_ink = _ink(render_mesh(tri, back, dist=2.0))

    assert front_ink > 0, "front-facing triangle was not drawn at all"
    assert back_ink == 0, "back-facing triangle should have been culled"


def test_head_winding_faces_outward():
    """The head's own winding must be the side that survives culling — a
    winding regression in head_mesh would otherwise render the skull's
    interior, which looks plausible in a still but shades inside-out."""
    flipped = FACES[:, ::-1].copy()
    outside = render_mesh(VERTS, FACES)
    inside = render_mesh(VERTS, flipped)
    assert outside != inside


def test_closer_camera_draws_a_bigger_head():
    near = _ink(render_mesh(VERTS, FACES, dist=2.0))
    far = _ink(render_mesh(VERTS, FACES, dist=5.0))
    assert near > far


def test_rotation_changes_the_frame():
    front = render_mesh(VERTS, FACES, rot_y=0.0)
    side = render_mesh(VERTS, FACES, rot_y=1.5708)
    assert front != side


def test_render_is_deterministic():
    assert render_mesh(VERTS, FACES, rot_y=0.7) == render_mesh(VERTS, FACES, rot_y=0.7)


def test_different_sliders_render_differently():
    """The end-to-end promise of the iteration loop: change a slider, see a
    different picture."""
    a, fa = build_head_params_mesh({"head_width": 0.0, "ear_size": 0.0})
    b, fb = build_head_params_mesh({"head_width": 1.0, "ear_size": 1.0})
    assert render_mesh(a, fa) != render_mesh(b, fb)


def test_render_turntable_returns_requested_frame_count():
    frames = render_turntable(VERTS, FACES, frames=4, width=40, height=18)
    assert len(frames) == 4
    assert all(len(f) == 18 for f in frames)


def test_turntable_frames_differ_from_each_other():
    frames = render_turntable(VERTS, FACES, frames=4, width=40, height=18)
    assert len({tuple(f) for f in frames}) > 1
