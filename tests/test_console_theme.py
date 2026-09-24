"""
Tests for app/console_theme.py.
redis.Redis is mocked (ConsoleThemeControl tests) — no real Redis needed.
Theme lookups use a small in-memory fixture, not the real 1247-entry Gogh
dump, so these stay fast and independent of that file's exact contents;
test_console_theme_data.py covers the real file's integrity separately.
"""
from unittest.mock import MagicMock, patch

import pytest
import redis

from console_theme import (ConsoleThemeControl, DEFAULT_THEME_NAME,
                            get_theme, osc_sequence, resolve_active_theme_name,
                            resolve_config_theme_name, resolve_redis_url,
                            theme_exists, xterm_xrm_pairs, xterm_xrm_shell_args)

FIXTURE_THEMES = {
    "Alpha": {
        "name": "Alpha",
        "background": "#000000",
        "foreground": "#ffffff",
        "cursor": "#ffffff",
        "colors": [f"#{i:02x}0000" for i in range(16)],
    },
    "Beta": {
        "name": "Beta",
        "background": "#111111",
        "foreground": "#eeeeee",
        "cursor": "#eeeeee",
        "colors": [f"#00{i:02x}00" for i in range(16)],
    },
}


# ── get_theme / theme_exists ─────────────────────────────────────────────────
def test_get_theme_exact_match():
    assert get_theme("Alpha", FIXTURE_THEMES, default="Alpha")["name"] == "Alpha"


def test_get_theme_case_insensitive():
    assert get_theme("alpha", FIXTURE_THEMES, default="Alpha")["name"] == "Alpha"


def test_get_theme_unknown_falls_back_to_default():
    assert get_theme("Nonexistent", FIXTURE_THEMES, default="Beta")["name"] == "Beta"


def test_get_theme_none_falls_back_to_default():
    assert get_theme(None, FIXTURE_THEMES, default="Beta")["name"] == "Beta"


def test_theme_exists_true_for_known_name_any_case():
    assert theme_exists("BETA", FIXTURE_THEMES) is True


def test_theme_exists_false_for_unknown_name():
    assert theme_exists("Gamma", FIXTURE_THEMES) is False


def test_theme_exists_false_for_none():
    assert theme_exists(None, FIXTURE_THEMES) is False


# ── resolve_config_theme_name ────────────────────────────────────────────────
def test_resolve_config_theme_name_present():
    assert resolve_config_theme_name({"console": {"theme": "Beta"}}) == "Beta"


def test_resolve_config_theme_name_missing_block():
    assert resolve_config_theme_name({}) is None


def test_resolve_config_theme_name_none_config():
    assert resolve_config_theme_name(None) is None


# ── resolve_redis_url (same shape as worker_control's) ───────────────────────
def test_resolve_redis_url_env_overrides_config():
    assert resolve_redis_url(
        config={"world_state": {"redis_url": "redis://config:6379"}},
        env_name="__NOT_SET__") == "redis://config:6379"


def test_resolve_redis_url_default_when_nothing_set():
    assert resolve_redis_url(config=None, env_name="__NOT_SET__") == "redis://redis:6379"


# ── ConsoleThemeControl ───────────────────────────────────────────────────────
def _control_with_fake_client():
    fake_client = MagicMock()
    with patch("console_theme.redis.Redis.from_url", return_value=fake_client):
        control = ConsoleThemeControl("redis://fake:6379")
    return control, fake_client


def test_get_theme_name_returns_default_when_key_missing():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    assert control.get_theme_name("coder", default="Fallback") == "Fallback"


def test_get_theme_name_returns_stored_value():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = "Dracula"
    assert control.get_theme_name("coder") == "Dracula"


def test_get_theme_name_fails_open_on_redis_error():
    control, fake_client = _control_with_fake_client()
    fake_client.get.side_effect = redis.RedisError("connection refused")
    assert control.get_theme_name("coder", default="Fallback") == "Fallback"


def test_set_theme_name_writes_expected_key_and_value():
    control, fake_client = _control_with_fake_client()
    control.set_theme_name("coder", "Dracula")
    fake_client.set.assert_called_once_with("console:coder:theme", "Dracula")


def test_set_theme_name_raises_on_redis_error():
    control, fake_client = _control_with_fake_client()
    fake_client.set.side_effect = redis.RedisError("connection refused")
    with pytest.raises(redis.RedisError):
        control.set_theme_name("coder", "Dracula")


def test_clear_theme_name_deletes_key():
    control, fake_client = _control_with_fake_client()
    control.clear_theme_name("coder")
    fake_client.delete.assert_called_once_with("console:coder:theme")


# ── resolve_active_theme_name precedence ─────────────────────────────────────
def test_precedence_redis_override_wins():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = "Redis Theme"
    name = resolve_active_theme_name(
        "coder", config={"console": {"theme": "Config Theme"}}, control=control)
    assert name == "Redis Theme"


def test_precedence_config_wins_when_no_redis_override():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    name = resolve_active_theme_name(
        "coder", config={"console": {"theme": "Config Theme"}}, control=control)
    assert name == "Config Theme"


def test_precedence_default_when_nothing_set():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    name = resolve_active_theme_name("coder", config={}, control=control)
    assert name == DEFAULT_THEME_NAME


def test_no_control_skips_redis_tier_entirely():
    """control=None (startup.sh's one-shot boot path) must not attempt a
    Redis connection — it goes straight to config, then the default."""
    name = resolve_active_theme_name(
        "coder", config={"console": {"theme": "Config Theme"}}, control=None)
    assert name == "Config Theme"


# ── xterm_xrm_pairs / xterm_xrm_shell_args ───────────────────────────────────
def test_xterm_xrm_pairs_covers_background_foreground_cursor_and_16_colors():
    pairs = xterm_xrm_pairs(FIXTURE_THEMES["Alpha"])
    keys = [k for k, _ in pairs]
    assert keys[:3] == ["XTerm*background", "XTerm*foreground", "XTerm*cursorColor"]
    assert keys[3:] == [f"XTerm*color{i}" for i in range(16)]
    assert len(pairs) == 19


def test_xterm_xrm_shell_args_are_individually_shell_quoted():
    out = xterm_xrm_shell_args(FIXTURE_THEMES["Alpha"])
    assert "-xrm 'XTerm*background: #000000'" in out
    assert "-xrm 'XTerm*color0: #000000'" in out
    assert "-xrm 'XTerm*color15: #0f0000'" in out


# ── osc_sequence ──────────────────────────────────────────────────────────────
def test_osc_sequence_sets_fg_bg_cursor_and_16_indexed_colors():
    seq = osc_sequence(FIXTURE_THEMES["Alpha"])
    assert "\x1b]10;#ffffff\x1b\\" in seq   # foreground
    assert "\x1b]11;#000000\x1b\\" in seq   # background
    assert "\x1b]12;#ffffff\x1b\\" in seq   # cursor
    assert "\x1b]4;0;#000000\x1b\\" in seq
    assert "\x1b]4;15;#0f0000\x1b\\" in seq
