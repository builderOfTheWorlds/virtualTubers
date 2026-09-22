# GPU-accelerated codec avatar rendering: status + benchmark

Written 2026-09-22, following on from docs/character_generator.md's codec
pixel pipeline (codec_head.py + pixel_raster.py). Answers two questions
raised while reviewing codec_final.png: does the numpy renderer hit a
usable framerate for LIVE (per-frame, not pre-rendered) rendering, and can
this run without an X server / GLX extension.

## TL;DR

- `pixel_raster.render()` (pure numpy): **~12-14 fps** at pane resolution
  (560x700) — below a 30fps stream target, confirmed again in
  `tests/test_gl_raster.py`'s realtime-floor comparison.
- `gl_raster.render()` (GPU via `moderngl`, `app/gl_raster.py`): **~290 fps**
  at the same resolution/geometry (full per-call cost: buffer
  alloc/upload/render/readback/release, not an optimistic reused-VAO
  number) — measured on this dev machine's RTX 3080.
- `moderngl.create_context(standalone=True)` needs **no window, no X
  server, and no `Xvfb ... +extension GLX` flag** — it opens a headless
  EGL/GL context directly against the driver. Confirmed on Windows with no
  display server running at all.
- **This dev machine's GPU is for local testing only.** Per explicit
  decision, the worker container fleet does not get this GPU or its
  passthrough — see "Open question: worker container GPU" below.

## Why this was worth checking

`app/character_preview.py`'s CLI previously called `pixel_raster.render()`
directly. That's fine for an agent iterating on sliders (a few frames on
demand), but docs/avatar_3d_design.md's live-rendering goal — the avatar
pane re-rendering every tick, matching the stream's 30fps — needs a
render call fast enough to never be the bottleneck. 12-14fps isn't.

## What changed

- **`app/gl_raster.py`** — GPU counterpart to `pixel_raster.py`. Same
  camera/view matrices (imported directly, not duplicated), same
  `KEY_LIGHT`/`AMBIENT`/`CODEC_PALETTE` constants, same flat-per-triangle
  shading model — ported into a GLSL vertex+fragment shader pair instead of
  a numpy scanline fill. `render()` has the identical signature and output
  contract as `pixel_raster.render()` ((H,W,3) float array, 0..1), so
  `apply_codec_screen()`/`write_png()` work unchanged on its output.
- **`is_available()` / `render_with_fallback()`** — the entry point actual
  callers should use. Caches whether a GL context can be created at all
  (any driver/GPU failure → `False`, logged once) and falls back to
  `pixel_raster.render()` transparently. A GPU-less box (CI, a worker
  without passthrough) degrades to the slower path instead of crashing.
- **`app/avatar_providers/codec_avatar.py`** — new provider for LIVE
  rendering. Split into `FrameSource` (pure: params in, frame out, no
  display — unit-testable headless) and `CodecAvatarProvider` (thin
  `AvatarProvider` adapter that owns a small undecorated pygame window,
  blitting `FrameSource`'s frames into it every tick). Registered as
  `codec_avatar` in `avatar_providers/__init__.py`'s `PROVIDERS` map —
  **not yet the default for any worker**; `termgl_avatar`/`coder.yaml`'s
  current wiring is untouched.
- **`app/character_preview.py`** now renders through
  `gl_raster.render_with_fallback()` by default (reports which backend ran
  and at what fps per frame), with a `--cpu` flag to force the old numpy
  path for comparison/debugging. Output is PNG files under `preview_out/`
  (gitignored) instead of ASCII text — see docs/character_generator.md's
  "Quick start" for the current CLI shape.
- **`requirements.txt`** gained `moderngl` and `pygame`. Both are optional
  at runtime in the sense that `gl_raster.is_available()` degrades
  gracefully without a working GL context, but they're installed
  unconditionally (not import-guarded at the requirements level) since
  neither pulls in anything heavy.

## Why a separate window instead of drawing into the tmux pane

Per docs/character_generator.md's earlier finding: `stream_supervisor.py`
captures the entire X **display** as pixels (`ffmpeg -f x11grab -i :99`),
not the tmux pane's text. tmux/xterm is just what currently happens to be
drawn there. `codec_head`/`pixel_raster`/`gl_raster` output real pixel
frames, not termgl's character-cell drawing — so `codec_avatar.py` opens
its own small undecorated window (`pygame.NOFRAME`, `SDL_VIDEO_WINDOW_POS`)
positioned over the avatar region instead. Any window there is captured;
which toolkit drew its pixels doesn't matter to ffmpeg.

## Open question: worker container GPU

**Explicitly out of scope for now.** This dev machine's RTX 3080 is used
for local testing/benchmarking only — it is not being passed through to
any worker container, and the worker fleet does not currently have a GPU
provisioned. `codec_avatar.py`'s `render_with_fallback()` means a
GPU-less worker still renders (at `pixel_raster.py`'s ~12-14fps ceiling)
rather than crashing; it just won't hit the fps this doc benchmarks until
a GPU is actually provisioned for a target deploy environment, at which
point:

- Confirm `moderngl.create_context(standalone=True)` behaves the same way
  inside the actual Ubuntu 22.04 worker image with EGL against whatever
  driver that environment exposes (Xvfb's `+extension GLX` flag may no
  longer be needed at all — untested inside the real container).
  Software-rendered EGL (llvmpipe) is also an option if a real GPU is
  never provisioned there, at roughly `pixel_raster.py`-class performance
  or worse; not benchmarked here.
- Add whatever passthrough mechanism applies (device plugin / `--gpus`
  equivalent) at the actual deploy layer for that environment — nothing
  in this repo assumes a specific one.

## Verifying the numbers yourself

```bash
source .venv/Scripts/activate
python -c "
import sys; sys.path.insert(0, 'app')
from codec_head import build_codec_head
from gl_raster import render as gpu_render, is_available
import time
print('GPU available:', is_available())
v, f, m = build_codec_head('chadwick')
gpu_render(v, f, m, width=560, height=700)  # warmup
t0 = time.perf_counter()
for i in range(200):
    gpu_render(v, f, m, width=560, height=700, rot_y=i * 0.01)
print(f'{200 / (time.perf_counter() - t0):.1f} fps')
"
```

Or via the CLI: `python app/character_preview.py --preset chadwick --view
front` (GPU, default) vs `--cpu` (forces `pixel_raster.py`) — both print
their per-frame timing and which backend rendered it.

## Tests

`tests/test_gl_raster.py` and `tests/test_codec_avatar.py` — includes a
realtime-floor assertion (`fps > 30` at pane resolution) so a future
regression in the shader/render path fails the suite rather than only
showing up as a choppy stream.
