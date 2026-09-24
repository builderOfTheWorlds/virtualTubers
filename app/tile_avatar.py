#!/usr/bin/env python3
"""
tile_avatar.py
Pure geometry + config resolution for a roundtable tile's 3D avatar head.

WHAT THIS IS FOR. A roundtable tile (app/tile_pane.py) draws three stacked
subpanels — AVATAR, TEXT, STATUS — as terminal text inside a tmux pane.
The AVATAR subpanel is the one that a real 3D head replaces: ffmpeg
captures the whole X DISPLAY, not tmux's character grid, so a small
borderless window parked over the top of a tile is captured exactly as if
it had been drawn into the pane. To park it there something has to answer
two questions — "where, in PIXELS, does this tile's head go?" and "which
character preset does this slot's head use?" — and this module is where
those answers live.

WHY IT HAS NO DISPLAY OR GL IMPORTS. Deliberately no pygame, no moderngl,
no OpenGL, not even a lazy module-scope import of app/codec_avatar.py.
Every function here is arithmetic over plain numbers and dicts, so the
whole module imports and runs on a headless box with no X display, no GPU
and no SDL — which is what CI and `pytest` actually are. The moment this
file imports a display library, the geometry it owns stops being testable
without a display, and the pixel math that positions a head over a live
broadcast tile becomes the one thing nobody can check before it ships.
The window-creating code imports THIS module, never the reverse.

WHY IT IS NOT INSIDE tile_pane.py. tile_pane.py is the long-lived pane
process; this is the arithmetic it (and the renderer that owns the head
window) consults. Keeping them apart means the geometry can be exercised
without standing up a pane, a relay dir, or a narration store.

FAILURE POLICY, same as every pane function in this repo: degrade, never
raise. A tile that throws blanks a live broadcast cell, and a blank cell
in an eight-tile grid is the most visible failure this project has. So a
zero-sized, negative, or nonsensical rect returns a sane (possibly empty)
rect and lets the caller decide to skip drawing — it never propagates an
exception up into a pane loop.
"""

# The tile's own tmux border is drawn by tmux in the pane's TOP row of
# cells. A window sized exactly to the pane paints straight over that row,
# which detaches the avatar from its frame and makes the tile read as a
# floating rectangle rather than a character in a box — the same finding
# that put the 8px inset into config/workers/coder.yaml's codec_avatar
# block ("an exactly pane-sized window would paint over the border and
# detach the avatar from the frame"). 8px is one cell row at the deployed
# 6x12 cell, so the inset is exactly "leave the border row alone".
AVATAR_TOP_INSET_PX = 8

# Horizontal breathing room on each side, for the same reason as the top
# inset: the pane's left/right border columns must stay visible. A head is
# centred horizontally, so this only ever matters when the requested window
# is wider than the tile can afford.
AVATAR_SIDE_INSET_PX = 8

# What fraction of a tile's HEIGHT the avatar subpanel owns. 0.4 of a
# 540px roundtable tile is 216px, which at the deployed 12px cell is 18
# terminal rows — comfortably more than the 3 rows today's ASCII face uses
# (tile_pane.TILE_AVATAR_LINES), and still leaving the TEXT subpanel the
# majority of the tile, which is what the design asks for (the text is the
# content; the head is the identity cue).
DEFAULT_AVATAR_FRACTION = 0.4

# Default side of the square head window. A square is deliberate: the head
# render is a centred bust, and a non-square viewport either letterboxes or
# distorts it. 200px sits inside the 216px band a default 4x2 tile reserves
# without touching the divider below it.
DEFAULT_AVATAR_WINDOW_PX = 200

# Floor for the ASCII fallback: even a tile too short to deserve a 3D head
# still reserves the rows tile_pane.TILE_FACES needs, so the AVATAR
# subpanel never collapses to nothing.
MIN_AVATAR_ROWS = 3


def _as_int(value, default=0):
    """Coerce anything a config or a detector might hand us into an int,
    falling back rather than raising. detect_pane_rect() returns clean ints
    today, but a configured override (a YAML `window_pos`, an env var) is
    whatever the operator typed."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def tile_avatar_rect(pane_rect, avatar_fraction=DEFAULT_AVATAR_FRACTION,
                     window_px=DEFAULT_AVATAR_WINDOW_PX):
    """Pixel rect (x, y, w, h) for the 3D head window of the tile whose
    on-screen pixel rect is `pane_rect` (x, y, width, height — the shape
    app/pane_geometry.py's detect_pane_rect returns).

    The window is:
      * SQUARE — `window_px` on a side, clamped so it can never exceed the
        room actually available (see below);
      * centred HORIZONTALLY in the tile, because the head is the tile's
        identity cue and an off-centre head reads as a rendering bug;
      * inset AVATAR_TOP_INSET_PX from the tile's top edge, so tmux keeps
        drawing the pane's own top border (coder.yaml's avatar window uses
        the same 8px inset for exactly this reason);
      * contained entirely within the avatar BAND — the top
        `avatar_fraction` of the tile's height. Overflowing the band would
        paint over the AVATAR/TEXT divider and the dialogue rows beneath
        it, which is the one thing the tile cannot afford to lose.

    At the deployed geometry (1920x1080, FONT_SIZE=7 -> 6x12px cells ->
    320x90 grid -> 80x45-char, 480x540px tiles) this yields a 200x200
    window at x = tile_x + 140, y = tile_y + 8: inside a 216px band with
    8px to spare at the bottom.

    Degenerate input degrades instead of raising (see module docstring): a
    zero, negative, tiny or unparseable rect yields a rect with zero width
    and height at the tile's origin, which a caller should read as "no room
    for a head here, keep the ASCII face".
    """
    try:
        x, y, width, height = (_as_int(v) for v in tuple(pane_rect)[:4])
    except (TypeError, ValueError):
        # Not even rect-shaped (None, a string, a 2-tuple). Nothing sane to
        # centre anything in.
        return (0, 0, 0, 0)

    if width <= 0 or height <= 0:
        return (x, y, 0, 0)

    try:
        fraction = float(avatar_fraction)
    except (TypeError, ValueError):
        fraction = DEFAULT_AVATAR_FRACTION
    # A fraction outside (0, 1] is meaningless; clamping beats raising, and
    # 1.0 ("the head owns the whole tile") is a legitimate caller choice.
    fraction = min(1.0, max(0.0, fraction))

    band_height = int(height * fraction)
    if band_height <= 0:
        return (x, y, 0, 0)

    # On a normal tile the insets are affordable. On a pathologically small
    # one they are not, and an inset larger than the space it is protecting
    # would produce a negative side length — so shrink the insets first and
    # only then measure what is left.
    top_inset = min(AVATAR_TOP_INSET_PX, max(0, band_height - 1))
    side_inset = min(AVATAR_SIDE_INSET_PX, max(0, (width - 1) // 2))

    available_h = band_height - top_inset
    available_w = width - 2 * side_inset
    side = min(_as_int(window_px), available_w, available_h)
    if side <= 0:
        return (x, y, 0, 0)

    # Integer-divide the leftover so the window lands on a whole pixel; a
    # half-pixel X offset is what makes a captured render look soft.
    return (x + (width - side) // 2, y + top_inset, side, side)


def avatar_subpanel_rows(tile_height_rows, avatar_fraction=DEFAULT_AVATAR_FRACTION,
                         min_rows=MIN_AVATAR_ROWS):
    """How many terminal ROWS the AVATAR subpanel occupies in a tile that is
    `tile_height_rows` rows tall.

    This is the character-grid twin of tile_avatar_rect's pixel band: the
    head window covers these rows, and tile_pane.py must reserve exactly
    them so the TEXT subpanel starts below the window rather than under it.
    Same fraction, same rounding direction (floor — better to reserve one
    row too few and leave a pixel gap than one too many and push the
    dialogue off the bottom of the pane).

    Floored at `min_rows` so a tile too short for a 3D head still reserves
    the rows tile_pane.TILE_FACES' ASCII face needs; capped at the tile's
    own height because reserving more rows than the tile has is how a frame
    ends up taller than its pane, which scrolls the avatar off the top.
    """
    rows = _as_int(tile_height_rows)
    if rows <= 0:
        return max(0, _as_int(min_rows))
    try:
        fraction = float(avatar_fraction)
    except (TypeError, ValueError):
        fraction = DEFAULT_AVATAR_FRACTION
    fraction = min(1.0, max(0.0, fraction))
    floor_rows = max(0, _as_int(min_rows))
    return min(rows, max(floor_rows, int(rows * fraction)))


def resolve_slot_character_params(config, slot):
    """The 3D character preset name for `slot`, or None.

    Reads the worker config's `roster:` mapping — the SAME mapping
    app/build_layout.py's _resolve_tile_title already reads for a tile's
    on-screen name — so a character's display name and its head are
    configured in one place instead of two that can silently disagree.

    Two entry shapes are accepted, on purpose:

        roster:
          tuber_1: "Chadwick"                    # plain string (today)
          tuber_2:                               # mapping (avatar-aware)
            name: "Vigil"
            character_params: nyx1

    The plain string is the form every existing config uses and
    _resolve_tile_title consumes via `str(roster[slot])`; it must keep
    working untouched, which is why this function returns None for it
    rather than trying to guess a preset from a display name. The mapping
    form lets a slot carry avatar information without breaking that reader
    (its `str()` of a dict would be wrong, so build_layout's own handling of
    the mapping form is its business — this module only reads
    character_params and never rewrites the roster).

    None means "this slot gets no 3D head" — the caller keeps the ASCII face
    from tile_pane.TILE_FACES. That is the answer for a missing/empty
    config, an absent slot, a plain-string entry, and a mapping with no
    character_params key alike: four different ways of not having configured
    a head, all of which must degrade to the face that always works rather
    than to an exception inside a pane.
    """
    if not isinstance(config, dict):
        return None
    roster = config.get("roster")
    if not isinstance(roster, dict):
        return None
    entry = roster.get(slot)
    if not isinstance(entry, dict):
        # Plain string (or None/absent): a named character with no avatar
        # configured.
        return None
    params = entry.get("character_params")
    if params is None:
        return None
    # A preset NAME is the only form this returns. An inline slider mapping
    # (the other shape codec_avatar accepts, see coder.yaml's comment) is
    # deliberately passed through unchanged only when it is a string; any
    # other type is left to the renderer's own validation rather than being
    # stringified into a bogus preset name here.
    if isinstance(params, str):
        params = params.strip()
        return params or None
    return None


# ── the runtime wrapper ──────────────────────────────────────────────────────
# Everything BELOW this line may touch a display; everything above it must
# not. The split is load-bearing (see the module docstring): the functions
# above are imported and exercised by a headless unit suite, so the display
# libraries below are imported inside methods, never at module scope. Adding
# a top-level `import pygame` here would make `import tile_avatar` fail on
# every machine without SDL, taking the pure geometry tests down with it.

#: Render cadence for a TILE's head, in frames per second — deliberately
#: well below the 30fps CodecAvatarProvider defaults to for a full-pane
#: avatar. Two reasons, both specific to the roundtable:
#:
#:   1. EIGHT heads share ONE container. A tile is not one avatar, it is one
#:      of eight simultaneous renders on the same CPU/GPU, so the per-head
#:      cost multiplies by eight before it reaches the budget the stream's
#:      own ffmpeg encode still has to fit inside. 12fps is 40% of the work
#:      30fps would be.
#:   2. A tile head is a secondary visual cue, not the focal point. On the
#:      roundtable the CONTENT is the dialogue in the TEXT subpanel; the head
#:      answers "who is this and are they animated/alive", which a 12fps
#:      rotation conveys just as well as a 30fps one at 200x200 pixels. The
#:      solo character channels, where the head IS the frame, keep 30.
TILE_AVATAR_FPS = 12

#: Window background for a tile head. Matches the Solarized Dark base03 the
#: tile frame is drawn on, so the square window reads as part of the tile
#: instead of a slab pasted over it (the same reasoning as codec_avatar's
#: default console composite). Keep in sync with startup.sh's xterm `-bg`.
TILE_AVATAR_BACKGROUND = "#002b36"

#: How long to keep retrying pane geometry detection before giving up.
#: startup.sh launches every pane's process BEFORE it creates and resizes
#: the xterm window, so the FIRST read from a freshly started tile reliably
#: races that setup and returns a nonexistent or still-forming window (a 1x0
#: rect was observed live). Same deadline-loop shape, and the same reason,
#: as CodecAvatarProvider._resolve_geometry.
TILE_GEOMETRY_RETRY_S = 5.0

#: Interval between geometry retries. Cheap enough to poll at, long enough
#: that a tile that will never have a window does not spin.
TILE_GEOMETRY_POLL_S = 0.5

#: Smallest pane rect worth trusting. Anything smaller is the half-formed
#: window described above, not a real tile — accepting it would position an
#: invisible window with no error anywhere.
TILE_GEOMETRY_MIN_PX = 32


def detect_tile_pane_rect(retry_s=TILE_GEOMETRY_RETRY_S,
                          poll_s=TILE_GEOMETRY_POLL_S,
                          min_px=TILE_GEOMETRY_MIN_PX):
    """This pane's on-screen pixel rect, retried until it looks plausible or
    the deadline passes. Returns None when no usable rect ever appeared —
    no tmux, no X display, no xdotool, or a window that never settled — in
    which case the caller keeps the ASCII face.

    pane_geometry is imported lazily for the same reason the display
    libraries are: it shells out to tmux/xdotool, and keeping it out of
    module scope means `import tile_avatar` stays a pure-Python no-op.
    """
    import time

    from pane_geometry import detect_pane_rect

    deadline = time.monotonic() + max(0.0, float(retry_s or 0.0))
    while True:
        try:
            candidate = detect_pane_rect()
        except Exception:  # noqa: BLE001 — best-effort; never take a tile down
            candidate = None
        if (candidate is not None and len(candidate) >= 4
                and candidate[2] >= min_px and candidate[3] >= min_px):
            return tuple(int(v) for v in candidate[:4])
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_s)


def build_tile_avatar_config(character_params, rect, fps=TILE_AVATAR_FPS,
                             background=TILE_AVATAR_BACKGROUND):
    """The `avatar_config` dict CodecAvatarProvider expects, for one tile.

    Shaped exactly the way the provider reads it: a `provider` key (what
    avatar_providers.__init__ dispatches on) plus a nested `codec_avatar`
    block, which is where the provider's own __init__ looks for
    character_params/window_pos/width/height/background/gpu_subprocess/fps.

    `window_pos` is passed EXPLICITLY, which also disables the provider's
    own pane auto-detection — and that is the point. The provider's
    detection narrows a pane to half its width and fills its full height,
    which is right for a tall solo avatar pane and wrong for a tile, where
    the head must occupy only the top `avatar_fraction` band that
    tile_avatar_rect computes and leave the dialogue rows below it alone.

    gpu_subprocess is hardcoded True rather than exposed: the provider's own
    process owns the pygame window, and that process sets
    AVATAR_HAS_PYGAME_WINDOW=1, which makes gl_raster.is_available() refuse
    GPU rendering IN THAT PROCESS (see gpu_render_worker.py's docstring — an
    in-process GL context and an SDL X11 window cannot share the deploy
    host's Xvfb/driver without a BadAccess crash or a silent degrade to
    llvmpipe). So without the subprocess there is no hardware acceleration
    available at all, and eight software-rendered heads is exactly the cost
    this design cannot afford. The provider still degrades to in-process CPU
    on its own if the worker fails to start.
    """
    x, y, width, height = rect
    return {
        "provider": "codec_avatar",
        "codec_avatar": {
            "character_params": character_params,
            "window_pos": [int(x), int(y)],
            "width": int(width),
            "height": int(height),
            "background": background,
            "gpu_subprocess": True,
            "fps": fps,
        },
    }


class TileAvatar:
    """One roundtable tile's live 3D head: a small borderless pygame window
    parked over that tile's AVATAR subpanel, driven by CodecAvatarProvider.

    This is an UPGRADE, never a dependency. Every failure path — no pygame,
    no DISPLAY, a window that will not open, a GPU worker that dies mid-run
    — leaves `.active` False and the caller drawing the ASCII face from
    tile_pane.TILE_FACES. A roundtable tile must never go blank and one bad
    render must never kill a tile, so nothing here raises out to its caller.

    FAIL ONCE, FAIL FOR GOOD. A tick() that raises marks this object
    permanently inactive rather than retrying on the next frame. At
    TILE_AVATAR_FPS that retry would be twelve attempts a second, eight
    tiles over, each one paying the full cost of whatever failed — and a
    provider that failed once keeps failing (the same judgement
    CodecAvatarProvider.render_tick already records for its own mid-run GPU
    worker fallback). One stderr line, then the ASCII face for the rest of
    the process's life, which is a tile that looks slightly plainer rather
    than a tile that stutters or dies.
    """

    def __init__(self, slot, character_params, pane_rect,
                 avatar_fraction=DEFAULT_AVATAR_FRACTION,
                 window_px=DEFAULT_AVATAR_WINDOW_PX,
                 fps=TILE_AVATAR_FPS,
                 background=TILE_AVATAR_BACKGROUND):
        self.slot = slot
        self.character_params = character_params
        self.pane_rect = pane_rect
        self.rect = tile_avatar_rect(pane_rect, avatar_fraction=avatar_fraction,
                                     window_px=window_px)
        self.fps = fps
        self.active = False
        self._provider = None
        self._failed = False

        if self.rect[2] <= 0 or self.rect[3] <= 0:
            self._fail("no room for a 3D head in pane rect "
                       f"{pane_rect!r} (computed window {self.rect!r})")
            return
        if not character_params:
            self._fail("no character preset configured")
            return

        try:
            # Imported HERE, not at module scope: this is the line that
            # needs SDL/numpy/GL to exist, and the pure geometry above must
            # stay importable on a box that has none of them.
            from avatar_providers.codec_avatar import CodecAvatarProvider
            self._provider = CodecAvatarProvider(
                build_tile_avatar_config(character_params, self.rect,
                                         fps=fps, background=background),
                str(slot), str(slot))
            self.active = True
        except Exception as exc:  # noqa: BLE001 — degrade to ASCII, never raise
            self._fail(f"could not start the 3D head ({type(exc).__name__}: {exc})")

    def _fail(self, reason):
        """Mark this head permanently inactive and say why — ONCE. stderr
        only: a tile's stdout is the live video display."""
        self.active = False
        if not self._failed:
            self._failed = True
            import sys
            print(f"[tile_avatar] {self.slot}: {reason} — falling back to the "
                  f"ASCII face for the rest of this process's life",
                  file=sys.stderr)

    def tick(self, expression):
        """Render one frame. Returns True when a frame was actually drawn,
        False when this head is inactive (the caller should be drawing the
        ASCII face). Never raises."""
        if not self.active or self._provider is None:
            return False
        try:
            # bubble_lines is deliberately empty: a tile's dialogue lives in
            # its TEXT subpanel, drawn by tile_pane as terminal text. Passing
            # captions here would only trip codec_avatar's "not implemented
            # for the pixel-window path" warning once per tile.
            self._provider.render_tick(expression, None)
            return True
        except Exception as exc:  # noqa: BLE001 — see the class docstring
            self._fail(f"render failed ({type(exc).__name__}: {exc})")
            return False

    def close(self):
        """Best-effort teardown: stop the GPU subprocess and drop the
        window. Idempotent, and safe to call on a head that never became
        active — a tile shutting down must not raise any more than a tile
        rendering must."""
        self.active = False
        provider, self._provider = self._provider, None
        if provider is None:
            return
        try:
            source = getattr(provider, "_source", None)
            if source is not None and hasattr(source, "close"):
                source.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup only
            pass
        try:
            pygame = getattr(provider, "_pygame", None)
            if pygame is not None:
                pygame.display.quit()
        except Exception:  # noqa: BLE001 — best-effort cleanup only
            pass


def make_tile_avatar(config, slot, pane_rect, **kwargs):
    """The one entry point a tile calls: a live TileAvatar, or None.

    None means "this slot has no 3D head" — either because the roster
    configures no preset for it (an uncast slot such as roundtable.yaml's
    tuber_4, which is absent from the roster entirely) or because no usable
    pane rect was detected. Returning None rather than an inert object keeps
    the caller on ONE code path: `if avatar is None or not avatar.active:
    draw the ASCII face`.

    A constructed-but-failed TileAvatar is returned rather than None on
    purpose, so its own single stderr line explains WHY a tile that should
    have had a head does not.
    """
    character_params = resolve_slot_character_params(config, slot)
    if not character_params:
        return None
    if pane_rect is None:
        return None
    return TileAvatar(slot, character_params, pane_rect, **kwargs)


__all__ = [
    "AVATAR_TOP_INSET_PX",
    "AVATAR_SIDE_INSET_PX",
    "DEFAULT_AVATAR_FRACTION",
    "DEFAULT_AVATAR_WINDOW_PX",
    "MIN_AVATAR_ROWS",
    "TILE_AVATAR_FPS",
    "TILE_AVATAR_BACKGROUND",
    "TILE_GEOMETRY_RETRY_S",
    "TILE_GEOMETRY_MIN_PX",
    "tile_avatar_rect",
    "avatar_subpanel_rows",
    "resolve_slot_character_params",
    "detect_tile_pane_rect",
    "build_tile_avatar_config",
    "TileAvatar",
    "make_tile_avatar",
]
