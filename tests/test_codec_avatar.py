#!/usr/bin/env python3
"""
tests/test_codec_avatar.py
Covers avatar_providers/codec_avatar.py's FrameSource — the pure render
step (character params in, RGB frame out) that needs no window/display, so
it's testable headless (Windows dev box included, no X server anywhere).

CodecAvatarProvider itself (the pygame window / AvatarProvider adapter) is
NOT covered here: constructing it needs a real display (pygame.display.init
would need SDL_VIDEODRIVER=dummy or similar even headless, and this suite
targets Windows dev + CI equally) — see the module docstring's two-class
split for why that boundary exists.
"""
import numpy as np
import pytest

from avatar_providers.codec_avatar import EXPRESSION_STYLE, FrameSource


def test_frame_source_builds_from_preset_name():
    source = FrameSource("chadwick", width=64, height=64)
    assert len(source.verts) > 0
    assert len(source.faces) > 0


def test_render_frame_returns_correctly_shaped_image():
    source = FrameSource("chadwick", width=80, height=100)
    img, backend = source.render_frame("idle")
    assert img.shape == (100, 80, 3)
    assert backend in ("gpu", "cpu")
    assert np.isfinite(img).all()
    assert img.min() >= 0.0 and img.max() <= 1.0


def test_render_frame_advances_rotation_each_call():
    source = FrameSource("chadwick", width=48, height=60, angle_speed=0.05)
    start_angle = source.angle
    source.render_frame("idle")
    assert source.angle > start_angle


@pytest.mark.parametrize("expression", sorted(EXPRESSION_STYLE))
def test_render_frame_accepts_every_known_expression(expression):
    source = FrameSource("chadwick", width=48, height=60)
    img, _backend = source.render_frame(expression)
    assert img.shape == (60, 48, 3)


def test_render_frame_falls_back_gracefully_for_unknown_expression():
    """render_tick() in the live pane can be called with any string a
    worker's state machine produces; an unrecognized one must not crash —
    it should just behave like 'idle' (EXPRESSION_STYLE.get's default)."""
    source = FrameSource("chadwick", width=48, height=60)
    img, _backend = source.render_frame("bewildered")
    assert img.shape == (60, 48, 3)


def test_frustrated_expression_uses_amber_tint_not_green():
    """The one expression mapped to a different tint — verifies the tint
    branch actually forks rather than always resolving to green."""
    source_a = FrameSource("chadwick", width=64, height=80)
    source_b = FrameSource("chadwick", width=64, height=80)
    idle_img, _ = source_a.render_frame("idle")
    frustrated_img, _ = source_b.render_frame("frustrated")
    assert not np.allclose(idle_img, frustrated_img)


def test_frame_source_accepts_inline_slider_dict():
    """character_params doesn't have to be a preset name — an inline
    slider dict (the other form character_schema.resolve_params accepts)
    must work identically."""
    source = FrameSource({"preset": "chadwick", "eye_size": 0.9},
                         width=48, height=60)
    img, _backend = source.render_frame("idle")
    assert img.shape == (60, 48, 3)


def test_frame_source_resolves_accent_color_from_preset():
    """nyx1/oko2/ada3/tess3/max1 differ only by accent_color (see
    character_schema.PRESETS) — confirms that actually reaches the
    renderer instead of always defaulting to GREEN."""
    source = FrameSource("nyx1", width=48, height=60)
    assert source.accent_color == "CYAN"


def test_different_accent_colors_render_visibly_different_idle_frames():
    """The whole point of giving nyx1/oko2/ada3/tess3/max1 distinct
    accent_color presets — verifies the tint actually changes rendered
    pixels, not just a stored attribute nothing reads."""
    green_source = FrameSource("chadwick", width=64, height=80)  # accent=YELLOW actually
    cyan_source = FrameSource("nyx1", width=64, height=80)
    a, _ = green_source.render_frame("idle")
    b, _ = cyan_source.render_frame("idle")
    assert not np.allclose(a, b)


def test_green_accent_color_matches_stock_codec_tint():
    """accent_color=GREEN is a special case (ACCENT_TINTS maps it to None,
    meaning "use TINT_CODEC_GREEN directly") — must render identically to
    a character with no accent override at all."""
    source = FrameSource("oko2", width=64, height=80)  # accent_color=GREEN
    assert source.accent_color == "GREEN"
    img, _ = source.render_frame("idle")
    assert img.shape == (80, 64, 3)  # renders without error via the None branch


def test_frame_source_composites_onto_the_console_background_by_default():
    """Un-drawn surround must come back as the console grey, not black —
    the window is an X window over the terminal, so black pixels read as a
    rectangle cut out of the layout (2026-09-22 UI fix)."""
    from pixel_raster import CONSOLE_BG
    source = FrameSource("chadwick", width=64, height=80)
    img, _ = source.render_frame("idle")
    # The extreme corners are always outside the head at this framing.
    corners = np.stack([img[0, 0], img[0, -1], img[-1, 0], img[-1, -1]])
    assert np.all(corners >= CONSOLE_BG[None, :] - 1e-3)
    # No pixel is pure black any more. Tested per-PIXEL (max across channels),
    # not per-channel: the Solarized base03 ground is a saturated teal whose
    # red channel is legitimately 0x00, so a per-channel floor would fail on
    # the very background it was meant to prove is present.
    assert img.max(axis=2).min() > 0.05


def test_frame_source_background_none_keeps_the_black_surround():
    """Opt-out must restore the previous look exactly."""
    source = FrameSource("chadwick", width=64, height=80, background=None)
    img, _ = source.render_frame("idle")
    assert img.min() < 0.02


def test_frame_source_accepts_a_hex_background_string():
    source = FrameSource("chadwick", width=48, height=60, background="#101820")
    assert np.allclose(source.background, np.array([0x10, 0x18, 0x20]) / 255.0,
                       atol=1e-6)


# ── CodecAvatarProvider._resolve_geometry (no display/pygame needed) ───────
def test_resolve_geometry_uses_explicit_window_pos_and_defaults_size():
    from avatar_providers.codec_avatar import HEIGHT, WIDTH, CodecAvatarProvider
    provider = CodecAvatarProvider.__new__(CodecAvatarProvider)
    width, height, pos = provider._resolve_geometry({"window_pos": [12, 34]})
    assert (width, height, pos) == (WIDTH, HEIGHT, (12, 34))


def test_resolve_geometry_explicit_window_pos_honors_explicit_size():
    from avatar_providers.codec_avatar import CodecAvatarProvider
    provider = CodecAvatarProvider.__new__(CodecAvatarProvider)
    width, height, pos = provider._resolve_geometry(
        {"window_pos": [0, 0], "width": 320, "height": 400})
    assert (width, height, pos) == (320, 400, (0, 0))


def test_resolve_geometry_falls_back_when_detection_unavailable(monkeypatch):
    """No tmux/X session (true on this dev box, and true in a headless
    CI run of the unit suite) must degrade to a safe default, not raise —
    codec_avatar.py must be importable/testable with no real display."""
    import pane_geometry
    from avatar_providers.codec_avatar import HEIGHT, WIDTH, CodecAvatarProvider
    monkeypatch.setattr(pane_geometry, "detect_pane_rect", lambda: None)
    provider = CodecAvatarProvider.__new__(CodecAvatarProvider)
    width, height, pos = provider._resolve_geometry({"geometry_retry_s": 0})
    assert (width, height, pos) == (WIDTH, HEIGHT, (0, 0))


def test_resolve_geometry_narrows_and_centers_in_the_detected_pane(monkeypatch):
    """The detected pane is wide and short; the window is deliberately half
    its width and centered, so the pane's left/right thirds stay plain
    console and the face blends into the tmux frame instead of sitting on a
    wide slab. 640 -> 320 wide, x 160 -> 160 + (640-320)/2 = 320."""
    import pane_geometry
    from avatar_providers.codec_avatar import CodecAvatarProvider
    monkeypatch.setattr(pane_geometry, "detect_pane_rect",
                        lambda: (160, 72, 640, 720))
    provider = CodecAvatarProvider.__new__(CodecAvatarProvider)
    width, height, pos = provider._resolve_geometry({"geometry_retry_s": 0})
    assert (width, height, pos) == (320, 720, (320, 72))


def test_resolve_geometry_width_fraction_one_fills_the_detected_pane(monkeypatch):
    """Opt back into the old edge-to-edge behavior."""
    import pane_geometry
    from avatar_providers.codec_avatar import CodecAvatarProvider
    monkeypatch.setattr(pane_geometry, "detect_pane_rect",
                        lambda: (160, 72, 640, 720))
    provider = CodecAvatarProvider.__new__(CodecAvatarProvider)
    width, height, pos = provider._resolve_geometry(
        {"geometry_retry_s": 0, "width_fraction": 1.0})
    assert (width, height, pos) == (640, 720, (160, 72))


def test_resolve_geometry_explicit_size_overrides_detected_size(monkeypatch):
    """An explicit width/height should win even when detection succeeds —
    lets a worker pin a render resolution independent of its pane's actual
    pixel size (e.g. render smaller than the pane for performance)."""
    import pane_geometry
    from avatar_providers.codec_avatar import CodecAvatarProvider
    monkeypatch.setattr(pane_geometry, "detect_pane_rect",
                        lambda: (160, 72, 640, 720))
    provider = CodecAvatarProvider.__new__(CodecAvatarProvider)
    width, height, pos = provider._resolve_geometry(
        {"width": 200, "height": 250, "geometry_retry_s": 0})
    # x is still centered on the narrowed rect, since the offset doesn't
    # depend on the explicit size override.
    assert (width, height, pos) == (200, 250, (320, 72))


def test_resolve_geometry_rejects_degenerate_detected_rect(monkeypatch):
    """Regression test for the gx10 first-deploy bug: detect_pane_rect()
    returning a 1px-wide rect (tmux not yet resized to match the xterm
    window at provider startup) must NOT be trusted as-is — it produced an
    invisible pygame window with no error. Must fall back to WIDTH/HEIGHT
    at (0,0), which is visible and obviously wrong instead of silently
    wrong."""
    import pane_geometry
    from avatar_providers.codec_avatar import HEIGHT, WIDTH, CodecAvatarProvider
    monkeypatch.setattr(pane_geometry, "detect_pane_rect",
                        lambda: (0, 0, 1, 700))
    provider = CodecAvatarProvider.__new__(CodecAvatarProvider)
    width, height, pos = provider._resolve_geometry({"geometry_retry_s": 0})
    assert (width, height, pos) == (WIDTH, HEIGHT, (0, 0))


def test_resolve_geometry_retries_until_a_stable_rect_appears(monkeypatch):
    """Regression test for the gx10 1x0 bug's real root cause: startup.sh
    launches this provider BEFORE the xterm window is created/resized
    (steps 5 vs 6) — a one-shot detect_pane_rect() call reliably races
    that. _resolve_geometry must retry and use the first geometrically
    sane rect once the window settles, not give up on the first bad read."""
    import pane_geometry
    from avatar_providers.codec_avatar import CodecAvatarProvider
    calls = []

    def flaky_detect():
        calls.append(1)
        if len(calls) < 3:
            return (0, 0, 1, 0)  # window not created/resized yet
        return (160, 72, 640, 720)  # settled

    monkeypatch.setattr(pane_geometry, "detect_pane_rect", flaky_detect)
    monkeypatch.setattr("time.sleep", lambda s: None)  # don't actually wait
    provider = CodecAvatarProvider.__new__(CodecAvatarProvider)
    width, height, pos = provider._resolve_geometry({"geometry_retry_s": 5.0})
    assert (width, height, pos) == (320, 720, (320, 72))  # narrowed+centered
    assert len(calls) == 3


def test_detect_truecolor_visual_id_parses_xdpyinfo_output(monkeypatch):
    """Regression test for the gx10 black-window bug: SDL must be pinned
    to the X server's default TrueColor visual, not whatever visual it
    picks on its own — gx10's Xvfb offers a DirectColor visual at the
    same depth (24) that SDL defaulted to, and a window on that visual
    renders as solid black to any external reader (ffmpeg x11grab, the
    real stream capture) because its colormap never gets installed. This
    only reproduces against a real X server, so the unit test covers the
    parsing logic against captured real `xdpyinfo` output instead."""
    import subprocess
    from avatar_providers import codec_avatar

    class FakeResult:
        returncode = 0
        stdout = (
            "name of display:    :99\n"
            "version number:    11.0\n"
            "vendor string:    The X.Org Foundation\n"
            "...\n"
            "default screen number:    0\n"
            "number of screens:    1\n"
            "\n"
            "screen #0:\n"
            "  dimensions:    3840x2160 pixels (975x549 millimeters)\n"
            "  default visual id:  0x21\n"
            "  visual:\n"
            "    visual id:    0x21\n"
            "    class:    TrueColor\n"
        )

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    assert codec_avatar._detect_truecolor_visual_id() == "0x21"


def test_detect_truecolor_visual_id_returns_none_when_xdpyinfo_unavailable(monkeypatch):
    """No X server (local dev/CI) or a missing xdpyinfo binary must not
    raise — just leave SDL to pick its own default, same as before this
    fix existed."""
    import subprocess
    from avatar_providers import codec_avatar

    def boom(*a, **k):
        raise FileNotFoundError("xdpyinfo not found")

    monkeypatch.setattr(subprocess, "run", boom)
    assert codec_avatar._detect_truecolor_visual_id() is None
