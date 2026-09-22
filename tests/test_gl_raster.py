#!/usr/bin/env python3
"""
tests/test_gl_raster.py
Covers app/gl_raster.py — the GPU-accelerated counterpart to
pixel_raster.py (docs/gl_raster_benchmark.md). Two concerns:

1. is_available()/render_with_fallback() degrade correctly when a GPU
   context can't be created — the eventual worker container's GPU
   provisioning is still an open question (see that doc), so every caller
   MUST work on a GPU-less box, not just this dev machine's RTX 3080.
2. When a GPU context IS available (true on this dev machine and in CI
   with a software GL fallback), gl_raster.render() must agree with
   pixel_raster.render() on the same scene — two renderers of one scene
   description should produce the same picture, not two different styles.
"""
import numpy as np
import pytest

import gl_raster
import pixel_raster
from codec_head import build_codec_head

GPU_AVAILABLE = gl_raster.is_available()
skip_no_gpu = pytest.mark.skipif(
    not GPU_AVAILABLE, reason="no GPU/GL context available in this environment")


def test_is_available_is_cached_and_boolean():
    result1 = gl_raster.is_available()
    result2 = gl_raster.is_available()
    assert isinstance(result1, bool)
    assert result1 == result2


def test_is_available_refuses_gpu_when_process_owns_a_pygame_window(monkeypatch):
    """Regression test for the gx10 GPU-vs-pygame-window crash: even with
    a real GPU passed through and EGL context creation succeeding on its
    own, sharing that GL/DRI resource with an SDL/pygame X11 window in
    the SAME process either crashes (X Error BadAccess/GLXMakeCurrent) or
    silently falls back to software rendering, depending on creation
    order — reproduced directly on gx10. codec_avatar sets
    AVATAR_HAS_PYGAME_WINDOW=1 before creating its window; is_available()
    must refuse GPU rendering whenever that's set, without even
    attempting a GL context, and WITHOUT this depending on any real GPU
    hardware being present to prove it — that's exactly why this uses
    monkeypatch instead of running on live hardware."""
    monkeypatch.setattr(gl_raster, "_available", None)
    monkeypatch.setenv("AVATAR_HAS_PYGAME_WINDOW", "1")

    def _boom():
        raise AssertionError(
            "gl_raster must not attempt to create a GL context at all "
            "when AVATAR_HAS_PYGAME_WINDOW=1")
    monkeypatch.setattr(gl_raster, "_ensure_context", _boom)

    assert gl_raster.is_available() is False


def test_render_with_fallback_returns_backend_label():
    verts, faces, mats = build_codec_head("chadwick")
    img, backend = gl_raster.render_with_fallback(
        verts, faces, mats, width=64, height=64)
    assert backend in ("gpu", "cpu")
    assert img.shape == (64, 64, 3)


def test_render_with_fallback_uses_cpu_when_gpu_unavailable(monkeypatch):
    monkeypatch.setattr(gl_raster, "is_available", lambda: False)
    verts, faces, mats = build_codec_head("chadwick")
    img, backend = gl_raster.render_with_fallback(
        verts, faces, mats, width=32, height=32)
    assert backend == "cpu"
    expected = pixel_raster.render(verts, faces, mats, width=32, height=32)
    np.testing.assert_allclose(img, expected, atol=1e-5)


def test_render_with_fallback_falls_back_when_gpu_render_raises(monkeypatch):
    """A GL context that IS available but fails mid-render (driver hiccup,
    lost context) must still produce a frame, not drop one."""
    monkeypatch.setattr(gl_raster, "is_available", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("simulated GL failure")
    monkeypatch.setattr(gl_raster, "render", _boom)

    verts, faces, mats = build_codec_head("chadwick")
    img, backend = gl_raster.render_with_fallback(
        verts, faces, mats, width=32, height=32)
    assert backend == "cpu"
    assert img.shape == (32, 32, 3)


def test_render_with_fallback_falls_back_when_gpu_returns_near_black(monkeypatch):
    """Regression test for the gx10 deploy bug: a GL context that IS
    available and renders WITHOUT raising, but produces a near-black frame
    (e.g. a winding/depth-state mismatch silently culling every triangle
    on that GPU/driver), must be treated as a failure and fall back to
    pixel_raster — not returned as if it were a legitimate dark frame."""
    monkeypatch.setattr(gl_raster, "is_available", lambda: True)

    def _black(verts, faces, materials, width=64, height=64, **kwargs):
        return np.zeros((height, width, 3), dtype=np.float32)
    monkeypatch.setattr(gl_raster, "render", _black)

    verts, faces, mats = build_codec_head("chadwick")
    img, backend = gl_raster.render_with_fallback(
        verts, faces, mats, width=32, height=32)
    assert backend == "cpu"
    assert img.mean() > 0.01  # the real CPU render of a lit face, not black


def test_create_gl_context_prefers_egl_when_it_succeeds():
    """Regression test for the gx10 GPU-passthrough bug: GLX-via-Xvfb is
    ALWAYS software-rendered (llvmpipe) even when the host's real GPU is
    correctly passed into the container (/dev/dri present, nvidia-smi
    working) — only EGL (talking to /dev/dri/renderD128 directly) picks
    up real GPU acceleration there. _create_gl_context must try EGL
    first and use it whenever create_context(backend="egl") succeeds,
    never falling through to the default (GLX/X11) backend in that case."""
    calls = []

    class FakeCtx:
        def __init__(self, tag):
            self.info = {"GL_RENDERER": tag}

    class FakeModernGL:
        @staticmethod
        def create_context(standalone=True, backend=None, **kw):
            calls.append(backend)
            return FakeCtx("egl-backend" if backend == "egl" else "default-backend")

    ctx = gl_raster._create_gl_context(FakeModernGL)
    assert ctx.info["GL_RENDERER"] == "egl-backend"
    assert calls == ["egl"]  # default backend never attempted


def test_create_gl_context_falls_back_when_egl_unavailable():
    """When EGL context creation itself raises (no /dev/dri, no EGL
    library, etc.), _create_gl_context must fall back to moderngl's
    default backend rather than propagating the EGL failure — hosts
    without EGL support (e.g. a plain X11/GLX dev box) must keep
    working exactly as they did before EGL was tried first."""
    calls = []

    class FakeCtx:
        def __init__(self, tag):
            self.info = {"GL_RENDERER": tag}

    class FakeModernGL:
        @staticmethod
        def create_context(standalone=True, backend=None, **kw):
            calls.append(backend)
            if backend == "egl":
                raise RuntimeError("simulated: no EGL device")
            return FakeCtx("default-backend")

    ctx = gl_raster._create_gl_context(FakeModernGL)
    assert ctx.info["GL_RENDERER"] == "default-backend"
    assert calls == ["egl", None]  # tried EGL, then fell back to default


@skip_no_gpu
def test_gpu_render_matches_cpu_render_shape_and_range():
    verts, faces, mats = build_codec_head("chadwick")
    img = gl_raster.render(verts, faces, mats, width=200, height=250)
    assert img.shape == (250, 200, 3)
    assert np.isfinite(img).all()
    assert img.min() >= 0.0 and img.max() <= 1.0


@skip_no_gpu
def test_gpu_render_agrees_with_cpu_render_on_mean_brightness():
    """Not pixel-identical (different AA/rasterization rules at edges), but
    the same scene lit the same way should land at roughly the same
    brightness — this is the check that would catch a wrong light
    direction, an inverted normal, or a swapped palette index."""
    verts, faces, mats = build_codec_head("chadwick")
    cpu = pixel_raster.render(verts, faces, mats, width=300, height=380, rot_y=0.0)
    gpu = gl_raster.render(verts, faces, mats, width=300, height=380, rot_y=0.0)
    assert cpu.mean() == pytest.approx(gpu.mean(), rel=0.25)
    # Lit-pixel FOOTPRINT (how much of the frame the head covers) should
    # also roughly agree — this catches a mismatched camera/view matrix.
    cpu_lit = (cpu.sum(axis=2) > 0.02).sum()
    gpu_lit = (gpu.sum(axis=2) > 0.02).sum()
    assert cpu_lit == pytest.approx(gpu_lit, rel=0.15)


@skip_no_gpu
def test_gpu_render_is_deterministic():
    verts, faces, mats = build_codec_head("chadwick")
    a = gl_raster.render(verts, faces, mats, width=100, height=120, rot_y=0.4)
    b = gl_raster.render(verts, faces, mats, width=100, height=120, rot_y=0.4)
    np.testing.assert_allclose(a, b, atol=1e-6)


@skip_no_gpu
def test_gpu_render_respects_tint():
    from pixel_raster import TINT_AMBER, TINT_CODEC_GREEN
    verts, faces, mats = build_codec_head("chadwick")
    green = gl_raster.render(verts, faces, mats, width=80, height=100,
                             tint=TINT_CODEC_GREEN)
    amber = gl_raster.render(verts, faces, mats, width=80, height=100,
                             tint=TINT_AMBER)
    assert not np.allclose(green, amber)


@skip_no_gpu
def test_gpu_render_at_pane_resolution_beats_realtime_floor():
    """The whole point of gl_raster over pixel_raster: pixel_raster measured
    ~12-14fps at pane resolution (docs/character_generator.md), well below
    a 30fps stream target. This is a floor, not a tight benchmark — CI
    hardware varies — but it must comfortably clear pixel_raster's ceiling."""
    import time
    verts, faces, mats = build_codec_head("chadwick")
    # warmup: first call pays one-time context/shader compile cost
    gl_raster.render(verts, faces, mats, width=560, height=700)
    n = 20
    t0 = time.perf_counter()
    for i in range(n):
        gl_raster.render(verts, faces, mats, width=560, height=700, rot_y=i * 0.01)
    dt = time.perf_counter() - t0
    fps = n / dt
    assert fps > 30, f"gl_raster only hit {fps:.1f}fps at pane resolution"


@skip_no_gpu
def test_repeated_render_does_not_grow_the_mesh_cache():
    """Regression test for the gx10 memory-leak bug: render() used to
    allocate a fresh VAO/buffers/FBO on EVERY call and release them again
    at the end, but under gx10's llvmpipe (software GL, no real GPU
    attached to the container) that per-frame churn leaked ~4MB/frame
    (34GB RSS in under 5 minutes at 30fps) and correlated with the
    renderer eventually going solid black. Repeated calls with the SAME
    verts/faces/materials objects (the live avatar's actual usage
    pattern — one static head, camera rotates every tick) must reuse one
    cache entry, not grow without bound."""
    verts, faces, mats = build_codec_head("chadwick")
    gl_raster.render(verts, faces, mats, width=64, height=64)
    size_after_one = len(gl_raster._mesh_cache)
    for i in range(10):
        gl_raster.render(verts, faces, mats, width=64, height=64, rot_y=i * 0.1)
    assert len(gl_raster._mesh_cache) == size_after_one, (
        "repeated renders of the same mesh grew the GPU object cache — "
        "the per-frame leak regressed")
