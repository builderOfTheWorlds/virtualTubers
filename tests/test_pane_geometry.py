#!/usr/bin/env python3
"""
tests/test_pane_geometry.py
Covers app/pane_geometry.py's tmux+xdotool pane rect detection. All tmux/
xdotool subprocess calls are mocked — this suite must pass with no real
tmux session, no X display, and no xdotool binary (true on the Windows dev
box), matching the module's own "best-effort, return None" contract when
any of those aren't available.
"""
from unittest.mock import MagicMock, patch

import pane_geometry


def _run_result(stdout="", returncode=0):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


def test_detect_pane_rect_returns_none_without_tmux_pane_env(monkeypatch):
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("DISPLAY", ":99")
    assert pane_geometry.detect_pane_rect() is None


def test_detect_pane_rect_returns_none_without_display_env(monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%3")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert pane_geometry.detect_pane_rect() is None


def test_detect_pane_rect_computes_pixel_offset_from_cell_geometry():
    """The core math: a pane at cell (10,2) sized 40x20, inside a
    120x30-cell window that itself sits at pixel (0,0) sized 1920x1080 (so
    each cell is 16x36px) should land at pixel (160, 72), sized 640x720."""
    def fake_run(cmd, capture_output, text, timeout):
        joined = " ".join(cmd)
        if "pane_left" in joined:
            return _run_result("10 2 40 20\n")
        if "window_width" in joined:
            return _run_result("120 30\n")
        if "search" in joined:
            return _run_result("12345\n")
        if "getwindowgeometry" in joined:
            return _run_result(
                "WINDOW=12345\nX=0\nY=0\nWIDTH=1920\nHEIGHT=1080\n")
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        rect = pane_geometry.detect_pane_rect(tmux_pane="%3", display=":99")

    assert rect == (160, 72, 640, 720)


def test_detect_pane_rect_returns_none_when_tmux_display_message_fails():
    def fake_run(cmd, capture_output, text, timeout):
        return _run_result(returncode=1)

    with patch("subprocess.run", side_effect=fake_run):
        assert pane_geometry.detect_pane_rect(tmux_pane="%3", display=":99") is None


def test_detect_pane_rect_returns_none_when_xdotool_search_finds_nothing():
    def fake_run(cmd, capture_output, text, timeout):
        joined = " ".join(cmd)
        if "pane_left" in joined:
            return _run_result("0 0 40 20\n")
        if "window_width" in joined:
            return _run_result("120 30\n")
        if "search" in joined:
            return _run_result("")  # no xterm window found
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        assert pane_geometry.detect_pane_rect(tmux_pane="%3", display=":99") is None


def test_detect_pane_rect_returns_none_on_malformed_tmux_output():
    def fake_run(cmd, capture_output, text, timeout):
        joined = " ".join(cmd)
        if "pane_left" in joined:
            return _run_result("not-a-number 0 40 20\n")
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        assert pane_geometry.detect_pane_rect(tmux_pane="%3", display=":99") is None


def test_detect_pane_rect_returns_none_when_subprocess_raises():
    """A missing binary (FileNotFoundError) or a hang (TimeoutExpired) must
    degrade to None, not propagate — this runs on every provider startup
    and must never crash the avatar pane over a tooling absence."""
    with patch("subprocess.run", side_effect=FileNotFoundError("no tmux")):
        assert pane_geometry.detect_pane_rect(tmux_pane="%3", display=":99") is None


def test_detect_pane_rect_handles_zero_size_window_gracefully():
    def fake_run(cmd, capture_output, text, timeout):
        joined = " ".join(cmd)
        if "pane_left" in joined:
            return _run_result("0 0 40 20\n")
        if "window_width" in joined:
            return _run_result("0 0\n")  # degenerate
        if "search" in joined:
            return _run_result("12345\n")
        if "getwindowgeometry" in joined:
            return _run_result("WINDOW=12345\nX=0\nY=0\nWIDTH=1920\nHEIGHT=1080\n")
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        assert pane_geometry.detect_pane_rect(tmux_pane="%3", display=":99") is None
