"""
Integrity checks for config/themes/gogh_themes.json — the real 1247-scheme
Gogh dump (https://github.com/Gogh-Co/Gogh, data/themes.json), not a
fixture. app/console_theme.py's unit tests (test_console_theme.py) use a
small in-memory fixture instead and don't touch this file; these tests are
what actually verify the shipped data is usable.
"""
import re

import pytest

from console_theme import DEFAULT_THEME_NAME, load_themes

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@pytest.fixture(scope="module")
def themes():
    return load_themes()


def test_theme_count_matches_the_gogh_dump():
    """Pinned exact: a silent partial write/truncation should fail loudly
    rather than quietly shipping fewer schemes than advertised."""
    assert len(load_themes()) == 1247


def test_names_are_unique():
    names = list(load_themes().keys())
    assert len(names) == len(set(names))


def test_default_theme_name_resolves():
    """console_theme.py's DEFAULT_THEME_NAME must actually exist in the
    shipped data — if Gogh ever renames/removes it, get_theme()'s fallback
    would otherwise KeyError instead of degrading gracefully."""
    assert DEFAULT_THEME_NAME in load_themes()


def test_default_theme_matches_the_original_solarized_dark_palette():
    """Anchors this refactor to the exact colors startup.sh hardcoded
    directly before console_theme.py existed (2026-09-24 UI pass) — a
    Gogh data update must not silently shift the worker's default look."""
    theme = load_themes()[DEFAULT_THEME_NAME]
    assert theme["background"] == "#002B36"


def test_every_theme_has_16_colors_plus_bg_fg_cursor(themes):
    for name, theme in themes.items():
        assert len(theme["colors"]) == 16, name
        for key in ("background", "foreground", "cursor"):
            assert key in theme, f"{name} missing {key}"


def test_every_color_value_is_a_valid_hex_triple(themes):
    bad = []
    for name, theme in themes.items():
        values = [theme["background"], theme["foreground"], theme["cursor"], *theme["colors"]]
        if not all(HEX_RE.match(v) for v in values):
            bad.append(name)
    assert not bad, f"non-hex color values in: {bad[:10]}"
