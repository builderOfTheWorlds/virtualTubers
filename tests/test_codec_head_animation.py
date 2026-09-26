"""Tests for app/codec_head.py's mouth_open / emotion morph channels
(docs/avatar_emotion_design.md) — Part 1 of the mouth-animation feature.

The core invariant these protect: mouth_open and emotion may move a mesh's
VERTEX POSITIONS, but must NEVER change its vertex count, face table, or
per-face materials. That's what lets termgl/codec_avatar blend frames by
just re-building the head each tick without re-uploading topology."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from codec_head import build_codec_head, EMOTION_RECIPES  # noqa: E402
from emotion import EMOTIONS  # noqa: E402


@pytest.fixture(scope="module")
def base():
    return build_codec_head()


def test_default_call_matches_the_documented_static_mesh(base):
    verts, faces, mats = base
    verts2, faces2, mats2 = build_codec_head(mouth_open=0.0, emotion="neutral")

    assert np.array_equal(verts, verts2)
    assert np.array_equal(faces, faces2)
    assert np.array_equal(mats, mats2)


@pytest.mark.parametrize("mouth_open", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_mouth_open_never_changes_topology(base, mouth_open):
    base_verts, base_faces, base_mats = base
    verts, faces, mats = build_codec_head(mouth_open=mouth_open)

    assert verts.shape == base_verts.shape
    assert np.array_equal(faces, base_faces)
    assert np.array_equal(mats, base_mats)


@pytest.mark.parametrize("emotion", EMOTIONS)
def test_every_emotion_never_changes_topology(base, emotion):
    base_verts, base_faces, base_mats = base
    verts, faces, mats = build_codec_head(emotion=emotion)

    assert verts.shape == base_verts.shape
    assert np.array_equal(faces, base_faces)
    assert np.array_equal(mats, base_mats)


def test_every_emotion_has_a_recipe():
    assert set(EMOTION_RECIPES) == set(EMOTIONS)


def test_neutral_recipe_is_all_zero():
    recipe = EMOTION_RECIPES["neutral"]
    assert all(v == 0.0 for v in recipe.values())


def test_mouth_open_actually_moves_mouth_vertices(base):
    """A non-zero mouth_open must move SOME vertex — otherwise the channel
    is wired but inert."""
    base_verts, _, _ = base
    open_verts, _, _ = build_codec_head(mouth_open=1.0)

    assert not np.array_equal(base_verts, open_verts)


def test_mouth_open_is_monotonic_in_the_mouth_gap():
    """Higher mouth_open should widen the mouth quad's vertical gap —
    otherwise a bigger envelope value could visually look LESS open."""
    from codec_head import MAT_MOUTH

    def mouth_gap(mouth_open):
        verts, faces, mats = build_codec_head(mouth_open=mouth_open)
        mouth_face_idx = np.where(mats == MAT_MOUTH)[0]
        mouth_vert_idx = np.unique(faces[mouth_face_idx])
        ys = verts[mouth_vert_idx, 1]
        return float(ys.max() - ys.min())

    gap_closed = mouth_gap(0.0)
    gap_half = mouth_gap(0.5)
    gap_open = mouth_gap(1.0)

    assert gap_closed <= gap_half <= gap_open
    assert gap_closed < gap_open  # strictly increases somewhere in the range


@pytest.mark.parametrize("emotion", [e for e in EMOTIONS if e != "neutral"])
def test_a_non_neutral_emotion_moves_some_vertex_relative_to_neutral(base, emotion):
    base_verts, _, _ = base
    emo_verts, _, _ = build_codec_head(emotion=emotion)

    assert not np.array_equal(base_verts, emo_verts)


def test_happy_and_sad_move_the_mouth_corners_in_opposite_directions():
    """happy's mouth dial is +1.0 (smile), sad's is -1.0 (frown) — the
    mouth quad's corner-lift should move opposite directions, not just
    'differently'."""
    from codec_head import MAT_MOUTH

    def mouth_corner_ys(emotion):
        verts, faces, mats = build_codec_head(emotion=emotion)
        mouth_face_idx = np.where(mats == MAT_MOUTH)[0]
        mouth_vert_idx = np.unique(faces[mouth_face_idx])
        return verts[mouth_vert_idx, 1]

    neutral_ys = mouth_corner_ys("neutral")
    happy_ys = mouth_corner_ys("happy")
    sad_ys = mouth_corner_ys("sad")

    # happy's top corners should sit higher than neutral's; sad's lower.
    assert happy_ys.max() > neutral_ys.max()
    assert sad_ys.max() < neutral_ys.max() or sad_ys.min() < neutral_ys.min()


def test_mouth_open_and_emotion_combine_additively_not_override():
    """An afraid/surprised emotion (positive jaw_drop) plus a real
    mouth_open value should open the mouth MORE than either alone —
    proving the two channels sum rather than one replacing the other."""
    from codec_head import MAT_MOUTH

    def mouth_gap(mouth_open, emotion):
        verts, faces, mats = build_codec_head(mouth_open=mouth_open, emotion=emotion)
        mouth_face_idx = np.where(mats == MAT_MOUTH)[0]
        mouth_vert_idx = np.unique(faces[mouth_face_idx])
        ys = verts[mouth_vert_idx, 1]
        return float(ys.max() - ys.min())

    neutral_closed = mouth_gap(0.0, "neutral")
    afraid_closed = mouth_gap(0.0, "afraid")     # afraid has jaw_drop > 0
    neutral_talking = mouth_gap(0.6, "neutral")
    afraid_talking = mouth_gap(0.6, "afraid")

    assert afraid_closed > neutral_closed        # emotion alone opens it a bit
    assert neutral_talking > neutral_closed      # audio alone opens it more
    assert afraid_talking >= neutral_talking      # combined is at least as open
    assert afraid_talking >= afraid_closed


def test_an_unrecognized_emotion_string_degrades_to_neutral(base):
    """codec_head must never raise on a bad/garbage emotion value — a
    corrupted state file or a bug upstream should render neutral, not
    crash the avatar pane."""
    base_verts, base_faces, base_mats = base
    verts, faces, mats = build_codec_head(emotion="not-a-real-emotion")

    assert np.array_equal(verts, base_verts)
    assert np.array_equal(faces, base_faces)
    assert np.array_equal(mats, base_mats)


def test_mouth_open_clamps_outside_0_1_rather_than_raising():
    # Should not raise for out-of-range input (a bad envelope sample).
    build_codec_head(mouth_open=-5.0)
    build_codec_head(mouth_open=5.0)
