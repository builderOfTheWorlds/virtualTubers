"""window_shape: silhouette mask + X bitmap packing for transparent heads."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from window_shape import ShapeMasker, pack_bitmap, silhouette_mask  # noqa: E402


def test_silhouette_mask_black_frame_is_empty():
    assert not silhouette_mask(np.zeros((4, 5, 3), dtype=np.float32)).any()


def test_silhouette_mask_fills_dark_interior_features():
    img = np.zeros((3, 7, 3), dtype=np.float32)
    img[1, 1:6] = 0.8
    img[1, 3] = 0.0  # a pupil: below threshold, but inside the head
    mask = silhouette_mask(img)
    assert mask[1].tolist() == [False, True, True, True, True, True, False]
    assert not mask[0].any() and not mask[2].any()


def test_silhouette_mask_ignores_grain_below_threshold():
    img = np.full((2, 2, 3), 0.015, dtype=np.float32)
    assert not silhouette_mask(img).any()


def test_pack_bitmap_is_lsb_first_and_row_padded():
    mask = np.zeros((2, 9), dtype=bool)
    mask[0, 0] = True
    mask[1, 8] = True
    assert pack_bitmap(mask) == bytes([0x01, 0x00, 0x00, 0x01])


@pytest.mark.parametrize("window_id", [None, 0])
def test_shape_masker_create_without_window_returns_none(window_id):
    assert ShapeMasker.create(window_id) is None
