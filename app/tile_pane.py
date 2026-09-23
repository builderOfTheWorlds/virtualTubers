#!/usr/bin/env python3
"""
tile_pane.py
Long-lived tmux pane program for ONE roundtable tile (design spec
.claude/prompts/roundtable_stream_design.md §5, WP-3).

The roundtable channel is hosted by the GM container (slot `tuber_0`) and
shows one tile per character slot. A tile is a self-contained mini-stage
for exactly one slot: it performs that slot's scenes, plays audio ONLY for
the scenes that slot owns, paces visually to the other slots' timing, and
drives a small per-tile avatar.

This is deliberately the SAME rendering contract the duet follower already
implements (app/replay_pane.py perform_follower_request) — one code path,
two uses:

  * external follower  — invited over Kafka, cued by `replay_cue` messages;
  * local tile (here)  — invited by a request FILE the director drops in
                         the relay dir, cued by a per-slot cue FILE the
                         director's `Performer.on_scene_start` writes
                         (§4). No Kafka round-trip for the GM's own tiles,
                         and a tile NEVER consumes the bus.

Everything reusable is imported rather than copied: `replay_pane.
_rebuild_scenes_from_rows` (the owns predicate contract),
`replay.Performer` (the render loop), `narration_store.load_airing` (the
airing the director already persisted), `agent_state.write_state` (atomic
per-tile state). A tile NEVER generates fresh narration — the director
prepared it, we only play it back.

Relay files, all per slot inside --relay-dir (default /tmp/tiles):

    <relay>/<slot>.request.json   director -> tile: perform this airing
    <relay>/<slot>.cue.json       director -> tile: the cue ratchet
    <relay>/<slot>.state.json     tile -> itself:   avatar state (§5.1)

Why the tile owns its own avatar render and state path (§5.1 / review
finding G2): `app/avatar.py` resolves its state path via
`agent_state.resolve_state_path` (AGENT_STATE_FILE env -> config ->
/tmp/agent_state.json) and takes only `--config`, so seven instances in
one container would all read ONE file and render seven identical faces.
`avatar.py` is placeholder-grade and slated for replacement, so it is NOT
patched here; instead every tile writes its own `<slot>.state.json` and
renders a deliberately SMALL face (a tile gets roughly 15-16 terminal
rows, §5.1 constraint 2 — not the full-pane ASCII art).

Failure policy, same as every other pane in this repo: everything
degrades, nothing raises out of the pane loop. A missing airing, an
unreachable narration store, a malformed request — each prints to stderr
and returns the tile to its idle screen. The roundtable channel must
never go blank (§5), so one bad show can never kill a tile.

stdout IS the display (this runs as a tmux pane captured by ffmpeg);
diagnostics go to stderr.
"""
import argparse
import os
import re
import sys
import tempfile
import textwrap
import threading
import time
from collections import deque
from pathlib import Path

import narration_store
import replay_pane
from agent_state import read_state, write_state
from replay import Pacer, Palette, Performer
from tile_avatar import (TILE_AVATAR_FPS, avatar_subpanel_rows,
                         detect_tile_pane_rect, make_tile_avatar,
                         resolve_slot_character_params)

DEFAULT_RELAY_DIR = "/tmp/tiles"
DEFAULT_WORKER_CONFIG = replay_pane.DEFAULT_WORKER_CONFIG
TILE_POLL_INTERVAL_S = 1.0
TILE_IDLE_REDRAW_S = 30.0
# How long the finished frame stays up before the tile returns to idle.
TILE_HOLD_FINAL_FRAME_S = 3.0

# The cue-protocol timeouts are NOT redefined here. They live in
# replay_pane (REPLAY_CUE_POLL_INTERVAL_S / REPLAY_FIRST_CUE_TIMEOUT_S /
# REPLAY_WATCHDOG_GRACE_S / REPLAY_WATCHDOG_MIN_S) and are read through the
# module at call time, so the local tile ratchet and the external follower
# ratchet can never drift apart — and so a test (or a future tuning pass)
# that adjusts them adjusts both.


# ── relay paths ──────────────────────────────────────────────────────────────
def resolve_relay_dir(relay_dir=None):
    """--relay-dir > TILE_RELAY_DIR env > /tmp/tiles."""
    return str(relay_dir or os.environ.get("TILE_RELAY_DIR") or DEFAULT_RELAY_DIR)


def tile_request_file(relay_dir, slot):
    return str(Path(relay_dir) / f"{slot}.request.json")


def tile_cue_file(relay_dir, slot):
    return str(Path(relay_dir) / f"{slot}.cue.json")


def tile_state_file(relay_dir, slot):
    """The per-tile avatar state path (§5.1). Per SLOT on purpose: this is
    the G2 regression guard — seven tiles in one container must never share
    one state file the way `avatar.py`'s resolve_state_path would force."""
    return str(Path(relay_dir) / f"{slot}.state.json")


def ensure_relay_dir(relay_dir):
    """Best-effort — a tile that can't create its relay dir still idles
    (and says so) rather than crashing the pane."""
    try:
        Path(relay_dir).mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        print(f"[tile_pane] relay dir {relay_dir} unusable: {exc}", file=sys.stderr)
        return False


# ── per-tile avatar (small, placeholder-grade by design — §5.1) ──────────────
# Three faces is the whole vocabulary: what a tile needs is "is this slot
# talking right now", not expressiveness. Kept tiny deliberately: the tile
# is the seam the real avatar work lands in, and a big render here would
# only be thrown away (§5.1, §12 deferred).
#
# v1.4: three ROWS per expression (was two) — the design ask for an "avatar
# subpanel" of about 3 lines, and enough vertical room to read as a face
# rather than a glyph pair once it's boxed off from the text/status
# subpanels below it.
TILE_FACES = {
    "speaking": ["  ___  ", " ( ^_^ )", "  \\___/ "],
    "listening": ["  ___  ", " ( ._. )", "  \\___/ "],
    "idle": ["  ___  ", " ( -_- )", "  \\___/ "],
    # The expressions replay.Performer._avatar actually writes while a show
    # runs. Without entries here every non-speaking moment fell back to the
    # `idle` face, so a tile looked asleep for most of the broadcast.
    "thinking": ["  ___  ", " ( o_o )", "  \\~~~/ "],
    "focused": ["  ___  ", " ( >_< )", "  \\___/ "],
    "frustrated": ["  ___  ", " ( x_x )", "  /~~~\\ "],
}

# The three subpanels the design calls for, top to bottom: AVATAR, then TEXT
# (the last spoken lines), then STATUS. Each is bounded by its own "├──┤"
# divider so the tile visibly reads as three sections, not one undifferentiated
# box — that's the whole point of splitting them out.
#
# There is deliberately NO separate name row inside the frame: tmux's own
# pane-border-format already prints the resolved character name on the
# pane's top border (build_layout.py's _resolve_tile_title -> `-T`), so a
# second name repeated one line below it inside the box (the raw slot id,
# e.g. "tuber_1") was pure duplication — confirmed redundant on the live
# broadcast frame once the border already showed "Chadwick".
TILE_AVATAR_LINES = 3    # fixed — "an OK height for now" per the design ask
TILE_STATUS_LINES = 1    # fixed — one line is enough for "status: speaking"

# ── the 3D head, and the two avatar modes a tile has ─────────────────────────
# A tile draws its AVATAR subpanel one of two ways, and everything below
# that reads a row count has to agree on which:
#
#   ASCII mode (the default, and the only mode before the 3D head existed):
#       TILE_AVATAR_LINES rows of TILE_FACES art, drawn as text.
#   PIXEL mode: a real 3D head rendered into a small borderless window
#       parked over this tile by app/tile_avatar.py. ffmpeg captures the X
#       DISPLAY rather than tmux's character grid, so that window lands in
#       the stream exactly as if it had been drawn into the pane — but only
#       if the pane leaves it somewhere to land. So in PIXEL mode the
#       AVATAR subpanel prints BLANK rows: the window sits over empty
#       console instead of over an ASCII face showing through behind it.
#
# The mode is process-wide, not per-call: one tile process owns at most one
# head window, and every render inside it must reserve the same band or the
# dialogue would jump rows between frames. It is held in a module-level
# holder rather than threaded through render_tile's signature because
# render_tile has many callers (TileRenderer.draw, draw_idle_screen, tests)
# and none of them should have to know about a window.
_ACTIVE_TILE_AVATAR = None

# The expression most recently RENDERED, so the background ticker thread
# animates the same face the text frame is showing. Written by render_tile —
# whatever chooses the TILE_FACES entry is by definition the tile's current
# expression, so reading it here can never drift from what is on screen.
_CURRENT_EXPRESSION = "idle"


def set_active_tile_avatar(avatar):
    """Install (or clear, with None) this process's live 3D head. Only a
    head reporting `.active` puts the tile into PIXEL mode — a TileAvatar
    that failed to construct or has since failed leaves the tile drawing the
    ASCII face, which is the whole point of it being an upgrade rather than
    a dependency."""
    global _ACTIVE_TILE_AVATAR
    _ACTIVE_TILE_AVATAR = avatar
    return avatar


def active_tile_avatar():
    """The installed 3D head, or None. Returns None once a head has failed,
    so a single check answers 'is this tile in PIXEL mode'."""
    avatar = _ACTIVE_TILE_AVATAR
    if avatar is not None and getattr(avatar, "active", False):
        return avatar
    return None


def resolve_avatar_row_count(height=None):
    """How many rows the AVATAR subpanel occupies RIGHT NOW.

    ASCII mode: TILE_AVATAR_LINES, unchanged from before the head existed.
    PIXEL mode: tile_avatar.avatar_subpanel_rows(), the character-grid twin
    of the pixel band tile_avatar_rect reserves — so the blank band the tile
    prints is exactly as tall as the window covering it, rather than the 3
    rows an ASCII face happens to need.
    """
    if active_tile_avatar() is None:
        return TILE_AVATAR_LINES
    h = height if height is not None else resolve_tile_height()
    return avatar_subpanel_rows(h)

# The TEXT subpanel is the one that's supposed to "take up the rest of the
# tuber panel" — sized from the pane's DETECTED height every render, not
# hardcoded, the same principle resolve_tile_width already uses for width.
# MIN_DIALOGUE_LINES is the floor so a tiny/undetected pane still shows
# something rather than an empty text subpanel.
MIN_DIALOGUE_LINES = 2
# Fixed overhead OUTSIDE the text subpanel, in rows: top border + avatar (3)
# + avatar/text divider + text/status divider + status (1) + bottom border
# = 2 + TILE_AVATAR_LINES + 1 + 1 + TILE_STATUS_LINES.
#
# This constant is the ASCII-mode value, and is kept because callers and
# tests reference it as "the tile's non-dialogue overhead". Live rendering
# does NOT use it: with a 3D head the avatar band is ~18 rows rather than 3,
# and a dialogue count computed from a fixed 3 would produce a frame six
# rows taller than its pane — which scrolls the tile and pushes the avatar
# off the top, the exact failure resolve_tile_height() exists to prevent.
# tile_fixed_overhead() below is the live one; it takes the avatar row count
# actually in use.
TILE_FIXED_OVERHEAD = 2 + TILE_AVATAR_LINES + 1 + 1 + TILE_STATUS_LINES


def tile_fixed_overhead(avatar_rows=None):
    """Non-dialogue rows in a tile frame, for the given avatar band height.
    Defaults to whichever mode this process is actually in."""
    rows = avatar_rows if avatar_rows is not None else resolve_avatar_row_count()
    return 2 + rows + 1 + 1 + TILE_STATUS_LINES

# Kept for backward compatibility with callers/tests that reference a static
# "how many dialogue lines" constant; live rendering computes this per-frame
# from the pane's real detected height via resolve_dialogue_line_count().
TILE_DIALOGUE_LINES = MIN_DIALOGUE_LINES

# Redraw cadence for a line that is still being typed out. The Performer types
# character by character; redrawing the whole frame per character would repaint
# the pane thousands of times a scene, so partial lines refresh on a timer and
# every COMPLETED line redraws immediately.
TILE_PARTIAL_REDRAW_S = 0.15

# ── post-line fade (dim after speaking) ──────────────────────────────────────
# Design ask: a tile's dialogue/frame reads at full brightness while its line
# is being spoken, then fades DOWN to 50% brightness over the following 5
# seconds once the line ends (starting immediately, not after a delay) — a
# passive "who's not talking right now" cue so a viewer's eye is drawn to
# whichever tile just lit up. Never fades below the floor (a tile that's been
# quiet for a while stays legible, not black).
TILE_FADE_DURATION_S = 5.0
TILE_FADE_FLOOR = 0.5
# The tile's own accent color, matching config/panels/tile.yaml's static
# `border_color: colour117` (xterm 256-color cube index 117 = RGB
# 135,215,255, a light sky-blue) — kept in sync by hand since tmux's palette
# and this file's truecolor override are two different color systems with no
# shared source of truth. Only used once a fade has actually started (see
# render_tile's brightness param); at brightness==1.0 a tile emits NO SGR of
# its own and the pane renders exactly as before (tmux's own default pane
# color), so a tile that never speaks is completely unaffected by this
# feature.
TILE_ACTIVE_RGB = (135, 215, 255)


def _tile_color_code(brightness):
    """24-bit truecolor SGR foreground code for TILE_ACTIVE_RGB scaled to
    `brightness` (1.0 = full color, TILE_FADE_FLOOR = dimmest). Clamped so a
    caller can pass any raw elapsed-time-derived fraction without producing
    negative/zero-black or over-bright output."""
    brightness = max(TILE_FADE_FLOOR, min(1.0, brightness))
    r, g, b = (max(0, min(255, int(round(c * brightness)))) for c in TILE_ACTIVE_RGB)
    return f"\x1b[38;2;{r};{g};{b}m"

# The Performer emits ANSI color even with a disabled palette upstream of us
# (prefixes, the odd literal); a tile re-renders into a fixed-width box, so all
# escape sequences are stripped before the text is measured and clipped.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Tile geometry is DETECTED AT RENDER TIME, not assumed.
#
# Hard-won: an early version hardcoded a width taken from a geometry spike run
# at build_layout.py's 240x67 default. The REAL grid is what startup.sh's xterm
# produces — at CAPTURE_RESOLUTION=1920x1080 with FONT_SIZE=7 the monospace cell
# is exactly 6x12 px, so the grid is 320x90 characters and a roundtable tile
# (an even 4x2 grid) is 80 columns x 45 rows, i.e. 480x540 pixels. The spike's
# assumed width was wrong in the same direction either way: every rendered line
# wrapped, doubling an 8-line frame to ~16 in an 11-row pane, which scrolled the
# avatar's face off the top. The pane looked broken on the live broadcast while
# `tmux list-panes` looked fine. The lesson outlives the numbers: the only grid
# that matters is the one the pane's own pty reports, and it changes with
# CAPTURE_RESOLUTION, FONT_SIZE and the layout's split percentages alike.
#
# Each tmux pane is its own pty, so os.get_terminal_size() inside a tile returns
# THAT pane's real size. Resolution order, first hit wins:
#   1. $TILE_WIDTH  — explicit operator override
#   2. the pane's detected width (minus a safety column)
#   3. FALLBACK_TILE_WIDTH — only when detection fails (not a tty, e.g. tests)
# Always a little under the true width: an overflowing pane corrupts its
# neighbour's border on air, which is far more visible than a narrow tile.
# 78 is that deliberate shortfall against the real 80-column roundtable tile
# (320-col grid / 4 columns) — two columns of slack absorbs a border cell and
# an off-by-one without ever pushing a glyph into the next pane.
FALLBACK_TILE_WIDTH = 78
MIN_TILE_WIDTH = 18

# Same story for HEIGHT (v1.4, TILE_HEIGHT / resolve_tile_height): the text
# subpanel needs to know how many rows it actually has to fill. 44 is one row
# under the real 45-row tile (90-row grid / 2 rows) for the same reason — a
# frame one row too tall scrolls the pane, which pushes the avatar off the top
# exactly like the wrapping bug above did.
FALLBACK_TILE_HEIGHT = 44
MIN_TILE_HEIGHT = TILE_FIXED_OVERHEAD + MIN_DIALOGUE_LINES


def resolve_tile_width():
    """The width to render this tile at, detected fresh each call so a tmux
    resize (startup.sh resizes the xterm after the layout is built) is picked
    up without restarting the pane."""
    override = os.environ.get("TILE_WIDTH")
    if override:
        try:
            return max(MIN_TILE_WIDTH, int(override))
        except (TypeError, ValueError):
            pass
    try:
        # -1 keeps a spare column so a full-width line can never wrap.
        detected = os.get_terminal_size(sys.stdout.fileno()).columns - 1
        if detected >= MIN_TILE_WIDTH:
            return detected
    except (OSError, ValueError, AttributeError):
        pass
    return FALLBACK_TILE_WIDTH


def resolve_tile_height():
    """The height to render this tile at, detected fresh each call — mirrors
    resolve_tile_width. This is what lets the text subpanel actually "take up
    the rest of the tuber panel" instead of a hardcoded row count: a wider
    pane on a different DISPLAY_NUM/font-size combination gets a bigger text
    area automatically."""
    override = os.environ.get("TILE_HEIGHT")
    if override:
        try:
            return max(MIN_TILE_HEIGHT, int(override))
        except (TypeError, ValueError):
            pass
    try:
        detected = os.get_terminal_size(sys.stdout.fileno()).lines
        if detected >= MIN_TILE_HEIGHT:
            return detected
    except (OSError, ValueError, AttributeError):
        pass
    return FALLBACK_TILE_HEIGHT


def resolve_dialogue_line_count(height=None, avatar_rows=None):
    """How many rows the TEXT subpanel gets: whatever is left in the tile
    after the avatar/status/border/divider overhead, floored at
    MIN_DIALOGUE_LINES so a very short pane still shows something.

    `avatar_rows` is the AVATAR subpanel's height in rows; it defaults to
    whichever mode the tile is in (3 for the ASCII face, ~18 for a reserved
    3D-head band). Passing it explicitly keeps one frame's arithmetic
    internally consistent even if the head fails between two reads.
    """
    h = height if height is not None else resolve_tile_height()
    rows = avatar_rows if avatar_rows is not None else resolve_avatar_row_count(h)
    return max(MIN_DIALOGUE_LINES, h - tile_fixed_overhead(rows))


# Kept for callers/tests that want a static default; live rendering uses
# resolve_tile_width() so it tracks the real pane.
TILE_WIDTH = FALLBACK_TILE_WIDTH
TILE_LINE_CHARS = TILE_WIDTH - 4


def write_tile_state(state_path, expression, action="", bubble=None):
    """Per-tile avatar state, written through agent_state.write_state (already
    atomic: temp file + os.replace). Best-effort — a read-only /tmp must not
    take a tile down."""
    if not state_path:
        return None
    try:
        return write_state(state_path, expression, action=action, bubble=bubble)
    except OSError as exc:
        print(f"[tile_pane] avatar state update skipped: {exc}", file=sys.stderr)
        return None


def _clip(text, width):
    text = " ".join(_ANSI_RE.sub("", str(text or "")).split())
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def _wrap_dialogue(lines, width):
    """Word-wrap each spoken bubble to `width` columns, oldest first,
    flattening into individual display ROWS.

    Before this existed the TEXT subpanel ran every bubble through _clip,
    which collapses it to ONE row and truncates anything past `width` with
    an ellipsis — so on the live broadcast a tile showed only the first
    handful of words of whatever a character was saying, no matter how
    long the actual line was. Wrapping keeps the full line on screen,
    spread across as many rows as it needs; render_tile still trims the
    flattened row list down to whatever the pane's detected height allows
    (oldest rows drop off the top first, same as before).
    """
    rows = []
    for spoken in lines:
        text = " ".join(_ANSI_RE.sub("", str(spoken or "")).split())
        if not text:
            continue
        rows.extend(textwrap.wrap(text, max(1, width)) or [""])
    return rows


def render_tile(slot, expression="idle", line="", status="listening", out=None,
                clear=True, width=None, height=None, lines=None, brightness=1.0):
    """Draw the tile frame as three bordered SUBPANELS, top to bottom:
    AVATAR (fixed TILE_AVATAR_LINES rows), TEXT (the last spoken lines — grows
    to fill whatever vertical room is left), and STATUS (fixed
    TILE_STATUS_LINES row). Returns the rendered lines so tests can assert on
    them without scraping stdout.

    `slot` is accepted but NOT drawn inside the frame: the character name
    already appears on the pane's own tmux border (build_layout.py's
    _resolve_tile_title), so repeating it inside the box was pure
    duplication (confirmed on a live broadcast frame — every tile showed its
    resolved name twice, once on the border and once one line inside it).
    Kept as a parameter for call-site/API stability and because a future
    caller without a tmux border (e.g. a bare terminal test run) may still
    want it available.

    The avatar is drawn on EVERY frame, in every state — a tile must never go
    blank or collapse to text (§5). `lines` is the dialogue history (oldest
    first); `line` is the single-line shorthand kept for callers that only
    have a current line. The TEXT subpanel's row count is resolved from the
    pane's real detected height (resolve_dialogue_line_count) so it actually
    fills the panel instead of a hardcoded 2 lines, and stays fixed for a
    given height so a tile that starts talking cannot shove its neighbours
    around on air.

    `width`/`height` default to the pane's DETECTED size (resolve_tile_width /
    resolve_tile_height) so the frame always fits its real tmux pane — see the
    comment on resolve_tile_width for why assuming a size put a broken-looking
    tile on a live broadcast. Tests pass explicit values for determinism.

    `brightness` (1.0 = full, TILE_FADE_FLOOR..1.0 range — see TileRenderer's
    fade math, which is where the TIME-based value actually comes from) wraps
    the WRITTEN frame in a truecolor SGR color code scaled to that fraction,
    then resets at the end — the post-line fade feature (dim after speaking,
    fades back up to full the instant a new line starts). At brightness==1.0
    (the default, and every non-tile caller) this emits NO extra SGR at all,
    so the frame is byte-identical to before the feature existed — tmux's own
    `select-pane -P fg=colour117` (config/panels/tile.yaml) is what colors an
    unfaded tile, exactly as before. The RETURNED `rows` list is always the
    plain, uncolored content (tests assert on rows' text, not raw escape
    sequences) — only what actually reaches `out` carries the color wrap.
    """
    out = out or sys.stdout
    global _CURRENT_EXPRESSION
    _CURRENT_EXPRESSION = expression
    face = TILE_FACES.get(expression) or TILE_FACES["idle"]
    tile_width = width or resolve_tile_width()
    tile_height = height if height is not None else resolve_tile_height()
    inner = tile_width - 2
    line_chars = tile_width - 4
    # Resolved ONCE per frame and threaded into both the band and the
    # dialogue count: reading the mode twice could see a head fail in
    # between and emit a frame whose subpanels do not add up to the pane's
    # height, which scrolls the tile.
    avatar_rows = resolve_avatar_row_count(tile_height)
    pixel_head = active_tile_avatar() is not None
    dialogue_line_count = resolve_dialogue_line_count(tile_height,
                                                      avatar_rows=avatar_rows)

    if lines is None:
        lines = [line] if line else []
    # Word-wrap every bubble to the TEXT subpanel's width FIRST, then slice
    # to however many rows the pane actually has — newest content at the
    # bottom, oldest rows trimmed off the top, always exactly
    # dialogue_line_count rows so the TEXT subpanel's height is constant for
    # a given tile size. (Previously each bubble was force-fit onto a SINGLE
    # row via _clip + ellipsis truncation, so only the first few words of a
    # spoken line ever made it to air — see _wrap_dialogue.)
    dialogue = _wrap_dialogue(lines, line_chars)[-dialogue_line_count:]
    dialogue = [""] * (dialogue_line_count - len(dialogue)) + dialogue
    rows = ["┌" + "─" * inner + "┐"]
    # ── AVATAR subpanel ──────────────────────────────────────────────────────
    # No name row here — tmux's pane-border-format (build_layout.py's
    # _resolve_tile_title -> `select-pane -T`) already prints the resolved
    # character name on the pane's own top border, so this box starts
    # straight into the avatar.
    #
    # In PIXEL mode the band is printed BLANK: the 3D head's window is
    # already covering these rows on the X display, and an ASCII face
    # underneath it would only show through wherever the window does not
    # reach (the window is square and centred; the band is full-width), so
    # the tile would read as a head glued onto half a second face. The rows
    # are still emitted — they are what holds the divider and the dialogue
    # below the window instead of under it.
    if pixel_head:
        blank = "│" + " " * inner + "│"
        rows.extend([blank] * avatar_rows)
    else:
        for row in face:
            rows.append("│" + row.center(inner) + "│")
    # ── TEXT subpanel ────────────────────────────────────────────────────────
    rows.append("├" + "─" * inner + "┤")
    for spoken in dialogue:
        rows.append("│ " + spoken.ljust(line_chars) + " │")
    # ── STATUS subpanel ──────────────────────────────────────────────────────
    rows.append("├" + "─" * inner + "┤")
    rows.append("│ " + _clip(f"status: {status}", inner - 2).ljust(inner - 2) + " │")
    rows.append("└" + "─" * inner + "┘")
    if clear:
        out.write("\x1b[2J\x1b[H")
    frame = "\n".join(rows) + "\n"
    if brightness < 1.0:
        # \x1b[0m reset at the end so the dim doesn't bleed into whatever
        # tmux/the next pane prints after this frame — the same discipline
        # replay.py's Palette already follows for every other color it emits.
        out.write(_tile_color_code(brightness) + frame + "\x1b[0m")
    else:
        out.write(frame)
    try:
        out.flush()
    except Exception:  # a StringIO in tests, a closed pipe in production
        pass
    return rows


class TileRenderer:
    """The Performer's `out` for a tile — the piece that makes a tile look like
    a tile while a show is running.

    Before this existed the tile handed the Performer no `out` at all, so the
    Performer wrote the FULL replay transcript straight to the pane's stdout:
    the framed avatar was drawn once at idle and then immediately scrolled off
    the top by dialogue, shell output and edit diffs. On air that read as
    "the avatar disappears as soon as the show starts".

    So: every byte the Performer writes is SWALLOWED here (it is the other
    panes' job to show a transcript — the show_log pane is the director itself,
    §6.1) and used only as a clock to repaint the tile frame.

    The displayed dialogue comes from the tile's own avatar STATE file rather
    than by scraping the transcript. `replay.Performer._avatar` writes the
    complete spoken text as `bubble` (plus the expression) atomically at the
    start of every spoken line, so reading it back gives whole, correctly
    ordered lines with no ANSI parsing and no guessing where one speaker's line
    ends and the next begins. Unowned scenes write expression `idle` with no
    bubble, which is exactly right: this tile stays visible and quiet while
    another character talks.

    `history` bounds how many spoken lines are RETAINED (kept generous —
    render_tile only ever displays the last `resolve_dialogue_line_count()` of
    them, which is derived from the pane's real height at draw time, not from
    this buffer size).

    `fade_enabled` (design ask, post-line dim): once this tile stops being the
    active speaker (expression transitions away from "speaking"), its frame
    fades from full brightness down to TILE_FADE_FLOOR over TILE_FADE_DURATION_S
    — starting the INSTANT the line ends, not after a delay — so a viewer's eye
    is drawn to whichever tile just lit up. Brightness snaps straight back to
    1.0 the moment this tile starts speaking again. Because a fading tile must
    keep animating even while the Performer is BLOCKED in a silent wait (e.g.
    `wait_extra` holding the scene until audio finishes, or the inter-line
    `line_gap_s` pause) — periods where nothing calls `write()`/`flush()` at
    all — a small background ticker thread drives `draw()` for the fade
    window's duration on its own, independent of the Performer's own writes.
    The ticker only runs DURING an active fade (roughly TILE_FADE_DURATION_S
    per speaking->non-speaking transition), not continuously, so a tile that
    has been quiet for the whole show does not spin a thread forever. Off by
    default (tests / any caller not passing fade_enabled=True get the exact
    pre-fade-feature behavior and spawn no thread at all); `perform_tile_request`
    is the only production caller that turns it on. Call `close()` when done
    with a fade-enabled renderer to stop the ticker thread.
    """

    # Comfortably larger than any real TEXT subpanel will ever display, so the
    # buffer is never the limiting factor — render_tile's height-derived slice
    # is.
    DEFAULT_HISTORY = 50

    # Ticker wake interval while a fade is in progress — matches
    # TILE_PARTIAL_REDRAW_S's cadence philosophy (frequent enough to read as
    # a smooth fade, cheap enough that it's not meaningfully more CPU/I/O
    # than the typing-driven redraws already happening during active speech).
    FADE_TICK_S = 0.15

    def __init__(self, slot, state_path, out=None, history=DEFAULT_HISTORY,
                fade_enabled=False):
        self.slot = slot
        self.state_path = state_path
        self.out = out or sys.stdout
        self.lines = deque(maxlen=max(1, history))
        self.expression = "listening"
        self.status = "listening"
        self._last_bubble = None
        self._last_draw = 0.0
        # Serializes draw() calls between the main thread (write/flush,
        # driven by the Performer) and the fade ticker thread — without this
        # a ticker wake landing mid-write to `out` could interleave a partial
        # frame with the main thread's own render_tile() output, garbling
        # what actually reaches the pane. Held only around the actual
        # render_tile() call, not the state read/absorb bookkeeping, so it's
        # never contended for longer than one frame write takes.
        self._draw_lock = threading.Lock()
        # Fade state: _faded_since is the monotonic timestamp this tile last
        # stopped being the active speaker (None while currently speaking, or
        # before the first line of the show has been spoken at all — a fresh
        # tile starts at full brightness, nothing has "just stopped" yet).
        # _fade_active_until bounds how long the background ticker keeps
        # calling draw() after a transition — once now() passes it, the fade
        # has visually finished (frame is already at TILE_FADE_FLOOR) and the
        # ticker goes back to doing nothing until the next transition.
        self._faded_since = None
        self._fade_active_until = None
        self._fade_enabled = fade_enabled
        self._stop_event = threading.Event() if fade_enabled else None
        self._ticker = None
        if fade_enabled:
            self._ticker = threading.Thread(target=self._fade_ticker_loop, daemon=True)
            self._ticker.start()

    # ── the file-like contract the Performer uses ────────────────────────────
    def write(self, text):
        """Swallow the transcript, then repaint on a timer."""
        self._maybe_redraw()
        return len(text or "")

    def flush(self):
        self._maybe_redraw()

    def _maybe_redraw(self, force=False):
        now = time.monotonic()
        state = read_state(self.state_path) if self.state_path else None
        bubble = (state or {}).get("bubble")
        changed = self._absorb(state, bubble)
        # A new spoken line repaints IMMEDIATELY; otherwise the frame refreshes
        # on a timer so per-character typing can't repaint the pane thousands
        # of times a scene.
        if changed or force or (now - self._last_draw) >= TILE_PARTIAL_REDRAW_S:
            self.draw()

    def _absorb(self, state, bubble):
        """Fold a state read into the renderer. Returns True when something
        worth an immediate repaint changed."""
        changed = False
        expression = (state or {}).get("expression")
        if expression and expression != self.expression:
            was_speaking = self.expression == "speaking"
            now_speaking = expression == "speaking"
            self.expression = expression
            self.status = "speaking" if expression == "speaking" else "listening"
            changed = True
            if self._fade_enabled:
                if now_speaking:
                    # Resuming as the active speaker snaps straight back to
                    # full brightness — no lingering dim from before.
                    self._faded_since = None
                    self._fade_active_until = None
                elif was_speaking:
                    # Just stopped being the active speaker: the fade starts
                    # THIS INSTANT, not after any delay.
                    now = time.monotonic()
                    self._faded_since = now
                    self._fade_active_until = now + TILE_FADE_DURATION_S
        if bubble and bubble != self._last_bubble:
            self._last_bubble = bubble
            self.lines.append(bubble)
            changed = True
        return changed

    def _current_brightness(self, now=None):
        """1.0 while actively speaking or before any line has ever finished;
        linearly ramps down to TILE_FADE_FLOOR over TILE_FADE_DURATION_S once
        this tile stops speaking, and stays pinned at the floor after that —
        never fades out entirely (§5: a tile must always stay legible)."""
        if not self._fade_enabled or self._faded_since is None:
            return 1.0
        now = now if now is not None else time.monotonic()
        elapsed = now - self._faded_since
        if elapsed >= TILE_FADE_DURATION_S:
            return TILE_FADE_FLOOR
        fraction = elapsed / TILE_FADE_DURATION_S
        return 1.0 - fraction * (1.0 - TILE_FADE_FLOOR)

    def _fade_ticker_loop(self):
        """Background thread: while a fade is in progress, keep calling
        draw() so the frame visibly dims even during a Performer wait that
        never calls write()/flush() (audio playback, the inter-line gap).
        Sleeps between wakes rather than busy-polling; exits promptly once
        close() sets the stop event."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self.FADE_TICK_S)
            if self._stop_event.is_set():
                return
            deadline = self._fade_active_until
            if deadline is None:
                continue
            now = time.monotonic()
            self.draw()
            if now >= deadline:
                # One final draw already landed at the floor brightness
                # above; stop ticking until the next speaking transition
                # re-arms _fade_active_until.
                self._fade_active_until = None

    def refresh(self):
        """Repaint now, whatever the timer says — used to land the final frame
        of a show."""
        self._maybe_redraw(force=True)

    def draw(self):
        self._last_draw = time.monotonic()
        with self._draw_lock:
            return render_tile(self.slot, expression=self.expression,
                               lines=list(self.lines), status=self.status,
                               out=self.out, brightness=self._current_brightness())

    def close(self):
        """Stop the fade ticker thread, if one was started. Best-effort and
        idempotent — safe to call even when fade_enabled was False."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._ticker is not None:
            self._ticker.join(timeout=1.0)


def draw_idle_screen(slot, state_path=None, out=None):
    """Between shows: the slot's default avatar in `idle` with a neutral
    "listening" status (§5). The roundtable channel never goes blank."""
    write_tile_state(state_path, "idle", action="waiting for the next round")
    return render_tile(slot, expression="idle", line="", status="listening", out=out)


class TileAvatarDriver:
    """Drives a TileAvatar's frames from a DAEMON thread.

    Why a thread at all: the tile's own loop is not a render loop. It sleeps
    on relay files, waits for cues, and — while a show runs — types dialogue
    out character by character through the Performer. Calling the head's
    blit from there would tie the head's framerate to the typing cadence,
    so a head would freeze for the whole of any silent wait (audio playback,
    the inter-line gap) — exactly the stalls TileRenderer already needed its
    own fade ticker to animate through. A thread renders on its own clock
    and the tile never blocks on a blit.

    Why a DAEMON thread: a tile that is being torn down must not be kept
    alive by a head. The head is decoration; the process exiting is not
    negotiable.

    The loop body is fully wrapped: an exception escaping a thread's target
    kills only that thread, silently, which would leave a frozen window
    painted over a live tile — visually worse than no head at all. So
    anything unexpected stops the driver deliberately and the tile falls
    back to drawing the ASCII face on its next frame.
    """

    def __init__(self, avatar, expression_source=None, fps=TILE_AVATAR_FPS):
        self.avatar = avatar
        # Defaults to the module's last-rendered expression, which is set by
        # render_tile itself — so the head animates the same expression the
        # text frame is currently showing, with no second source of truth to
        # drift from it.
        self.expression_source = expression_source or (lambda: _CURRENT_EXPRESSION)
        self.interval_s = 1.0 / max(1.0, float(fps or TILE_AVATAR_FPS))
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self.avatar is None or not getattr(self.avatar, "active", False):
            return None
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="tile-avatar")
        self._thread.start()
        return self._thread

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                if not self.avatar.tick(self.expression_source()):
                    # tick() returning False means the head marked itself
                    # inactive (it logs its own single stderr line). Stop
                    # ticking rather than spinning at the frame rate on a
                    # head that will never draw again.
                    return
            except Exception as exc:  # noqa: BLE001 — never escape the thread
                print(f"[tile_pane] avatar driver stopping: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
                return
            self._stop_event.wait(self.interval_s)

    def close(self):
        """Stop ticking and tear the head down. Best-effort and idempotent."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.avatar is not None:
            try:
                self.avatar.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup only
                pass


def start_tile_avatar(config, slot, retry_s=None):
    """Build this tile's 3D head and start driving it, or return None.

    Returns None — leaving the tile in ASCII mode, exactly as it rendered
    before the head existed — for every reason a head might not happen:
    the slot has no preset configured (an uncast slot such as
    roundtable.yaml's tuber_4, absent from its roster), no pane geometry
    could be detected, or the provider could not be constructed.

    The geometry read RETRIES (detect_tile_pane_rect): startup.sh launches
    every pane's process before it creates and resizes the xterm window, so
    a one-shot read from a freshly started tile reliably returns a
    nonexistent or still-forming window.
    """
    try:
        if resolve_slot_character_params(config, slot) is None:
            return None
        kwargs = {} if retry_s is None else {"retry_s": retry_s}
        pane_rect = detect_tile_pane_rect(**kwargs)
        if pane_rect is None:
            print(f"[tile_pane] {slot}: no usable pane geometry for a 3D head "
                  f"(no tmux/X session, or the window never settled) — "
                  f"keeping the ASCII face", file=sys.stderr)
            return None
        avatar = make_tile_avatar(config, slot, pane_rect)
        if avatar is None or not getattr(avatar, "active", False):
            return None
        set_active_tile_avatar(avatar)
        driver = TileAvatarDriver(avatar)
        driver.start()
        print(f"[tile_pane] {slot}: 3D head active at {avatar.rect} "
              f"({TILE_AVATAR_FPS}fps)", file=sys.stderr)
        return driver
    except Exception as exc:  # noqa: BLE001 — a head is never a dependency
        print(f"[tile_pane] {slot}: 3D head unavailable "
              f"({type(exc).__name__}: {exc}) — keeping the ASCII face",
              file=sys.stderr)
        set_active_tile_avatar(None)
        return None



# ── the follower contract, locally ───────────────────────────────────────────
def make_owns(cast, slot):
    """The ownership predicate handed to replay_pane._rebuild_scenes_from_rows.

    True only when the cast maps that scene's speaker to THIS tile's slot —
    so only this slot's audio bytes ever get written to this tile's temp
    dir, and this tile plays nothing else. `target_duration` is set from the
    row regardless (inside _rebuild_scenes_from_rows), so visual pacing
    still tracks whoever does own the scene. Identical semantics to
    perform_follower_request's `cast.get(scene.get("speaker")) == self_id`,
    with the slot id standing in for the bus worker id."""
    cast = cast or {}

    def owns(scene, row=None):
        return cast.get((scene or {}).get("speaker")) == slot

    return owns


def make_wait_for_scene(cue_file, airing_id, show, slot, stop_file=None):
    """The local cue ratchet — the file-relay twin of
    perform_follower_request.wait_for_scene, same protocol, same constants:

      * a dict whose airing_id matches AND type == "cue" with an int
        scene_index >= the requested index authorizes that scene (a cue
        AHEAD of us is the fast-forward/catch-up rule, handled by
        Performer.perform);
      * type == "end" for this airing returns -1 (show over / aborted);
      * a cue for a DIFFERENT airing, or a cue BELOW the requested index, is
        stale and ignored — it must never authorize a scene;
      * the watchdog returns -1: a flat generous allowance for scene 0 (the
        director is still preparing/inviting), then the PREVIOUS scene's own
        target_duration plus a grace window, floored so a near-zero-duration
        scene doesn't produce a hair-trigger watchdog.

    `stop_file` (the operator replay_stop signal) is only ever READ here,
    never deleted: on the roundtable it belongs to the director process
    sharing this container, and a tile consuming it would disarm the stop
    for everyone else.
    """
    def wait_for_scene(index):
        if index == 0:
            timeout_s = replay_pane.REPLAY_FIRST_CUE_TIMEOUT_S
        else:
            prev = show[index - 1] if 0 <= index - 1 < len(show) else {}
            prev_duration = prev.get("target_duration") or 0
            timeout_s = max(replay_pane.REPLAY_WATCHDOG_MIN_S,
                            prev_duration + replay_pane.REPLAY_WATCHDOG_GRACE_S)
        deadline = time.monotonic() + timeout_s
        while True:
            if stop_file and os.path.exists(stop_file):
                return -1
            cue = replay_pane._read_json_file(cue_file)
            if isinstance(cue, dict) and cue.get("airing_id") == airing_id:
                if cue.get("type") == "end":
                    return -1
                if cue.get("type") == "cue":
                    scene_index = cue.get("scene_index")
                    if isinstance(scene_index, int) and not isinstance(scene_index, bool) \
                            and scene_index >= index:
                        return scene_index
            if time.monotonic() >= deadline:
                print(f"[tile_pane] {slot} watchdog timed out waiting for scene {index}",
                      file=sys.stderr)
                return -1
            time.sleep(replay_pane.REPLAY_CUE_POLL_INTERVAL_S)

    return wait_for_scene


def clear_stale_relay_files(relay_dir, slot):
    """Stale-state hygiene, BEFORE performing — mirroring the duet roles'
    rule (docs/duet_replay.md "stale-state hygiene", replay_pane lines
    ~548-563). This has caused real silent failures in this codebase: a
    leftover cue/end from a PREVIOUS airing is read on the very first poll
    of the new one, which either aborts the show instantly (a stale "end")
    or fast-forwards it into Performer.perform's catch-up path, where owned
    audio is discarded via playback.stop() instead of played — a tile that
    looks broken with no error anywhere. A leftover request file for this
    slot goes too, so a stale invite can't re-trigger the tile the moment
    this show ends."""
    replay_pane._delete_stale_file(tile_cue_file(relay_dir, slot))
    replay_pane._delete_stale_file(tile_request_file(relay_dir, slot))


def perform_tile_request(request, slot, relay_dir, state_path=None, config=None,
                         default_speed=1.0):
    """Perform ONE airing on this tile. Returns True only when the show
    actually aired; every failure path prints to stderr and returns False so
    the caller can go straight back to the idle screen.

    Shape copied from perform_follower_request: load the airing the director
    ALREADY persisted (never generate narration in a tile), keep audio only
    for the scenes cast to this slot, then perform scene-by-scene as the
    local cue file authorizes each one.
    """
    if not isinstance(request, dict):
        print(f"[tile_pane] {slot}: malformed request ({type(request).__name__}) — ignoring",
              file=sys.stderr)
        return False

    airing_id = request.get("airing_id")
    episode = request.get("episode")
    cast = request.get("cast")
    if not airing_id or not episode or not isinstance(cast, dict):
        print(f"[tile_pane] {slot}: malformed tile request (airing_id={airing_id!r} "
              f"episode={episode!r} cast={cast!r}) — ignoring", file=sys.stderr)
        return False

    episode_name, script = replay_pane.resolve_episode(episode)
    if script is None:
        print(f"[tile_pane] {slot}: episode not in the library: {episode!r}", file=sys.stderr)
        return False

    try:
        speed = float(request.get("speed") or default_speed)
    except (TypeError, ValueError):
        speed = default_speed
    # The on-screen persona name comes from the show header via the request
    # (§5: the slot is the stable identity, the display name is resolved at
    # scene time); the slot id is the fallback so a tile is never nameless.
    name = str(request.get("worker_name") or slot)

    if not narration_store.available():
        print(f"[tile_pane] {slot}: narration store unavailable — cannot perform airing",
              file=sys.stderr)
        return False
    try:
        rows = narration_store.load_airing(airing_id)
    except Exception as exc:
        print(f"[tile_pane] {slot}: airing load failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return False
    if not rows:
        print(f"[tile_pane] {slot}: no cached airing {airing_id!r} to perform",
              file=sys.stderr)
        return False

    cue_file = tile_cue_file(relay_dir, slot)
    stop_file = replay_pane._resolve_replay_stop_file()
    owns = make_owns(cast, slot)

    with tempfile.TemporaryDirectory(prefix=f"tile_{slot}_") as workdir:
        try:
            show = replay_pane._rebuild_scenes_from_rows(script, rows, workdir, owns=owns)
        except Exception as exc:
            print(f"[tile_pane] {slot}: scene rebuild failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return False
        if show is None:
            print(f"[tile_pane] {slot}: cached airing {airing_id!r} no longer matches the "
                  f"episode script — cannot perform", file=sys.stderr)
            return False

        # "owned" is what Performer._perform_scene gates speaking on; audio
        # for unowned scenes was never written to workdir in the first place
        # (see make_owns), and target_duration is kept so this tile paces to
        # the owner's timing instead of racing ahead.
        for scene in show:
            scene["owned"] = bool(owns(scene))

        clear_stale_relay_files(relay_dir, slot)

        wait_for_scene = make_wait_for_scene(cue_file, airing_id, show, slot,
                                             stop_file=stop_file)
        voice = (config or {}).get("voice") or {}
        # The Performer writes its transcript into the tile renderer instead of
        # straight to the pane. Without this the avatar frame was drawn once at
        # idle and then scrolled off the top by the first scene's dialogue —
        # the tile showed a wall of text for the whole show. The renderer keeps
        # the avatar and the last TILE_DIALOGUE_LINES spoken lines on screen
        # from the first frame to the last.
        #
        # fade_enabled=True here (the only production call site): a live
        # airing is exactly the case the post-line dim design ask targets —
        # tiles between lines fade to TILE_FADE_FLOOR brightness so a
        # viewer's eye is drawn to whoever is actively speaking. Wrapped in
        # try/finally so the background fade ticker thread is always stopped
        # (renderer.close()) even if the Performer raises mid-show — a tile
        # process that leaks a ticker thread per show would eventually
        # accumulate threads across a long broadcast.
        renderer = TileRenderer(slot, state_path, fade_enabled=True)
        try:
            renderer.draw()
            # Voice gate (docs/voice_gate.md): all seven tiles + the director in
            # this container share one gate (the container's own /tmp), so a
            # tile cannot start its line while a sibling tile's line is still
            # sounding — the roundtable gets "one voice at a time" (default 1
            # seat; show.audio.max_concurrent or VOICE_GATE_CONCURRENT allows
            # deliberate overlap).
            gate, line_gap_s = replay_pane.build_voice_gate(script, config, tag=f"tile:{slot}")
            performer = Performer(
                out=renderer,
                pacer=Pacer(speed=speed, should_stop=lambda: os.path.exists(stop_file)),
                palette=Palette(enabled=True),
                worker_name=name,
                state_path=state_path,
                wait_for_scene=wait_for_scene,
                speaker_names=voice.get("speaker_names") or {},
                boss_name=voice.get("boss_name"),
                voice_gate=gate,
                line_gap_s=line_gap_s,
            )
            performer.perform(script, show=show)
            # Leave the final frame up (the caller holds it for
            # TILE_HOLD_FINAL_FRAME_S) rather than whatever the last partial
            # repaint happened to catch.
            renderer.refresh()
        finally:
            renderer.close()

    # The cue file is this tile's alone; consume it so the NEXT show starts
    # from a clean slate too (the request file was already consumed by
    # read_request before we got here).
    replay_pane._delete_stale_file(cue_file)
    return True


def handle_once(slot, relay_dir, state_path=None, config=None, default_speed=1.0):
    """Poll once: consume a pending request (if any) and perform it.
    Returns True when a show aired. Never raises — this is the seam the
    idle loop wraps, and §5's "the roundtable never goes blank" rule means
    a bad show must degrade to the idle screen, not kill the tile."""
    request = replay_pane.read_request(tile_request_file(relay_dir, slot))
    if not request:
        return False
    try:
        return perform_tile_request(request, slot, relay_dir, state_path=state_path,
                                    config=config, default_speed=default_speed)
    except Exception as exc:  # one bad show must never kill the tile
        print(f"[tile_pane] {slot}: show failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return False


def build_parser():
    parser = argparse.ArgumentParser(
        description="Roundtable tile pane — performs one slot's scenes of a director's airing")
    parser.add_argument("--slot", required=True,
                        help="This tile's character slot id, e.g. tuber_2")
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", DEFAULT_WORKER_CONFIG),
                        help="Worker config YAML — its voice section supplies "
                             "speaker_names/boss_name for on-screen labels")
    parser.add_argument("--relay-dir", default=None,
                        help=f"Director -> tile relay directory "
                             f"(default: $TILE_RELAY_DIR or {DEFAULT_RELAY_DIR})")
    parser.add_argument("--state-file", default=None,
                        help="Per-tile avatar state file "
                             "(default: <relay-dir>/<slot>.state.json)")
    parser.add_argument("--once", action="store_true",
                        help="Handle at most one show, then exit (testing)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    slot = args.slot
    relay_dir = resolve_relay_dir(args.relay_dir)
    ensure_relay_dir(relay_dir)
    # Per-tile by construction — NOT agent_state.resolve_state_path, which
    # would collapse all seven tiles onto one file (§5.1 / G2).
    state_path = args.state_file or tile_state_file(relay_dir, slot)
    config = replay_pane.load_worker_config(args.config)

    print(f"[tile_pane] slot={slot} relay_dir={relay_dir} state_file={state_path} "
          f"narration_store={'ok' if narration_store.available() else 'UNAVAILABLE'}",
          file=sys.stderr)

    # The 3D head, if this slot has one. Started BEFORE the first frame is
    # drawn so the very first render already reserves the blank band the
    # window sits in, rather than drawing an ASCII face and replacing it a
    # few seconds later — a visible flicker on a live broadcast. Returns
    # None for an uncast slot or any failure, in which case everything below
    # renders exactly as it did before the head existed.
    avatar_driver = start_tile_avatar(config, slot)

    try:
        if args.once:
            draw_idle_screen(slot, state_path)
            handle_once(slot, relay_dir, state_path=state_path, config=config)
            return 0

        last_drawn = 0.0
        while True:
            if handle_once(slot, relay_dir, state_path=state_path, config=config):
                time.sleep(TILE_HOLD_FINAL_FRAME_S)  # hold the final frame briefly
                last_drawn = 0.0  # force an idle redraw
            if time.monotonic() - last_drawn > TILE_IDLE_REDRAW_S:
                draw_idle_screen(slot, state_path)
                last_drawn = time.monotonic()
            time.sleep(TILE_POLL_INTERVAL_S)
    finally:
        # Stops the driver thread and the GPU render subprocess behind it.
        # The thread is a daemon so this is not required for the process to
        # exit, but leaving a spawned GPU worker running past its parent
        # would keep a head's share of the container's GPU budget occupied
        # for nothing.
        if avatar_driver is not None:
            avatar_driver.close()
            set_active_tile_avatar(None)


if __name__ == "__main__":
    sys.exit(main())
