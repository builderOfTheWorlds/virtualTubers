#!/usr/bin/env python3
"""
tests/test_character_schema.py
Covers the slider schema an AI agent drives (app/character_schema.py).
Complexity-scaled per CLAUDE.md: normalize_params/resolve_params carry the
real branching (clamping, strict vs lenient, preset layering), so they get
parametrized coverage; the thin wrappers get one case each.
"""
import pytest

from character_schema import (
    ACCENT_COLORS,
    ENUM_DEFAULTS,
    PARAM_DEFAULTS,
    PRESETS,
    SLIDER_DEFAULTS,
    CharacterParamError,
    load_preset,
    normalize_params,
    resolve_params,
)


def test_normalize_params_empty_returns_all_defaults():
    assert normalize_params({}) == PARAM_DEFAULTS


def test_normalize_params_fills_missing_sliders():
    result = normalize_params({"eye_size": 0.9})
    assert result["eye_size"] == 0.9
    assert result["jaw_width"] == SLIDER_DEFAULTS["jaw_width"]
    assert set(result) == set(PARAM_DEFAULTS)


def test_normalize_params_does_not_mutate_input():
    raw = {"eye_size": 0.9}
    normalize_params(raw)
    assert raw == {"eye_size": 0.9}


@pytest.mark.parametrize("value,expected", [
    (1.5, 1.0),
    (-0.5, 0.0),
    (99, 1.0),
    (0.0, 0.0),
    (1.0, 1.0),
    (0.42, 0.42),
    ("0.3", 0.3),   # YAML/JSON round-trips can hand us a string
])
def test_normalize_params_clamps_sliders(value, expected):
    assert normalize_params({"eye_size": value})["eye_size"] == pytest.approx(expected)


def test_normalize_params_rejects_nan():
    # NaN must not slip through: min/max propagate it, and a NaN vertex
    # coordinate silently poisons the whole rendered mesh.
    with pytest.raises(CharacterParamError, match="NaN"):
        normalize_params({"eye_size": float("nan")})


def test_normalize_params_nan_lenient_falls_back_to_default():
    assert normalize_params({"eye_size": float("nan")}, strict=False)["eye_size"] == \
        SLIDER_DEFAULTS["eye_size"]


@pytest.mark.parametrize("bad", ["wide", None, [0.5], {}])
def test_normalize_params_rejects_non_numeric_slider(bad):
    with pytest.raises(CharacterParamError):
        normalize_params({"eye_size": bad})


def test_unknown_key_raises_in_strict_mode():
    with pytest.raises(CharacterParamError, match="unknown character parameter"):
        normalize_params({"hat_size": 0.5})


def test_unknown_key_ignored_in_lenient_mode():
    # The config path must not kill the avatar pane over a typo'd YAML key.
    result = normalize_params({"hat_size": 0.5}, strict=False)
    assert result == PARAM_DEFAULTS


def test_normalize_params_rejects_non_dict():
    with pytest.raises(CharacterParamError, match="must be a dict"):
        normalize_params([0.5, 0.5])


@pytest.mark.parametrize("color", ACCENT_COLORS)
def test_accent_color_accepts_every_palette_entry(color):
    assert normalize_params({"accent_color": color})["accent_color"] == color


def test_accent_color_is_case_insensitive():
    assert normalize_params({"accent_color": "cyan"})["accent_color"] == "CYAN"


def test_accent_color_rejects_unknown_in_strict_mode():
    with pytest.raises(CharacterParamError):
        normalize_params({"accent_color": "MAUVE"})


def test_accent_color_falls_back_in_lenient_mode():
    assert normalize_params({"accent_color": "MAUVE"}, strict=False)["accent_color"] == \
        ENUM_DEFAULTS["accent_color"]


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_is_valid_and_complete(name):
    # A malformed preset would otherwise only surface at render time.
    params = load_preset(name)
    assert set(params) == set(PARAM_DEFAULTS)
    for key in SLIDER_DEFAULTS:
        assert 0.0 <= params[key] <= 1.0


def test_every_preset_has_a_distinct_silhouette():
    """No two presets may share the same eight slider values.

    The cast is meant to be told apart by SHAPE, not by accent_color alone
    (see the comment above PRESETS). Without this, a future edit could
    quietly collapse a character back to defaults-plus-tint and nothing
    would fail until someone watched the stream.
    """
    by_sliders = {}
    for name in sorted(PRESETS):
        params = load_preset(name)
        key = tuple(round(params[s], 6) for s in sorted(SLIDER_DEFAULTS))
        by_sliders.setdefault(key, []).append(name)

    collisions = {k: v for k, v in by_sliders.items() if len(v) > 1}
    assert not collisions, f"presets share a silhouette: {list(collisions.values())}"
    assert len(by_sliders) == len(PRESETS)


def test_no_preset_is_merely_the_default_silhouette():
    """A preset equal to SLIDER_DEFAULTS is the 'defaults plus a tint'
    anti-pattern this roster was built to replace."""
    default_key = tuple(round(v, 6) for _, v in sorted(SLIDER_DEFAULTS.items()))
    for name in sorted(PRESETS):
        params = load_preset(name)
        key = tuple(round(params[s], 6) for s in sorted(SLIDER_DEFAULTS))
        assert key != default_key, f"{name} is the neutral default silhouette"


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_resolves_cleanly(name):
    """Both entry points must accept every preset without raising: the
    config path (`character_params: nyx1`) and the layering path
    (`{"preset": ..., <override>}`) an agent uses while iterating."""
    direct = resolve_params(name)
    assert direct == load_preset(name)

    layered = resolve_params({"preset": name, "eye_size": 0.5}, strict=True)
    assert set(layered) == set(PARAM_DEFAULTS)
    assert layered["eye_size"] == pytest.approx(0.5)
    assert layered["accent_color"] == direct["accent_color"]


@pytest.mark.parametrize("name,color", sorted(
    (n, p["accent_color"]) for n, p in PRESETS.items()
))
def test_preset_accent_colors_are_unique_and_valid(name, color):
    assert color in ACCENT_COLORS
    owners = [n for n, p in PRESETS.items() if p["accent_color"] == color]
    assert owners == [name], f"{color} is shared by {owners}"


def test_load_preset_rejects_unknown_name():
    with pytest.raises(CharacterParamError, match="unknown preset"):
        load_preset("nobody")


def test_resolve_params_accepts_preset_name():
    assert resolve_params("chadwick") == load_preset("chadwick")


def test_resolve_params_accepts_none():
    assert resolve_params(None) == PARAM_DEFAULTS


def test_resolve_params_layers_overrides_on_preset():
    """A preset plus explicit sliders: overrides apply, and every slider the
    caller DIDN'T mention keeps the preset's value rather than reverting to
    the neutral default."""
    base = load_preset("chadwick")
    result = resolve_params({"preset": "chadwick", "eye_size": 0.11})

    assert result["eye_size"] == pytest.approx(0.11)
    for key in PARAM_DEFAULTS:
        if key != "eye_size":
            assert result[key] == base[key], f"{key} lost its preset value"


def test_resolve_params_preset_override_clamps():
    assert resolve_params({"preset": "chadwick", "eye_size": 5.0})["eye_size"] == 1.0
