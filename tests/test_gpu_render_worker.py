#!/usr/bin/env python3
"""
tests/test_gpu_render_worker.py
Covers app/gpu_render_worker.py — GPU rendering in a subprocess, separate
from whatever process owns the avatar's pygame window (see that module's
docstring for the gx10 bug this exists to fix: an in-process GL context
and an SDL/pygame X11 window cannot safely coexist on some Xvfb/driver
combinations).

These tests spawn a REAL child process each time (no mocking multiprocessing
itself — that would defeat the point, since the whole bug class this module
exists to avoid only shows up with a real process boundary). They're slower
than a typical unit test (process startup + GL context creation, ~0.1-2s
each depending on host) but still fast enough to run in the normal suite.
"""
import numpy as np
import pytest

import gpu_render_worker
from gpu_render_worker import GPURenderWorker


@pytest.fixture
def worker():
    w = GPURenderWorker(
        "chadwick", width=64, height=64, view_dist=3.25, angle_speed=0.03,
        timeout_s=15.0)  # generous — CI/shared hosts can be slow to spawn
    yield w
    w.close()


def test_render_frame_returns_float_0_to_1_same_contract_as_frame_source(worker):
    img, backend = worker.render_frame("idle")
    assert img.shape == (64, 64, 3)
    assert img.dtype == np.float32
    assert img.min() >= 0.0 and img.max() <= 1.0
    assert backend in ("gpu", "cpu")  # whatever this host's gl_raster picks


def test_render_frame_produces_a_real_non_black_frame(worker):
    """Regression guard for the exact bug this module exists to dodge:
    a worker that's silently returning all-zero/garbage frames (a stale
    shared-memory buffer, a worker that crashed after startup but before
    the first real render) must not look like a successful call."""
    img, _backend = worker.render_frame("idle")
    assert img.mean() > 0.01


def test_render_frame_advances_rotation_between_calls(worker):
    """Two consecutive calls must not be pixel-identical — the worker
    keeps its own FrameSource state (including rotation angle) across
    calls, the same as an in-process FrameSource would."""
    img1, _ = worker.render_frame("idle")
    img2, _ = worker.render_frame("idle")
    assert not np.array_equal(img1, img2)


def test_render_frame_after_close_raises():
    w = GPURenderWorker("chadwick", width=32, height=32, view_dist=3.25,
                        angle_speed=0.03, timeout_s=15.0)
    w.close()
    with pytest.raises(RuntimeError):
        w.render_frame("idle")


def test_close_is_idempotent(worker):
    worker.close()
    worker.close()  # must not raise a second time


def test_faces_available_without_rendering_a_frame(worker):
    """CodecAvatarProvider's startup log line reads len(self._source.faces)
    before any render_frame() call — this must work immediately after
    construction, not require a round-trip to the child process."""
    assert len(worker.faces) > 0
