# Roundtable tile rendering — `render_tile` / `TileRenderer`

## Overview

`app/tile_pane.py` draws one character slot's mini-stage on the roundtable
channel. Two pieces own what a viewer actually sees:

- **`render_tile`** paints one frame: slot name, avatar face, the last N spoken
  lines, and a status row.
- **`TileRenderer`** is the file-like object handed to `replay.Performer` as its
  `out` while a show runs. It swallows the Performer's transcript and repaints
  the tile frame instead.

### Why `TileRenderer` exists

Before it, the tile passed the Performer no `out`, so the Performer wrote the
full replay transcript (dialogue, shell output, edit diffs) straight to the
pane's stdout. The framed avatar was drawn once at idle and then immediately
scrolled off the top by the first scene. On air that looked like "the avatar
disappears as soon as the show starts".

The transcript is not lost — the roundtable's `show_log` pane *is* the director
process (`roundtable_stream_design.md` §6.1) and renders the full ordered
transcript there. A tile shows an avatar and recent lines; the log shows
everything.

### Why no name row inside the frame

`render_tile` accepts `slot` but does not draw it. The character name already
appears on the pane's own tmux top border — `build_layout.py`'s
`_resolve_tile_title` resolves it and `emit_tmux` sets it via
`select-pane -T`. A name row one line inside the box duplicated that border
text (confirmed on a live broadcast frame: every tile showed its resolved
name twice). `slot` stays a parameter for API/call-site stability.

### Where the displayed lines come from

Not from scraping the transcript. `replay.Performer._avatar` atomically writes
the complete spoken text as `bubble` (plus an `expression`) to the tile's own
`<slot>.state.json` at the start of every spoken line. `TileRenderer` reads that
back, so it gets whole, correctly-ordered lines with no ANSI parsing and no
guessing where one speaker's line ends. Unowned scenes write `idle` with no
bubble — the tile stays visible and quiet while another character talks.

## Signatures

```python
render_tile(slot, expression="idle", line="", status="listening", out=None,
            clear=True, width=None, height=None, lines=None) -> list[str]

TileRenderer(slot, state_path, out=None, history=TileRenderer.DEFAULT_HISTORY)
```

## Parameters

### `render_tile`

| Param | Type | Default | Notes |
|---|---|---|---|
| `slot` | str | required | Slot id shown in the name row (`tuber_2`). |
| `expression` | str | `"idle"` | Key into `TILE_FACES`; unknown values fall back to `idle`. |
| `line` | str | `""` | Single-line shorthand for callers with only a current line. Ignored when `lines` is given. |
| `status` | str | `"listening"` | Text of the status row. |
| `out` | file-like | `sys.stdout` | Where the frame is written. |
| `clear` | bool | `True` | Emit `\x1b[2J\x1b[H` before the frame. |
| `width` | int | detected | Defaults to `resolve_tile_width()` (the real pane width). Tests pass an explicit width. |
| `lines` | list[str] | `None` | Dialogue history, oldest first. Only the last `TILE_DIALOGUE_LINES` render. |

### `TileRenderer`

| Param | Type | Default | Notes |
|---|---|---|---|
| `slot` | str | required | Slot this renderer draws. |
| `state_path` | str | required | The tile's own `<slot>.state.json`. May be missing — reads degrade to no update. |
| `out` | file-like | `sys.stdout` | The pane. |
| `history` | int | `TILE_DIALOGUE_LINES` | How many spoken lines to retain. |

Methods: `write(text)` / `flush()` (the Performer's file contract — swallow and
repaint), `refresh()` (force a repaint now), `draw()` (paint the current state).

## Return values

`render_tile` returns the rendered rows as a list of strings, so tests can
assert on the frame without scraping stdout. `TileRenderer.draw()` returns the
same. `write()` returns the byte count it was handed.

## Constants

| Name | Value | Meaning |
|---|---|---|
| `TILE_AVATAR_LINES` | `3` | Fixed height of the AVATAR subpanel — "an OK height for now" per the design ask. |
| `TILE_STATUS_LINES` | `1` | Fixed height of the STATUS subpanel. |
| `MIN_DIALOGUE_LINES` | `2` | Floor for the TEXT subpanel — how small it can shrink to on a tiny/undetected pane. |
| `TILE_FIXED_OVERHEAD` | `8` | Rows outside the TEXT subpanel (top+bottom border, avatar, avatar/text divider, text/status divider, status). `resolve_dialogue_line_count` subtracts this from the pane's real height. |
| `TILE_PARTIAL_REDRAW_S` | `0.15` | Mid-line repaint cadence. A *new* line repaints immediately; per-character typing does not. |
| `TILE_FACES` | 6 entries, 3 rows each | One per expression `Performer._avatar` writes (`speaking`, `listening`, `idle`, `thinking`, `focused`, `frustrated`). A missing entry silently falls back to `idle`, which made tiles look asleep mid-show. |

## Dependencies

`agent_state.read_state` / `write_state`, `replay.Performer` / `Pacer` /
`Palette`, `replay_pane`, `narration_store`.

## Usage

```python
# during a show — the Performer paints the tile as a side effect
renderer = TileRenderer(slot, state_path)
renderer.draw()
performer = Performer(out=renderer, state_path=state_path, ...)
performer.perform(script, show=show)
renderer.refresh()   # land the final frame

# a one-off frame (idle screen, tests)
render_tile("tuber_2", expression="speaking",
            lines=["The lantern gutters.", "Then we go north."], width=31)
```

## Error handling

Nothing here raises out of the pane loop (§5: the roundtable never goes blank).
A missing or malformed state file reads as `None` and simply produces no update;
an unwritable state path is reported to stderr and skipped; `out.flush()`
failures (closed pipe, `StringIO`) are swallowed.

## Geometry

At `startup.sh`'s real xterm (1920x1080 / font 14 => 160x44) the roundtable gives
each tile a 31x20 pane; the frame renders 9 rows at 31 columns. Verified with
`tmux list-panes`, not arithmetic — see the pitfalls in
`config/layouts/roundtable.yaml`.

## Changelog

- **v1.5** — Removed the redundant name row inside the frame: the character
  name already appears on the pane's own tmux top border
  (`build_layout.py`'s `_resolve_tile_title` + `select-pane -T`), so a tile
  showed its name twice. The frame now starts straight into the AVATAR
  subpanel; `TILE_FIXED_OVERHEAD` dropped by 2 rows accordingly.
- **v1.4** — Three visually distinct subpanels (avatar / text / status), each
  behind its own divider; avatar grew from 2 to 3 rows; the TEXT subpanel now
  sizes itself from the pane's real detected height
  (`resolve_dialogue_line_count`) instead of a fixed 2 lines, so it actually
  "takes up the rest of the tuber panel". Pane titles resolve through a new
  `roster:` key in the worker config (`build_layout.py`'s `_resolve_tile_title`)
  instead of showing the raw slot id — `Game Master` for the GM, the real
  character name for a cast slot, `Offline` for an uncast one. Every ACTIVE
  tile now renders in one shared light-blue (`colour117`) instead of a
  per-slot rainbow; uncast tiles render grey (`colour240`). The tmux status
  bar's default `[worker] 0:python3*` segment is replaced with a static
  `status_label` (layout-level, cosmetic only — the session keeps its real
  name).
- **v1.3** — Show log column removed (it left the grid too narrow to read —
  the tuber_1/tuber_5 regression); the character grid is now a full-screen
  even 4x2 tile grid.
- **v1.2** — Avatar now persists for the whole show via `TileRenderer`; tiles
  show the last 2 spoken lines; faces added for every Performer expression; ANSI
  stripped before clipping. Layout: `System`/htop strip removed, show log moved
  to a full-height 20% left column with the character grid on the right 80%.
- **v1.0** — Initial tile: face + single current line + status.
