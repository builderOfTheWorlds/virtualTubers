#!/usr/bin/env python3
"""
tests/test_pixel_raster_background.py
Covers pixel_raster.composite_on_background / parse_background — the step
that makes the avatar's X window blend into the console instead of
punching a black rectangle through the tmux layout (2026-09-22 UI fix).

Pure numpy, no display and no GL: the compositing happens after rendering,
on a plain (H,W,3) array, so it's fully testable on the Windows dev box.
"""
import numpy as np
import pytest

from pixel_raster import (CONSOLE_BG, composite_on_background,
                          parse_background)


# ── composite_on_background ───────────────────────────────────────────────
def test_undrawn_black_pixels_become_exactly_the_background():
    """The whole point: un-drawn (black) surround must land on the console
    grey EXACTLY, or the window edge stays visible as a seam."""
    frame = np.zeros((4, 5, 3), dtype=np.float32)
    out = composite_on_background(frame, CONSOLE_BG)
    assert np.allclose(out, CONSOLE_BG[None, None, :], atol=1e-6)


def test_white_pixels_stay_white():
    """A screen blend must not wash out the bright end of the face."""
    frame = np.ones((3, 3, 3), dtype=np.float32)
    out = composite_on_background(frame, CONSOLE_BG)
    assert np.allclose(out, 1.0, atol=1e-6)


def test_bright_face_pixels_are_barely_shifted():
    """Mid/high-intensity face pixels should be essentially untouched —
    this is a background fix, not a recolor of the character."""
    frame = np.full((2, 2, 3), 0.85, dtype=np.float32)
    out = composite_on_background(frame, CONSOLE_BG)
    assert np.all(out >= 0.85)          # screen only ever brightens
    assert np.all(out - 0.85 < 0.03)    # ...and barely, up here


def test_output_stays_in_unit_range_and_keeps_shape():
    rng = np.random.default_rng(0)
    frame = rng.random((6, 7, 3)).astype(np.float32)
    out = composite_on_background(frame, CONSOLE_BG)
    assert out.shape == frame.shape
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_none_background_is_a_passthrough():
    """`background: none` must preserve the original black-surround look
    byte for byte, not approximate it."""
    rng = np.random.default_rng(1)
    frame = rng.random((4, 4, 3)).astype(np.float32)
    assert np.array_equal(composite_on_background(frame, None), frame)


def test_gradient_falls_off_smoothly_into_the_background():
    """No hard edge: a dark-to-light ramp (the CRT bloom halo) must stay
    monotonic after compositing rather than being clipped at a threshold,
    which is what a naive black-is-transparent key would do."""
    ramp = np.linspace(0.0, 1.0, 64, dtype=np.float32)
    frame = np.repeat(ramp[None, :, None], 3, axis=2)[None, :, :, :][0]
    out = composite_on_background(frame, CONSOLE_BG)
    channel = out[:, 0]
    assert np.all(np.diff(channel) >= -1e-6)


# ── parse_background ──────────────────────────────────────────────────────
def test_parse_none_returns_the_console_default():
    assert np.allclose(parse_background(None), CONSOLE_BG)


@pytest.mark.parametrize("value", ["#2b2b2b", "2b2b2b", "#2B2B2B"])
def test_parse_hex_forms_all_match_the_console_grey(value):
    assert np.allclose(parse_background(value), CONSOLE_BG, atol=1e-6)


def test_parse_shorthand_hex_expands():
    assert np.allclose(parse_background("#fff"), np.ones(3), atol=1e-6)


@pytest.mark.parametrize("value", ["none", "off", "TRANSPARENT", " none "])
def test_parse_disable_keywords_return_none(value):
    assert parse_background(value) is None


def test_parse_black_keyword_is_black_not_disabled():
    """'black' composites onto black (a no-op visually) — distinct from
    'none', which skips compositing entirely. Both look the same on
    screen; keeping them separate keeps the config honest."""
    result = parse_background("black")
    assert result is not None
    assert np.allclose(result, 0.0)


def test_parse_float_triple_passes_through():
    assert np.allclose(parse_background([0.1, 0.2, 0.3]), [0.1, 0.2, 0.3])


def test_parse_byte_triple_is_scaled_to_unit_range():
    assert np.allclose(parse_background([43, 43, 43]), CONSOLE_BG, atol=1e-6)


@pytest.mark.parametrize("value", ["not-a-color", "#12345", [1, 2], {}, "#xyzxyz"])
def test_parse_garbage_falls_back_to_the_default_instead_of_raising(value):
    """A cosmetic setting must never kill the avatar pane."""
    assert np.allclose(parse_background(value), CONSOLE_BG)
