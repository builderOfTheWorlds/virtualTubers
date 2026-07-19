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

**Credential security.** Stream keys and RTMP URLs are redacted in all
supervisor log output (`redact_stream_key()`) so they never reach Postgres
via `log-shipper`. ffmpeg's stdout/stderr is also suppressed, preventing
its startup output (which includes the full command line with credentials)
from being logged to container logs.

## Signature

```python
def resolve(env_name, config_value, default=None) -> str
def log(msg: str) -> None
def redact_stream_key(text: str) -> str
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

- v1.2.0 (2026-07-18) — Security fix: added `redact_stream_key()` to mask
  Twitch credentials (format `live_XXXX`) in supervisor log messages so they
  never reach Postgres via `log-shipper`. Also suppressed ffmpeg's
  stdout/stderr to prevent its startup output (which logs the full command
  including the stream key) from being captured in container logs. Credential
  redaction now matches the pattern already used in `session_log_parser.py`.
- v1.1.2 (2026-07-12) — Fixed the third layer of the same bug: even after
  the `pulse-access` group fix (v1.1.1) let `pactl` connect, the null-sink
  load still failed ("Module initialization failed") because
  `startup.sh` started PulseAudio with `--disallow-module-loading` — a
  flag that rejects exactly the kind of runtime `pactl load-module` call
  needed to create the `vout` sink, made one line later in the same
  script. Removed the flag; `--disallow-exit` is kept.
- v1.1.1 (2026-07-12) — Fixed the actual reason `pulse_monitor_available`
  (added in v1.1.0, same day) kept returning false even after that fix
  deployed: PulseAudio's `--system` mode (`startup.sh`) gates every client
  connection — `pactl`, `paplay`, ffmpeg's `-f pulse` input — on membership
  in the `pulse-access` group, and nothing in the image ever added the
  container's `root` user to it. Every Pulse client got a silent "Access
  denied": `startup.sh`'s null-sink creation (masked by `2>/dev/null ||
  true`), `audio_player.py`'s `paplay` (masked by `DEVNULL`), and this
  module's own probe all failed quietly. Fixed with `RUN usermod -aG
  pulse-access root` in the Dockerfile; `startup.sh`'s sink creation now
  also logs success/failure instead of swallowing it, so this class of
  problem is visible in `docker logs` next time instead of requiring a
  multi-step trace from "no audio" down to a group membership.
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
