# audio_player

## Overview

Best-effort, non-blocking WAV playback for replay narration. Inside a
worker container, `paplay` plays into the PulseAudio sink (`PULSE_SINK=vout`
in the Dockerfile) that `stream_supervisor.py`'s ffmpeg command captures
via `-f pulse -i vout.monitor` (docs/stream_supervisor.md) when Pulse is
up — so anything played here is what the Twitch stream hears. On a dev box
it falls back to `ffplay`, then `aplay`, then to silence.

The contract is the same as avatar-state writes in `replay.py`: **audio
must never take the show down.** No player installed, a spawn failure, a
player crash — all yield a playback object whose waits return immediately,
so the performer's timing loop has no special cases.

## Signature

```python
def play_wav(path, out=sys.stderr) -> Playback

class Playback:
    running: bool
    def wait(self, timeout: float | None = None) -> None
    def stop(self) -> None

def wait_extra(playback, started_at: float, min_seconds: float) -> None
```

## Parameters

- `play_wav(path, out)`: `path` — the WAV to play; `out` — where soft
  failures are noted (defaults to stderr).
- `wait_extra(playback, started_at, min_seconds)`: hold until
  `min_seconds` have elapsed since `started_at` (a `time.monotonic()`
  stamp) **and** playback has finished — used when a scene's visuals finish
  before its spoken line does, with a 10s grace cap on a line running long.

## Return Value

`play_wav` always returns a `Playback` — real (wrapping the player
subprocess) or silent/already-finished on any failure.

## Dependencies

Standard library only. Uses whichever of `paplay` / `ffplay` / `aplay` is
on PATH (the worker image has the first two via `pulseaudio-utils` and
`ffmpeg`).

## Usage Examples

```python
from audio_player import play_wav, wait_extra
import time

playback = play_wav(scene_audio.audio_path)
started = time.monotonic()
render_the_scene()                                # visuals, paced
wait_extra(playback, started, scene_audio.duration)  # let the voice land
```

Stopping early (e.g. show interrupted):

```python
playback = play_wav("scene.wav")
...
playback.stop()   # safe even if already finished / silent
```

## Error Handling

Nothing raises. Missing players and spawn failures return a silent
`Playback` and note it on `out`; `stop()` swallows kill races; `wait()`
with a timeout kills a hung player rather than blocking the show.

## Changelog

- **v1.0.0** (2026-07-12): Initial version — paplay/ffplay/aplay fallback
  chain, silent-playback degradation, scene-hold helper. 5 tests.
