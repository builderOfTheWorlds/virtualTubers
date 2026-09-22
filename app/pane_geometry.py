#!/usr/bin/env python3
"""
pane_geometry.py
Resolves a tmux pane's actual on-screen PIXEL rectangle, so a provider that
draws outside the tmux character grid (avatar_providers/codec_avatar.py)
can position its window exactly over the pane it's replacing — without a
hardcoded pixel offset that would drift the moment the layout, screen
resolution, or font size changes.

WHY THIS IS NEEDED, NOT JUST A STATIC CONFIG VALUE. tuber_base.yaml's
avatar pane's cell position comes from tmux's PERCENTAGE splits
(config/layouts/tuber_base.yaml's split math), resolved at container
startup — not a fixed cell count. startup.sh's xterm is then resized to
fill CAPTURE_RESOLUTION exactly and tmux's cell grid recomputed to match
("xterm recomputes its cell grid to fill the window; make tmux follow the
new client size"), so the pixel size of a single cell is itself only known
at runtime (window pixel size / grid cell count), not a constant. Any
layout change, capture resolution change (docker-compose's
CAPTURE_RESOLUTION), or font size change (FONT_SIZE) shifts every pane's
pixel rect. Reading it live from tmux + xdotool at provider startup is the
only way that stays correct without hand-updating a pixel offset every
time the layout changes.

HOW. Two pieces of information, both queryable while the pane process is
actually running inside tmux (avatar.py's env always has TMUX_PANE set —
build_layout.py launches every pane via `tmux send-keys`):

1. The pane's rect in CELLS, relative to its window — `tmux display-message`
   with `#{pane_left}`/`#{pane_top}`/`#{pane_width}`/`#{pane_height}`.
2. The window's rect in PIXELS on the X display — `xdotool getwindowgeometry`
   for the xterm window running that tmux session (matched by
   `startup.sh`'s xterm PID the same way startup.sh itself does: search by
   PID, not `--class`, since xterm's WM_CLASS is "XTerm").

Cell pixel size = window pixel size / window cell size (tmux's grid is
uniform), so a pane's pixel rect = window origin + pane cell offset * cell
pixel size.

Best-effort throughout: every step can fail (no tmux, no X display, no
xdotool, not running inside a pane at all — e.g. character_preview.py's
CLI, or a unit test) and returns None rather than raising, so a caller
should always have an explicit fallback (a configured `window_pos`) for
when detection isn't possible.
"""
import logging
import os
import subprocess

log = logging.getLogger(__name__)


def _run(cmd, timeout=3, env=None):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, env=env)
        if result.returncode != 0:
            log.debug("pane_geometry: %r exited %d: %s", cmd, result.returncode,
                     result.stderr.strip())
            return None
        return result.stdout.strip()
    except Exception as exc:  # noqa: BLE001 — best-effort, see module docstring
        log.debug("pane_geometry: %r failed (%r)", cmd, exc)
        return None


def _tmux_pane_cell_rect(tmux_pane):
    """(pane_left, pane_top, pane_width, pane_height) in CELLS, or None."""
    out = _run(["tmux", "display-message", "-p", "-t", tmux_pane,
               "#{pane_left} #{pane_top} #{pane_width} #{pane_height}"])
    if not out:
        return None
    try:
        left, top, width, height = (int(v) for v in out.split())
        return left, top, width, height
    except ValueError:
        log.debug("pane_geometry: unparseable tmux output %r", out)
        return None


def _tmux_window_cell_size(tmux_pane):
    """(window_width, window_height) in CELLS, or None."""
    out = _run(["tmux", "display-message", "-p", "-t", tmux_pane,
               "#{window_width} #{window_height}"])
    if not out:
        return None
    try:
        width, height = (int(v) for v in out.split())
        return width, height
    except ValueError:
        return None


def _xterm_window_pixel_rect(display):
    """(x, y, width, height) in PIXELS of the xterm window on `display`, or
    None. Matches startup.sh's own approach: search by window class
    "XTerm" (xterm's actual WM_CLASS, capitalized) since a container only
    ever runs one."""
    env = dict(os.environ)
    env["DISPLAY"] = display
    wid_out = _run(["xdotool", "search", "--class", "XTerm"], env=env)
    if not wid_out:
        return None
    wid = wid_out.splitlines()[0].strip()
    if not wid:
        return None

    geom_out = _run(["xdotool", "getwindowgeometry", "--shell", wid], env=env)
    if not geom_out:
        return None
    fields = {}
    for line in geom_out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip().upper()] = value.strip()
    try:
        return (int(fields["X"]), int(fields["Y"]),
               int(fields["WIDTH"]), int(fields["HEIGHT"]))
    except (KeyError, ValueError):
        log.debug("pane_geometry: unparseable xdotool output %r", geom_out)
        return None


def detect_pane_rect(tmux_pane=None, display=None):
    """Best-effort: return (x, y, width, height) in PIXELS of the given
    tmux pane's on-screen rect, or None if any step fails.

    tmux_pane defaults to the TMUX_PANE env var (set for every process tmux
    launches — see module docstring); display defaults to the DISPLAY env
    var. Both are parameterized for testability without a real tmux/X
    session.
    """
    tmux_pane = tmux_pane or os.environ.get("TMUX_PANE")
    display = display or os.environ.get("DISPLAY")
    if not tmux_pane or not display:
        log.debug("pane_geometry: no TMUX_PANE/DISPLAY in environment "
                 "(tmux_pane=%r display=%r) — not running inside a tmux pane",
                 tmux_pane, display)
        return None

    pane_cells = _tmux_pane_cell_rect(tmux_pane)
    window_cells = _tmux_window_cell_size(tmux_pane)
    window_pixels = _xterm_window_pixel_rect(display)
    if pane_cells is None or window_cells is None or window_pixels is None:
        return None

    pane_left, pane_top, pane_w, pane_h = pane_cells
    window_cols, window_rows = window_cells
    win_x, win_y, win_w, win_h = window_pixels
    if window_cols <= 0 or window_rows <= 0:
        return None

    cell_w = win_w / window_cols
    cell_h = win_h / window_rows

    x = win_x + round(pane_left * cell_w)
    y = win_y + round(pane_top * cell_h)
    width = round(pane_w * cell_w)
    height = round(pane_h * cell_h)
    log.info("pane_geometry: detected pane rect x=%d y=%d w=%d h=%d "
            "(cell %.2fx%.2fpx)", x, y, width, height, cell_w, cell_h)
    return x, y, width, height


__all__ = ["detect_pane_rect"]
