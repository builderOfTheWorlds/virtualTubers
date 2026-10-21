#!/usr/bin/env python3
"""
tests/test_tile_pane_memory.py
Regression guards for the roundtable tile memory leak (tile_pane.py processes
grew to 4-10 GiB RSS over ~3.8 days; measured with scripts/tile_leak_probe.py).

These assert STRUCTURE (sizes, call counts), never wall-clock RSS, so they are
deterministic on a headless box without pygame/moderngl:

  * root cause: TileAvatar.tick() must pump/discard the SDL window's events
    every frame. A window that is flipped 30x a second but never pumped grew
    ~0.5 KB/frame (SHAPE-clipped head over an xterm) — A/B runs: no pump =
    +15 MB per 30k frames, per-frame `pygame.event.clear()` = flat.
  * the bounded structures a long-lived tile owns stay bounded: the dialogue
    deque, the live renderer registry, the GPU mesh cache, the screen-map
    cache.
"""
import io
import os

import pytest

import gl_raster
import live_pane
import pixel_raster
import tile_avatar
import tile_pane


# ── root cause: the head's window events are drained every frame ─────────────
class _FakeEvent:
    def __init__(self, fail=False):
        self.clears = 0
        self._fail = fail

    def clear(self):
        self.clears += 1
        if self._fail:
            raise RuntimeError("SDL video system not initialised")


class _FakePygame:
    def __init__(self, fail=False):
        self.event = _FakeEvent(fail)


class _FakeProvider:
    def __init__(self, pygame=None, raises=False):
        self._pygame = pygame
        self.raises = raises
        self.ticks = 0

    def render_tick(self, expression, bubble_lines, **extra):
        self.ticks += 1
        if self.raises:
            raise RuntimeError("gpu worker died")


def _head(provider):
    """A TileAvatar wired to a fake provider (no window, no SDL)."""
    head = tile_avatar.TileAvatar("tuber_3", None, (0, 0, 0, 0))
    head._failed = False
    head.active = True
    head._provider = provider
    return head


def test_every_tick_pumps_the_window_events():
    pygame = _FakePygame()
    head = _head(_FakeProvider(pygame))
    for _ in range(250):
        assert head.tick("speaking", gaze=(0.1, 0.0), mouth_open=0.4) is True
    assert pygame.event.clears == 250


def test_event_pump_failure_never_fails_the_head():
    pygame = _FakePygame(fail=True)
    head = _head(_FakeProvider(pygame))
    assert head.tick("idle") is True
    assert head.active is True
    assert pygame.event.clears == 1


def test_provider_without_a_window_still_ticks():
    # The in-process CPU fallback and test doubles have no `_pygame`.
    head = _head(_FakeProvider(pygame=None))
    assert head.tick("idle") is True


def test_failed_render_marks_head_inactive_and_does_not_pump():
    pygame = _FakePygame()
    head = _head(_FakeProvider(pygame, raises=True))
    assert head.tick("idle") is False
    assert head.active is False
    assert pygame.event.clears == 0


# ── bounded structures a long-lived tile owns ────────────────────────────────
def test_dialogue_history_is_bounded(tmp_path):
    renderer = tile_pane.TileRenderer("tuber_3", str(tmp_path / "s.json"),
                                      out=io.StringIO(), history=50)
    for n in range(5000):
        renderer._absorb({"expression": "speaking"}, f"line number {n}")
    assert len(renderer.lines) == 50
    assert renderer.lines.maxlen == 50
    assert list(renderer.lines)[-1] == "line number 4999"


def test_default_history_is_bounded():
    assert tile_pane.TileRenderer.DEFAULT_HISTORY <= 200


@pytest.fixture
def fast_live(monkeypatch, tmp_path):
    """perform_live_line with no real sleeping and no real player."""
    import replay
    monkeypatch.setenv("REPLAY_STOP_FILE", str(tmp_path / "no-stop"))
    monkeypatch.setenv("VOICE_GATE_DIR", str(tmp_path / "gate"))

    class FastPacer(replay.Pacer):
        def __init__(self, *a, **kw):
            kw["speed"] = 10_000.0
            super().__init__(**kw)

    monkeypatch.setattr(replay, "Pacer", FastPacer)
    live_pane._renderers.clear()
    yield
    for slot in list(live_pane._renderers):
        live_pane.reset_live(slot)


def test_many_live_lines_keep_one_bounded_renderer(fast_live, tmp_path):
    relay_dir = str(tmp_path / "relay")
    os.makedirs(relay_dir)
    state_path = str(tmp_path / "tuber_3.state.json")
    sink = io.StringIO()
    for n in range(120):
        payload = {"type": "live_line", "line_id": f"l{n}", "speaker": "tuber_3",
                   "name": "Bob", "text": f"Line {n}: the ledger balances.",
                   "audio_path": None, "duration": 0.01}
        live_pane.perform_live_line(payload, "tuber_3", relay_dir,
                                    state_path=state_path, config={}, out=sink)
        sink.seek(0)
        sink.truncate()
    assert list(live_pane._renderers) == ["tuber_3"]
    renderer = live_pane._renderers["tuber_3"]
    assert len(renderer.lines) == min(120, renderer.lines.maxlen)
    assert renderer.lines.maxlen <= 200


def test_screen_map_cache_is_bounded():
    pixel_raster._SCREEN_MAP_CACHE.clear()
    for size in range(40, 40 + 3 * pixel_raster._SCREEN_MAP_CACHE_MAX):
        pixel_raster._screen_maps(size, size, 0.2, 0.3, 0.01, 7)
        assert len(pixel_raster._SCREEN_MAP_CACHE) <= pixel_raster._SCREEN_MAP_CACHE_MAX
    pixel_raster._SCREEN_MAP_CACHE.clear()


# ── GPU mesh cache: capped, and evictions are released explicitly ────────────
class _GLObj:
    def __init__(self, log, name):
        self.log, self.name, self.released = log, name, False

    def write(self, data):
        pass

    def release(self):
        self.released = True
        self.log.append(self.name)


class _FakeCtx:
    def __init__(self):
        self.released = []
        self.created = 0

    def _obj(self, kind):
        self.created += 1
        return _GLObj(self.released, f"{kind}{self.created}")

    def buffer(self, data):
        return self._obj("buf")

    def vertex_array(self, prog, content):
        return self._obj("vao")

    def simple_framebuffer(self, size):
        return self._obj("fbo")


def _mesh(n_faces):
    import numpy as np
    verts = np.random.rand(n_faces * 3, 3).astype("f4")
    faces = np.arange(n_faces * 3, dtype="i4").reshape(n_faces, 3)
    mats = np.zeros(n_faces, dtype="i4")
    return verts, faces, mats


def test_mesh_cache_is_capped_and_releases_evicted_gl_objects(monkeypatch):
    monkeypatch.setattr(gl_raster, "_mesh_cache", {})
    ctx = _FakeCtx()
    total = gl_raster._MESH_CACHE_MAX + 5
    for n in range(1, total + 1):
        gl_raster._get_mesh_gpu_objects(ctx, object(), *_mesh(n), 64, 64)
        assert len(gl_raster._mesh_cache) <= gl_raster._MESH_CACHE_MAX
    evicted = total - gl_raster._MESH_CACHE_MAX
    assert len(ctx.released) == evicted * 5       # vao + 3 buffers + fbo each
    # the survivors are the most recent topologies, none of them released
    assert sorted(k[0] for k in gl_raster._mesh_cache) == list(
        range(evicted + 1, total + 1))


def test_mesh_cache_reuses_allocations_for_the_same_topology(monkeypatch):
    monkeypatch.setattr(gl_raster, "_mesh_cache", {})
    ctx = _FakeCtx()
    for _ in range(500):
        gl_raster._get_mesh_gpu_objects(ctx, object(), *_mesh(12), 64, 64)
    assert len(gl_raster._mesh_cache) == 1
    assert ctx.created == 5      # allocated once, re-uploaded 499 times
    assert ctx.released == []
