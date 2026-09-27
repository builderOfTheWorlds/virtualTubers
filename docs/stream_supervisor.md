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

**Local kill switch / emergency stop.** `WorkerControl.is_enabled` returns
`False` whenever the container's kill file (`WORKER_KILL_FILE`, default
`/tmp/worker_disabled`) exists, without consulting Redis
(docs/worker_control.md), so the normal poll stops ffmpeg even with Redis
down. To make that prompt, the wait between polls is sliced into
`KILL_CHECK_INTERVAL_S = 0.5` checks: if ffmpeg is running and the kill file
appears, the supervisor wakes early and stops it (≤0.5s, never more than one
poll). `SIGUSR1` means "stop streaming now": the handler writes the kill file
(reason `SIGUSR1`) and wakes the loop; if the file can't be written it logs
`ERROR event=kill_file_write_failed` and falls back to an in-memory
force-off that lasts until the process restarts. An override-driven stop is
logged as `WARN event=stop_ffmpeg reason=local_override worker_id=...
kill_file=...`, and a supervisor that starts with the file present logs
`WARN event=local_override_active_at_startup` and never starts ffmpeg. While
the override is in force ffmpeg is not restarted if it had exited.

```bash
docker exec <worker-container> pkill -USR1 -f /app/stream_supervisor.py
```

(Only send `SIGUSR1` to a supervisor built from this version or later —
older ones have no handler, and SIGUSR1's default action terminates the
supervisor and with it the container. `scripts/emergency_stop.sh` therefore
uses the kill file, not the signal.)

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
def supervise_step(control: WorkerControl, worker_id: str, proc, ffmpeg_cmd: list[str],
                   force_off: bool = False) -> subprocess.Popen | None
def wait_for_next_poll(control, proc, should_wake: Callable[[], bool], sleep=time.sleep,
                       interval=POLL_INTERVAL_S, slice_s=KILL_CHECK_INTERVAL_S) -> bool
def make_emergency_stop_handler(control: WorkerControl, state: dict) -> Callable  # SIGUSR1
def main() -> None
```

CLI: `stream_supervisor.py --config PATH --rtmp-url URL --stream-key KEY --resolution WxH --display :N`

## Parameters

- `--config` (str, default `/config/worker.yaml`) — worker config path, loaded via `message_bus.load_worker_config` to resolve `worker_id` and the Redis URL.
- `--rtmp-url`, `--stream-key`, `--resolution`, `--display` (str, all required) — same values `startup.sh` already resolves from env (`STREAM_RTMP_URL`, `STREAM_KEY`, `RESOLUTION`, `DISPLAY`); passed through unchanged into the ffmpeg command.
- `enabled` (bool) / `proc_running` (bool) — inputs to `decide_action`.

- `force_off` (bool) — `supervise_step`'s in-memory override (set by the SIGUSR1 handler when the kill file can't be written).
- `should_wake` (callable) — `wait_for_next_poll` returns early when it's truthy (SIGUSR1/SIGTERM set it).

Poll interval is fixed at `POLL_INTERVAL_S = 3` seconds (kill file checked every `KILL_CHECK_INTERVAL_S = 0.5` s in between); stop grace period at `STOP_TIMEOUT_S = 10` seconds before escalating from `SIGTERM` to `SIGKILL`.

## Return Value

- `decide_action` — `"start"` (enabled, no process running), `"stop"` (disabled, process running), or `"noop"` otherwise.
- `supervise_step` — the ffmpeg process after this poll (new `Popen`, the same one, or `None`).
- `wait_for_next_poll` — `True` if it woke early, `False` after the full interval.
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

- Redis unreachable — `WorkerControl.is_enabled` fails open (treats the worker as enabled), so a control-plane outage keeps the stream running rather than stopping it — unless the local kill file exists, which always wins.
- `SIGUSR1` — write the kill file and stop ffmpeg now; on `OSError` fall back to an in-memory force-off (logged at ERROR).
- ffmpeg exits unexpectedly while still enabled — logged, treated as "no process running" next poll, restarted.
- `SIGTERM`/`SIGINT` — stop the poll loop and terminate any running ffmpeg child (`SIGTERM`, escalating to `SIGKILL` after `STOP_TIMEOUT_S`) before exiting, so `docker stop`/container recreation still works normally.
- `pulse_monitor_available` — any error (Pulse down, `pactl` missing/timeout, non-zero exit) is treated as "not available"; it never raises, it only decides which audio input `build_ffmpeg_cmd` picks.

## Changelog

- v1.3.0 (2026-09-27) — Emergency stop that works with Redis down: honours
  WorkerControl's local kill file (checked every 0.5s between polls), SIGUSR1
  writes it and stops ffmpeg immediately, WARN-level structured log lines for
  override-driven stops. Main loop split into `supervise_step` /
  `wait_for_next_poll` / `make_emergency_stop_handler` for testability. +9 tests.
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
