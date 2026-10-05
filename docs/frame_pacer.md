# FramePacer

## Overview

`app/frame_pacer.py` paces the live 3D avatar render loops on a fixed
deadline schedule. It exists because both avatar loops used to
`render(); sleep(1/fps)`. That pattern adds the render time to every frame,
so the real rate was never the configured one.

Measured on air on 2026-10-05 (30fps x11grab of the live displays):

| Head | Configured | Before | After |
|---|---|---|---|
| Roundtable tile (x8, 200x190) | 12 → 15 fps | ~6 fps live, uneven 2-15 frame holds | 15 fps live (14.7-16.5 measured), max gap 3 frames |
| Solo GM channel head (549x470) | 30 fps | ~9 fps live, 15.75 ticks/s isolated | 29.25 ticks/s isolated, 30 render calls/s live |

## Signature

```python
class FramePacer:
    def __init__(self, fps: float, clock=time.monotonic, sleep=time.sleep): ...
    def wait(self, sleep=None): ...
```

## Parameters

- `fps` (float, required, > 0): target frames per second. `ValueError` if <= 0.
- `clock` (callable, optional): monotonic clock. Injected by tests.
- `sleep` (callable, optional): takes seconds. Pass a
  `threading.Event().wait` so a stop request interrupts the wait.
- `wait(sleep=None)`: per-call sleeper override.

## Return Value

`wait()` returns whatever the sleeper returned (`Event.wait` gives `True`
once the event is set). It returns `None` when the frame overran its slot
and no wait was needed.

## Behaviour

- Keeps an absolute schedule `t0, t0+T, t0+2T, ...` and waits only for the
  time remaining in the current slot.
- When a frame overruns its slot, the schedule re-anchors to now. It does
  not burst-render to catch up, so a hiccup costs one late frame instead of
  a stutter followed by a sprint.

## Dependencies

Standard library only (`time`). Used by `app/avatar.py` (solo channel avatar
loop) and `app/tile_pane.py` (`TileAvatarDriver`, roundtable heads).

## Usage Examples

```python
from frame_pacer import FramePacer

pacer = FramePacer(30)
while True:
    provider.render_tick(expression, bubble_lines)
    pacer.wait()
```

```python
stop = threading.Event()
pacer = FramePacer(15, sleep=stop.wait)
while not stop.is_set():
    head.tick(expr)
    pacer.wait()
```

## Error Handling

- `ValueError`: `fps` is zero, negative, or NaN.

## Related changes (same fix)

- **Roundtable CPU limit 2.0 → 4.0** (`docker-compose.yml`, worker-roundtable
  only). Even with the pacer, the live roundtable held heads at ~7fps with
  up to 15-frame freezes once a show was running. The container was being
  CPU-throttled ~25% of every period (`/sys/fs/cgroup/cpu.stat`
  `throttled_usec`): eight heads, their GPU render processes, Xvfb and
  ffmpeg don't fit in 2 cores. At 4.0, throttling was 0 and all eight heads
  held 14.7-16.5 updates/s with a max gap of 3 frames. Applied live with
  `docker update --cpus 4`; the compose change makes it survive a recreate.
  To check: `docker exec <c> grep throttled /sys/fs/cgroup/cpu.stat`, twice,
  10s apart.
- `pixel_raster.apply_codec_screen` (the CRT pass, run on every live frame)
  now caches the static scanline, vignette and fixed-seed grain maps per
  frame size, runs in float32, and blurs in place. The output is identical
  (max diff ~2e-7) and it is 2-2.5x faster: 8.1 → 3.9 ms at 549x470, and
  1.0 → 0.4 ms at tile size.
- `tile_avatar.TILE_AVATAR_FPS` is 12 → 15. 15 divides the 30fps capture
  evenly. 12 gives an alternating 3,2,3,2 frame hold, which reads as judder
  on head turns.

## Changelog

- v1.0.0 (2026-10-05): initial version.
