#!/usr/bin/env python3
"""
tests/test_tile_avatar.py
Covers app/tile_avatar.py — the pure pixel/row geometry and roster
resolution a roundtable tile's 3D head depends on.

Deliberately display-free, like the module it tests: no pygame, no GL, no
X display, no tmux. That is the whole point of tile_avatar.py existing as
a separate module — the arithmetic that positions a window over a live
broadcast tile has to be checkable on a headless box before it ships.
"""
import pytest

import tile_avatar
from tile_avatar import (AVATAR_TOP_INSET_PX, MIN_AVATAR_ROWS,
                         avatar_subpanel_rows, resolve_slot_character_params,
                         tile_avatar_rect)

# The deployed roundtable geometry: CAPTURE_RESOLUTION=1920x1080 with
# FONT_SIZE=7 gives a 6x12px cell -> a 320x90 char grid -> an even 4x2 grid
# of 80x45-char tiles -> 480x540 pixels per tile.
TILE_W, TILE_H = 480, 540


def _assert_inside_band(rect, pane_rect, fraction=tile_avatar.DEFAULT_AVATAR_FRACTION):
    """The window must fit entirely inside the top `fraction` of the tile —
    overflowing it paints over the AVATAR/TEXT divider and the dialogue."""
    x, y, w, h = rect
    px, py, pw, ph = pane_rect
    band_bottom = py + int(ph * fraction)
    assert y >= py + 1, "window must not sit on the pane's top border row"
    assert y + h <= band_bottom
    assert x >= px and x + w <= px + pw


def test_avatar_rect_for_the_origin_tile_of_the_real_4x2_grid():
    """Top-left tile at 1920x1080: a 200px square, centred horizontally in
    the 480px tile (x=140), inset 8px from the top so tmux's own border row
    keeps being drawn, inside the 216px reserved band."""
    pane = (0, 0, TILE_W, TILE_H)
    x, y, w, h = tile_avatar_rect(pane)
    assert (w, h) == (200, 200)
    assert w == h  # square
    assert x == (TILE_W - w) // 2 == 140
    assert y == AVATAR_TOP_INSET_PX == 8
    _assert_inside_band((x, y, w, h), pane)


def test_avatar_rect_for_a_non_origin_tile_offsets_by_the_pane_origin():
    """Bottom row, third column of the 4x2 grid: same window, translated by
    the tile's own origin — nothing may be measured from the screen."""
    pane = (960, 540, TILE_W, TILE_H)
    x, y, w, h = tile_avatar_rect(pane)
    assert (w, h) == (200, 200)
    assert x == 960 + 140
    assert y == 540 + AVATAR_TOP_INSET_PX
    _assert_inside_band((x, y, w, h), pane)


def test_avatar_rect_scales_with_a_different_capture_resolution():
    """A 2560x1440 capture at the same font gives 640x720 tiles. Nothing is
    hardcoded to the 480x540 default: the window still centres and still
    fits the (larger) band."""
    pane = (0, 0, 640, 720)
    x, y, w, h = tile_avatar_rect(pane)
    assert w == h
    assert x == (640 - w) // 2
    assert y == AVATAR_TOP_INSET_PX
    _assert_inside_band((x, y, w, h), pane)


def test_avatar_rect_clamps_a_window_larger_than_the_band():
    """A requested window bigger than the reserved band shrinks to fit
    rather than spilling over the dialogue rows."""
    pane = (0, 0, TILE_W, TILE_H)
    x, y, w, h = tile_avatar_rect(pane, window_px=10_000)
    assert w == h
    _assert_inside_band((x, y, w, h), pane)


@pytest.mark.parametrize("pane", [
    (0, 0, 0, 0),          # a pane detector that returned nothing usable
    (10, 10, 4, 4),        # absurdly tiny
    (0, 0, -480, -540),    # negative
    (0, 0, 480, 0),        # zero height only
    (),                    # not rect-shaped at all
    None,                  # detect_pane_rect() failure value
])
def test_degenerate_rects_degrade_instead_of_raising(pane):
    """A tile must never crash the pane it draws in (tile_pane.py's
    'degrade, never blank' rule): a useless rect yields a zero-sized window
    the caller can skip, not an exception."""
    rect = tile_avatar_rect(pane)
    assert isinstance(rect, tuple) and len(rect) == 4
    assert all(isinstance(v, int) for v in rect)
    assert rect[2] >= 0 and rect[3] >= 0


def test_avatar_subpanel_rows_for_a_real_45_row_tile():
    """0.4 of 45 rows floors to 18 — 216px at the 12px cell, matching the
    pixel band tile_avatar_rect reserves."""
    assert avatar_subpanel_rows(45) == 18


def test_avatar_subpanel_rows_floors_at_the_ascii_face_height():
    """A tile too short for a 3D head still reserves the 3 rows today's
    ASCII face (tile_pane.TILE_FACES) needs."""
    assert avatar_subpanel_rows(5) == MIN_AVATAR_ROWS
    assert avatar_subpanel_rows(1) == 1  # never more rows than the tile has


@pytest.mark.parametrize("rows", [1, 2, 3, 6, 12, 20, 45, 90])
def test_avatar_subpanel_rows_never_exceeds_the_tile_height(rows):
    """Reserving more rows than the tile has is how a frame ends up taller
    than its pane, which scrolls the avatar off the top."""
    reserved = avatar_subpanel_rows(rows)
    assert 0 < reserved <= rows


def test_resolve_slot_character_params_reads_the_mapping_form():
    """The avatar-aware roster entry: a display name PLUS the 3D preset."""
    config = {"roster": {"tuber_2": {"name": "Vigil", "character_params": "nyx1"}}}
    assert resolve_slot_character_params(config, "tuber_2") == "nyx1"


def test_resolve_slot_character_params_returns_none_for_a_plain_string_entry():
    """Today's roster form. A display name is not a preset name — guessing
    one would render the wrong head; None means 'keep the ASCII face'."""
    config = {"roster": {"tuber_1": "Chadwick"}}
    assert resolve_slot_character_params(config, "tuber_1") is None


def test_resolve_slot_character_params_passes_an_inline_slider_mapping_through():
    """The character generator's cast export writes sliders inline, not a
    preset name — the same inline form a channel's codec_avatar takes."""
    sliders = {"head_width": 0.45, "eye_size": 0.6, "accent_color": "RED"}
    config = {"roster": {"tuber_2": {"name": "Harry", "character_params": sliders}}}
    assert resolve_slot_character_params(config, "tuber_2") == sliders


def test_resolve_slot_character_params_returns_a_copy_of_the_mapping():
    sliders = {"preset": "nyx1", "eye_size": 0.9}
    config = {"roster": {"tuber_2": {"name": "Harry", "character_params": sliders}}}
    resolved = resolve_slot_character_params(config, "tuber_2")
    resolved["eye_size"] = 0.1
    assert sliders["eye_size"] == 0.9


def test_resolve_slot_character_params_returns_none_for_a_missing_slot():
    config = {"roster": {"tuber_1": {"name": "Vigil", "character_params": "nyx1"}}}
    assert resolve_slot_character_params(config, "tuber_5") is None


@pytest.mark.parametrize("config", [
    None,
    {},
    {"roster": None},
    {"roster": {}},
    {"roster": "not-a-mapping"},
    "not-a-config",
    {"roster": {"tuber_3": {"name": "Iris"}}},          # mapping, no preset
    {"roster": {"tuber_3": {"character_params": "  "}}},  # blank preset
    {"roster": {"tuber_3": {"character_params": {}}}},    # empty slider mapping
    {"roster": {"tuber_3": {"character_params": 42}}},    # neither form
    {"roster": {"tuber_3": {"character_params": ["nyx1"]}}},
])
def test_resolve_slot_character_params_degrades_to_none(config):
    assert resolve_slot_character_params(config, "tuber_3") is None


# ── the module-scope-import rule ─────────────────────────────────────────────
def test_importing_tile_avatar_pulls_in_no_display_library():
    """The load-bearing property of this module: importing it must not
    import pygame, moderngl, OpenGL or the provider — otherwise the geometry
    that positions a head over a live broadcast tile stops being checkable
    on a headless box, which is what CI and this suite are. The runtime
    wrapper below the pure functions does all of that INSIDE its methods."""
    import importlib
    import sys

    for module in ("pygame", "moderngl", "OpenGL",
                   "avatar_providers.codec_avatar"):
        sys.modules.pop(module, None)
    importlib.reload(tile_avatar)
    for module in ("pygame", "moderngl", "OpenGL",
                   "avatar_providers.codec_avatar"):
        assert module not in sys.modules, \
            f"import tile_avatar dragged in {module}"


# ── build_tile_avatar_config ─────────────────────────────────────────────────
def test_config_matches_the_shape_codec_avatar_reads():
    """CodecAvatarProvider reads avatar_config['codec_avatar'][...]; a
    mismatch here fails at provider construction on a live pane, not in a
    test, so the shape is asserted key by key."""
    cfg = tile_avatar.build_tile_avatar_config("gm0", (140, 8, 200, 200))
    assert cfg["provider"] == "codec_avatar"
    block = cfg["codec_avatar"]
    assert block["character_params"] == "gm0"
    assert block["window_pos"] == [140, 8]
    assert (block["width"], block["height"]) == (200, 200)
    assert block["background"] == tile_avatar.TILE_AVATAR_BACKGROUND


def test_config_always_requests_the_gpu_subprocess():
    """The provider's own process owns the pygame window and therefore sets
    AVATAR_HAS_PYGAME_WINDOW=1, which makes gl_raster refuse GPU rendering
    IN THAT PROCESS — so without the subprocess there is no hardware
    acceleration available at all, and eight software heads is the cost this
    design cannot pay."""
    cfg = tile_avatar.build_tile_avatar_config("gm0", (0, 0, 200, 200))
    assert cfg["codec_avatar"]["gpu_subprocess"] is True


def test_config_carries_the_tile_framerate_not_the_provider_default():
    """Eight simultaneous heads in one container; a tile head is a secondary
    cue, not the frame's focal point."""
    cfg = tile_avatar.build_tile_avatar_config("gm0", (0, 0, 200, 200))
    assert cfg["codec_avatar"]["fps"] == tile_avatar.TILE_AVATAR_FPS == 12


# ── TileAvatar: every failure degrades to the ASCII face ─────────────────────
class _FakeProvider:
    """Stands in for CodecAvatarProvider — no pygame, no display, no GPU."""
    instances = []

    def __init__(self, avatar_config, name, title, fail_on_tick=False):
        self.avatar_config = avatar_config
        self.name = name
        self.ticks = []
        self.fail_on_tick = fail_on_tick
        _FakeProvider.instances.append(self)

    def render_tick(self, expression, bubble_lines):
        if self.fail_on_tick:
            raise RuntimeError("gpu worker died")
        self.ticks.append(expression)


@pytest.fixture
def fake_provider(monkeypatch):
    """Patch the provider class the constructor imports lazily."""
    import avatar_providers.codec_avatar as codec_avatar
    _FakeProvider.instances = []
    monkeypatch.setattr(codec_avatar, "CodecAvatarProvider", _FakeProvider)
    return _FakeProvider


def test_tile_avatar_becomes_active_and_ticks(fake_provider):
    avatar = tile_avatar.TileAvatar("tuber_1", "gm0", (0, 0, TILE_W, TILE_H))
    assert avatar.active is True
    assert avatar.tick("speaking") is True
    assert fake_provider.instances[-1].ticks == ["speaking"]


def test_tile_avatar_construction_failure_reports_inactive(monkeypatch, capsys):
    """No pygame, no DISPLAY, a window that will not open — a tile must fall
    back to ASCII, never raise into the pane loop."""
    import avatar_providers.codec_avatar as codec_avatar

    def explode(*args, **kwargs):
        raise RuntimeError("pygame.error: No available video device")

    monkeypatch.setattr(codec_avatar, "CodecAvatarProvider", explode)
    avatar = tile_avatar.TileAvatar("tuber_1", "gm0", (0, 0, TILE_W, TILE_H))
    assert avatar.active is False
    assert avatar.tick("idle") is False
    assert "No available video device" in capsys.readouterr().err


def test_tile_avatar_construction_failure_logs_to_stderr_only(monkeypatch, capsys):
    """stdout IS the pane's live video display — diagnostics must never
    reach it."""
    import avatar_providers.codec_avatar as codec_avatar
    monkeypatch.setattr(codec_avatar, "CodecAvatarProvider",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    tile_avatar.TileAvatar("tuber_1", "gm0", (0, 0, TILE_W, TILE_H))
    assert capsys.readouterr().out == ""


def test_a_raising_tick_marks_the_head_inactive_and_logs_once(monkeypatch, capsys):
    """A provider that failed once keeps failing: retrying at 12fps across
    eight tiles would pay the cost of the failure ~100 times a second. One
    stderr line, then ASCII for the rest of this process's life."""
    import avatar_providers.codec_avatar as codec_avatar
    monkeypatch.setattr(
        codec_avatar, "CodecAvatarProvider",
        lambda cfg, name, title: _FakeProvider(cfg, name, title, fail_on_tick=True))

    avatar = tile_avatar.TileAvatar("tuber_1", "gm0", (0, 0, TILE_W, TILE_H))
    assert avatar.active is True
    assert avatar.tick("idle") is False
    assert avatar.active is False
    for _ in range(20):
        assert avatar.tick("idle") is False

    err = capsys.readouterr().err
    assert err.count("[tile_avatar]") == 1, "logged once per frame, not once"
    assert "gpu worker died" in err


def test_tile_avatar_with_no_room_for_a_window_is_inactive(fake_provider):
    """A pane rect the detector could not make sense of must not open a
    zero-sized window — tile_avatar_rect signals that by returning a rect of
    width/height 0, and the head has to read it as 'keep the ASCII face'."""
    avatar = tile_avatar.TileAvatar("tuber_1", "gm0", (0, 0, 0, 0))
    assert avatar.active is False
    assert fake_provider.instances == []


def test_close_is_best_effort_and_idempotent(fake_provider):
    avatar = tile_avatar.TileAvatar("tuber_1", "gm0", (0, 0, TILE_W, TILE_H))
    avatar.close()
    avatar.close()
    assert avatar.active is False
    assert avatar.tick("idle") is False


def test_close_on_a_never_constructed_head_does_not_raise(monkeypatch):
    import avatar_providers.codec_avatar as codec_avatar
    monkeypatch.setattr(codec_avatar, "CodecAvatarProvider",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    tile_avatar.TileAvatar("tuber_1", "gm0", (0, 0, TILE_W, TILE_H)).close()


# ── the factory: one code path for the caller ────────────────────────────────
def test_factory_returns_none_for_an_uncast_slot(fake_provider):
    """roundtable.yaml deliberately omits tuber_4 from its roster. That slot
    keeps today's ASCII behaviour and opens no window at all."""
    config = {"roster": {"tuber_1": {"name": "Chadwick", "character_params": "gm0"}}}
    assert tile_avatar.make_tile_avatar(config, "tuber_4", (0, 0, TILE_W, TILE_H)) is None
    assert fake_provider.instances == []


def test_factory_returns_none_for_a_plain_string_roster_entry(fake_provider):
    """Today's roster form names a character but configures no preset."""
    assert tile_avatar.make_tile_avatar({"roster": {"tuber_1": "Chadwick"}},
                                        "tuber_1", (0, 0, TILE_W, TILE_H)) is None


def test_factory_returns_none_without_a_detected_pane_rect(fake_provider):
    config = {"roster": {"tuber_1": {"character_params": "gm0"}}}
    assert tile_avatar.make_tile_avatar(config, "tuber_1", None) is None


def test_factory_builds_a_head_for_a_cast_slot(fake_provider):
    config = {"roster": {"tuber_1": {"name": "Chadwick", "character_params": "gm0"}}}
    avatar = tile_avatar.make_tile_avatar(config, "tuber_1", (0, 0, TILE_W, TILE_H))
    assert avatar is not None and avatar.active
    assert avatar.rect == tile_avatar_rect((0, 0, TILE_W, TILE_H))
    block = fake_provider.instances[-1].avatar_config["codec_avatar"]
    assert block["character_params"] == "gm0"
    assert block["window_pos"] == [140, 8]


# ── geometry detection retries the startup race ──────────────────────────────
def test_detect_tile_pane_rect_retries_past_a_still_forming_window(monkeypatch):
    """startup.sh launches every pane's process BEFORE it creates and resizes
    the xterm window, so a one-shot read reliably returns a nonexistent or
    half-formed rect (a 1x0 was observed live)."""
    import pane_geometry
    calls = []

    def flaky():
        calls.append(1)
        return (0, 0, 1, 0) if len(calls) < 3 else (0, 0, TILE_W, TILE_H)

    monkeypatch.setattr(pane_geometry, "detect_pane_rect", flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert tile_avatar.detect_tile_pane_rect(retry_s=5.0) == (0, 0, TILE_W, TILE_H)
    assert len(calls) == 3


def test_detect_tile_pane_rect_gives_up_and_returns_none(monkeypatch):
    import pane_geometry
    monkeypatch.setattr(pane_geometry, "detect_pane_rect", lambda: None)
    assert tile_avatar.detect_tile_pane_rect(retry_s=0) is None


def test_detect_tile_pane_rect_swallows_a_raising_detector(monkeypatch):
    import pane_geometry
    monkeypatch.setattr(pane_geometry, "detect_pane_rect",
                        lambda: (_ for _ in ()).throw(OSError("no xdotool")))
    assert tile_avatar.detect_tile_pane_rect(retry_s=0) is None
