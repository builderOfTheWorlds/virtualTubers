# window_shape

## Overview
`app/window_shape.py` makes the roundtable's 3D tile heads float on the
console instead of sitting in a solid square. It uses the X **SHAPE**
extension to trim each head's pygame window to the outline of the rendered
head. Pixels outside that outline are no longer part of the window, so the
xterm underneath shows through in whatever console theme is live.

Why SHAPE and not alpha: the stream captures Xvfb with ffmpeg x11grab, and
there is no compositor. An ARGB window would be painted opaque, so the
square would still show. SHAPE is enforced by the X server itself. The
trade-off is a hard 1-bit edge instead of a soft one, which the CRT
scanline pass hides at tile size.

## Signature
```python
silhouette_mask(rgb, threshold=SILHOUETTE_THRESHOLD) -> np.ndarray  # (H,W) bool
pack_bitmap(mask) -> bytes
class ShapeMasker:
    @classmethod
    def create(cls, window_id) -> "ShapeMasker | None"
    def apply(self, mask) -> None
```

## Parameters
- `rgb`: (H,W,3) float frame in 0..1, **not** composited onto a background
  (the surround must stay black).
- `threshold`: brightest-channel cutoff for "subject" pixels (default 0.08).
- `window_id`: X window id, taken from `pygame.display.get_wm_info()["window"]`.
- `mask`: (H,W) bool, the same size as the window.

## Return Value
- `silhouette_mask`: a bool mask. Each row is filled solid between its first
  and last subject pixel, so dark pupils and the mouth don't punch holes.
- `create`: a masker, or `None` when libX11/libXext, the display, or SHAPE
  is unavailable.
- `apply`: nothing. It skips the X call when the mask hasn't changed.

## Dependencies
numpy, ctypes (libX11.so.6, libXext.so.6). It is wired in through
`CodecAvatarProvider` (`codec_avatar.transparent: true`) and
`tile_avatar.TILE_AVATAR_TRANSPARENT` (on by default for roundtable tiles).

## Usage Examples
```python
# worker YAML: opt a solo codec avatar into transparency
avatar:
  provider: codec_avatar
  codec_avatar:
    transparent: true
    background: "#002b36"   # used only if SHAPE is unavailable
```
```python
masker = ShapeMasker.create(pygame.display.get_wm_info()["window"])
if masker:
    masker.apply(silhouette_mask(frame))
```

## Error Handling
Nothing in this module raises to the caller. If `create()` returns `None`,
or `apply()` throws, `CodecAvatarProvider._apply_transparency` logs one
warning and goes back to compositing onto the flat `background` colour.

## Changelog
- v1.0.0 (2026-10-02): Initial version. Transparent roundtable tile heads.
