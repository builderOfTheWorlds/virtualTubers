# app/stream_supervisor.py

## Overview

Runs and supervises the ffmpeg broadcaster as a child process, starting or
stopping it based on this worker's on/off flag (`worker_control.WorkerControl`).
Replaces the raw foreground `ffmpeg ...` command that used to sit at the end
of `startup.sh`: killing that directly would have exited the whole container
(the cleanup line right after it kills the agent/xterm/Xvfb PIDs too), so
making "disable" actually stop the Twitch stream — rather than just pausing
the agent — needed something long-lived that can stop/restart ffmpeg *without*
the container exiting. This script is that process; `startup.sh` now runs it
in ffmpeg's old place.

As a side effect of the same poll loop, if ffmpeg exits on its own (e.g. an
RTMP hiccup exhausts its `-reconnect` budget) while the worker is still
enabled, the supervisor notices and restarts it.

**Audio input.** `build_ffmpeg_cmd` captures the `vout` PulseAudio null
sink's monitor (`-f pulse -i vout.monitor`) when `pulse_monitor_available()`
finds it — that's the same sink `app/audio_player.py`'s `paplay` plays
Rerun Theater's spoken narration into (docs/audio_player.md), so this is
the link that actually gets narration onto the stream. If Pulse isn't up
(startup hiccup, `pactl` missing, etc.) it falls back to a synthesized
silent track (`-f lavfi -i anullsrc=...`) so the *video* broadcast never
fails over an audio-only problem — same soft-degradation contract as the
rest of the voice pipeline (an episode always airs, at worst muted).

## Signature

```python
def resolve(env_name, config_value, default=None) -> str
def pulse_monitor_available(sink="vout") -> bool
def build_ffmpeg_cmd(rtmp_url, stream_key, resolution, display) -> list[str]
def decide_action(enabled: bool, proc_running: bool) -> "start" | "stop" | "noop"
def stop_process(proc: subprocess.Popen) -> None
def main() -> None
```

CLI: `stream_supervisor.py --config PATH --rtmp-url URL --stream-key KEY --resolution WxH --display :N`

## Parameters

- `--config` (str, default `/config/worker.yaml`) — worker config path, loaded via `message_bus.load_worker_config` to resolve `worker_id` and the Redis URL.
- `--rtmp-url`, `--stream-key`, `--resolution`, `--display` (str, all required) — same values `startup.sh` already resolves from env (`STREAM_RTMP_URL`, `STREAM_KEY`, `RESOLUTION`, `DISPLAY`); passed through unchanged into the ffmpeg command.
- `enabled` (bool) / `proc_running` (bool) — inputs to `decide_action`.

Poll interval is fixed at `POLL_INTERVAL_S = 3` seconds; stop grace period at `STOP_TIMEOUT_S = 10` seconds before escalating from `SIGTERM` to `SIGKILL`.

## Return Value

- `decide_action` — `"start"` (enabled, no process running), `"stop"` (disabled, process running), or `"noop"` otherwise.
- `main` — blocks until `SIGTERM`/`SIGINT`, then stops any running ffmpeg child and returns.

## Dependencies

- `message_bus.load_worker_config` (`app/message_bus.py`)
- `worker_control.WorkerControl` (`app/worker_control.py`, docs/worker_control.md)
- `pactl` (pulseaudio-utils, already in the worker image) — probed by
  `pulse_monitor_available`, never required to be installed for the
  broadcaster to run (its absence just forces the silent-audio fallback)
- Python stdlib `subprocess`, `signal`

## Usage Examples

```bash
python3 /app/stream_supervisor.py \
    --config /config/worker.yaml \
    --rtmp-url rtmp://live.twitch.tv/app \
    --stream-key live_xxxxxxxx \
    --resolution 1920x1080 \
    --display :99
```

```python
# the decision table in isolation (tests/test_stream_supervisor.py)
from stream_supervisor import decide_action
assert decide_action(enabled=False, proc_running=True) == "stop"
```

## Error Handling

- Redis unreachable — `WorkerControl.is_enabled` fails open (treats the worker as enabled), so a control-plane outage keeps the stream running rather than stopping it.
- ffmpeg exits unexpectedly while still enabled — logged, treated as "no process running" next poll, restarted.
- `SIGTERM`/`SIGINT` — stop the poll loop and terminate any running ffmpeg child (`SIGTERM`, escalating to `SIGKILL` after `STOP_TIMEOUT_S`) before exiting, so `docker stop`/container recreation still works normally.
- `pulse_monitor_available` — any error (Pulse down, `pactl` missing/timeout, non-zero exit) is treated as "not available"; it never raises, it only decides which audio input `build_ffmpeg_cmd` picks.

## Changelog

- v1.1.0 (2026-07-12) — Fixed a real bug: `build_ffmpeg_cmd`'s audio input
  was hardcoded to `anullsrc` (synthesized silence) regardless of whether
  Rerun Theater's spoken narration was configured — `audio_player.py`
  played into the `vout` Pulse sink, but ffmpeg never captured it, so no
  voice could ever reach the stream no matter how correctly everything
  upstream (TTS, config, voice models) was wired. Added
  `pulse_monitor_available` and switched the audio input to
  `-f pulse -i vout.monitor` when it's actually up, falling back to
  `anullsrc` otherwise so the video broadcast still never fails over an
  audio-only issue. +6 tests.
- v1.0.0 (2026-07-07) — Initial version.
