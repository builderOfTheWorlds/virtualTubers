# composite_on_background / parse_background

## Overview

The codec avatar is **not** drawn into the tmux character grid — it is its
own borderless X window that `CodecAvatarProvider` positions on top of the
console (see `app/avatar_providers/codec_avatar.py`'s module docstring).
The renderer clears to black and only paints the head, so every un-drawn
pixel in that window used to appear on stream as a hard black rectangle
punched through the layout — clearly visible in the 2026-09-22 screenshot
that prompted this change.

`composite_on_background` blends the finished frame over a flat colour —
by default `CONSOLE_BG`, which is exactly the `-bg '#002b36'` that
`startup.sh` launches xterm with — so the window's edges disappear into
the terminal and the face reads as floating in the console.

`parse_background` is the config-facing half: it turns whatever a worker
YAML holds (`"#002b36"`, `"none"`, `[0, 43, 54]`, …) into the concrete
value `composite_on_background` wants, and never raises.

### Why a screen blend, not an alpha key

The render pipeline has no alpha channel, and `apply_codec_screen`
deliberately bleeds CRT glow, vignette and grain into the surround. A hard
"black means transparent" key would clip that halo into a visible
rectangular seam — the exact artefact being removed. The screen blend
`1 - (1 - bg) * (1 - fg)` instead maps `fg = 0` to *exactly* the
background colour, leaves bright face pixels essentially untouched
(`0.85 -> <0.88`), and lets the bloom fall off smoothly into the grey.

### Ordering

Compositing runs **after** `apply_codec_screen`, never before: the
scanline and vignette passes multiply the frame down towards black, so a
background applied first would be darkened out of sync with the console it
is supposed to match.

## Signature

```python
def composite_on_background(rgb: np.ndarray,
                            background: np.ndarray | None = CONSOLE_BG
                            ) -> np.ndarray: ...

def parse_background(value: str | Sequence[float] | None,
                     default: np.ndarray | None = CONSOLE_BG
                     ) -> np.ndarray | None: ...
```

## Parameters

### `composite_on_background`

| Name | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `rgb` | `np.ndarray` | yes | — | `(H, W, 3)` float array in `0..1`; a rendered, post-processed frame |
| `background` | `np.ndarray \| None` | no | `CONSOLE_BG` | `(3,)` float in `0..1`. `None` skips compositing entirely (returns the input unchanged) |

### `parse_background`

| Name | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `value` | `str \| Sequence[float] \| None` | yes | — | See accepted forms below |
| `default` | `np.ndarray \| None` | no | `CONSOLE_BG` | Returned for `None` input and for any unparseable input |

Accepted `value` forms:

- `None` → `default`
- `"none"` / `"off"` / `"transparent"` (case/space insensitive) → `None`, i.e. disable compositing
- `"black"` → `[0, 0, 0]` (composites onto black — visually a no-op, but distinct from `none`)
- `"#002b36"`, `"002b36"`, `"#fff"` → the parsed colour
- `[0.17, 0.17, 0.17]` → used as-is
- `[0, 43, 54]` → any channel `> 1.0` is treated as `0..255` and scaled
- anything else → a warning is logged and `default` is returned

## Return Value

`composite_on_background` returns an `(H, W, 3)` float array in `0..1`,
same shape and dtype contract as its input — so `write_png` and the
provider's `uint8` conversion work on it unchanged.

`parse_background` returns a `(3,)` float32 array in `0..1`, or `None`
meaning "do not composite".

## Dependencies

- `numpy`
- `pixel_raster.CONSOLE_BG` — the console grey constant, kept in sync by hand with `startup.sh`'s xterm `-bg`
- Called by `avatar_providers.codec_avatar.FrameSource.render_frame`

## Usage Examples

### 1. In the render path (what `FrameSource` actually does)

```python
from pixel_raster import apply_codec_screen, composite_on_background

img, backend = gl_raster.render_with_fallback(verts, faces, materials, ...)
img = apply_codec_screen(img)                        # CRT pass first
img = composite_on_background(img, self.background)  # then blend to console
```

### 2. Resolving a worker config value

```python
from pixel_raster import parse_background

cfg = {"background": "#002b36"}      # from config/workers/<role>.yaml
background = parse_background(cfg.get("background"))
source = FrameSource("chadwick", width=576, height=432, background=background)
```

Worker YAML:

```yaml
avatar:
  provider: codec_avatar
  codec_avatar:
    character_params: chadwick
    background: "#002b36"   # or `none` for the old black surround
```

## Error Handling

Neither function raises for bad input — both are cosmetic and must never
take down an avatar pane:

- `composite_on_background` will propagate a `ValueError` from numpy only if `rgb` is not broadcast-compatible with a `(1,1,3)` colour, i.e. a genuine programming error in the render path, not a config mistake.
- `parse_background` catches **everything** (bad hex, wrong channel count, non-numeric sequences, mappings) and falls back to `default`, logging a `WARNING` with the offending value.

## Changelog

- **v1.0.0** (2026-09-22) — Initial version. Added alongside the avatar UI
  pass that narrows the pane window to half the detected pane width and
  blends its background into the console grey.
