"""
stream_recorder.py
Saves a replay airing to disk by recording the exact encoded stream this
worker's broadcaster already sends to the RTMP endpoint (docs/stream_recorder.md).

How it's wired:
  * stream_supervisor.build_ffmpeg_cmd(record_tap_url=...) adds one extra
    `tee` leg to the broadcaster: `[f=mpegts:onfail=ignore]udp://127.0.0.1:<port>`.
    It's a copy of the already-encoded packets (no second encode), and UDP
    to a port nobody listens on simply drops — so the live broadcast is
    unaffected whether or not a recording is running, and a recorder
    failing can never take the Twitch leg down (onfail=ignore on the tap
    leg only, same rule as the local-preview leg).
  * During a recorded airing, replay_pane wraps the performance in
    `recording(...)`, which starts `ffmpeg -i udp://... -c copy` into
    <RECORDINGS_DIR>/<recording_id>/<worker_id>.mp4 and stops it (SIGINT,
    so the file is finalized) when the show ends or is stopped.

Output is fragmented MP4 (frag_keyframe+empty_moov), so even a recorder
that is SIGKILLed or hits the size cap leaves a playable file. `-fs
<max_bytes>` is the per-stream hard cap message-api computed from the 5 GB
budget (app/recording_budget.py) — the estimate is the admission check,
`-fs` is the guarantee.

The recorder only runs inside a directory message-api reserved
(`<id>/.reservation.json`) — no reservation, no recording — and always
drops a `.<worker>.done` marker when it's finished (or failed), which is
what releases its reservation back to the budget (app/recording_budget.py).

A recording problem must never stop a show from airing: every failure here
is logged and the airing continues unrecorded.
"""
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from recording_budget import (
    done_marker_name,
    read_reservation,
    resolve_recordings_dir,
    sanitize_recording_id,
)

RECORDING_TAP_URL_ENV = "RECORDING_TAP_URL"
DEFAULT_RECORDING_TAP_URL = "udp://127.0.0.1:23000"
#: Wait this long for the first bytes before declaring the recorder
#: failed. The tap carries a keyframe every 2s (-g 60 @ 30fps) and ffmpeg
#: probes ~3s before writing the header.
START_WAIT_S = 8.0
START_POLL_S = 0.25
STOP_TIMEOUT_S = 10.0
#: The recorder exits on its own if the tap goes silent this long (the
#: broadcaster is down) instead of hanging forever.
TAP_TIMEOUT_US = 10_000_000
#: Kernel-side UDP receive FIFO (in 188-byte TS packets' worth of bytes as
#: ffmpeg counts them). ~37 MB of headroom so a slow disk write doesn't
#: drop packets.
UDP_FIFO_SIZE = 200_000
#: `-fs` is checked between written fragments, and a fragment is one GOP
#: (2s ≈ 1.2 MB at the stream bitrate), so ffmpeg overshoots its limit by up
#: to a fragment (measured: +0.4 MB on a 3 MB cap). Stop this far short so
#: the file on disk never exceeds the reserved `max_bytes`.
FS_MARGIN_BYTES = 3_000_000


def effective_fs_limit(max_bytes):
    """-fs value that keeps the finished file <= max_bytes."""
    max_bytes = int(max_bytes)
    return max(1, max_bytes - min(FS_MARGIN_BYTES, max_bytes // 2))


def log(msg):
    print(f"[stream_recorder] {msg}", flush=True)


def resolve_tap_url():
    return os.environ.get(RECORDING_TAP_URL_ENV) or DEFAULT_RECORDING_TAP_URL


def parse_record_request(record):
    """Validate a replay_request's `record` payload.

    Returns (recording_id, max_bytes) or None when the payload is not a
    usable recording request. `record` must be a dict with a non-empty
    `recording_id` (sanitized to one safe path component) and a positive
    integer `max_bytes` — a request without an explicit size cap is
    rejected, so nothing can ever record unbounded.
    """
    if not isinstance(record, dict):
        return None
    recording_id = sanitize_recording_id(record.get("recording_id"))
    try:
        max_bytes = int(record.get("max_bytes"))
    except (TypeError, ValueError):
        return None
    if not recording_id or max_bytes <= 0:
        return None
    return recording_id, max_bytes


def recording_path(recording_id, worker_id, root=None):
    root = Path(root or resolve_recordings_dir())
    name = sanitize_recording_id(worker_id) or "worker"
    return root / recording_id / f"{name}.mp4"


def build_recorder_cmd(tap_url, output_path, max_bytes):
    """ffmpeg command that stream-copies the tap into a fragmented MP4.

    `-map 0:v -map 0:a?` — video required, audio optional (the broadcaster
    always has audio, but a recording should never fail over it).
    `aac_adtstoasc` is required: MPEG-TS carries ADTS AAC, which the MP4
    muxer refuses (verified on the worker image's ffmpeg 4.4).
    """
    source = (f"{tap_url}?fifo_size={UDP_FIFO_SIZE}&overrun_nonfatal=1"
              f"&timeout={TAP_TIMEOUT_US}")
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-analyzeduration", "3000000",
        "-i", source,
        "-map", "0:v", "-map", "0:a?",
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-fs", str(effective_fs_limit(max_bytes)),
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        str(output_path),
    ]


def _file_size(path):
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


class StreamRecorder:
    """One recorder process for one airing on this worker."""

    def __init__(self, recording_id, max_bytes, worker_id, tap_url=None,
                 root=None, popen=subprocess.Popen, sleep=time.sleep,
                 clock=time.monotonic):
        self.recording_id = recording_id
        self.max_bytes = int(max_bytes)
        self.worker_id = worker_id
        self.tap_url = tap_url or resolve_tap_url()
        self.path = recording_path(recording_id, worker_id, root)
        self.done_path = self.path.parent / done_marker_name(worker_id)
        self._popen = popen
        self._sleep = sleep
        self._clock = clock
        self.proc = None
        self._started_at = None
        # stderr goes to a file, not a PIPE: nobody drains a pipe during a
        # 30-minute airing, and a full pipe would block ffmpeg mid-recording.
        # /tmp, not the recordings dir, so it never counts against the budget.
        self.stderr_path = Path(os.environ.get("TMPDIR") or "/tmp") / (
            f"stream_recorder_{recording_id}_{sanitize_recording_id(worker_id) or 'worker'}.log")
        self._stderr_file = None

    def start(self):
        """Start recording. Returns True when bytes are flowing to disk,
        False (and cleans up) when the recorder couldn't start."""
        log(f"event=start recording_id={self.recording_id} worker_id={self.worker_id} "
            f"path={self.path} max_bytes={self.max_bytes}")
        reservation = read_reservation(self.path.parent)
        if reservation is None:
            # message-api creates the directory + reservation on the shared
            # recordings volume. Missing means this worker isn't on that
            # volume (or the id is bogus) — recording here would bypass the
            # storage budget, so don't.
            log(f"ERROR event=no_reservation recording_id={self.recording_id} "
                f"dir={self.path.parent} — is RECORDINGS_DIR the shared recordings "
                f"volume? airing unrecorded")
            return False
        budgeted = [sanitize_recording_id(s) for s in reservation.get("streams") or []]
        if sanitize_recording_id(self.worker_id) not in budgeted:
            # e.g. a duet follower handed the director's `record` — its
            # stream wasn't part of the size estimate, so it can't record.
            log(f"WARN event=stream_not_budgeted recording_id={self.recording_id} "
                f"worker_id={self.worker_id} budgeted={budgeted} — airing unrecorded here")
            return False
        self.max_bytes = min(self.max_bytes,
                             int(reservation.get("max_bytes_per_stream") or self.max_bytes))
        try:
            self._stderr_file = open(self.stderr_path, "wb")
            self.proc = self._popen(
                build_recorder_cmd(self.tap_url, self.path, self.max_bytes),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
            )
        except OSError as exc:
            self._close_stderr()
            log(f"ERROR event=start_failed recording_id={self.recording_id} error={exc!r}")
            self.proc = None
            self.mark_done(0)
            return False
        self._started_at = self._clock()

        deadline = self._clock() + START_WAIT_S
        while self._clock() < deadline:
            if self.proc.poll() is not None:
                self._close_stderr()
                log(f"ERROR event=recorder_exited_early recording_id={self.recording_id} "
                    f"code={self.proc.returncode} stderr={self._stderr_tail()!r}")
                self._discard_if_empty()
                self.proc = None
                self.mark_done(0)
                return False
            if _file_size(self.path) > 0:
                log(f"event=recording recording_id={self.recording_id} worker_id={self.worker_id}")
                return True
            self._sleep(START_POLL_S)
        # Still alive but nothing written yet (slow probe): keep it — it may
        # still catch up — but say so, since a stopped tap looks the same.
        log(f"WARN event=no_bytes_yet recording_id={self.recording_id} "
            f"waited_s={START_WAIT_S} tap={self.tap_url} — is the broadcaster's "
            f"recording tap enabled (RECORDING_TAP_ENABLED)?")
        return True

    def stop(self):
        """Finalize the file (SIGINT -> ffmpeg writes its trailer), then
        report the outcome. Never raises. Returns the final size in bytes."""
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                log(f"WARN event=stop_timeout recording_id={self.recording_id} action=kill")
                proc.kill()
                proc.wait()
            except OSError as exc:
                log(f"ERROR event=stop_failed recording_id={self.recording_id} error={exc!r}")
        self._close_stderr()
        size = _file_size(self.path)
        elapsed = (self._clock() - self._started_at) if self._started_at else 0.0
        if size == 0:
            log(f"ERROR event=empty_recording recording_id={self.recording_id} "
                f"worker_id={self.worker_id} stderr={self._stderr_tail()!r}")
            self._discard_if_empty()
            self.mark_done(0)
            return 0
        # -fs was set a margin below the cap (effective_fs_limit), so
        # reaching that limit is how a truncation shows up.
        if size >= effective_fs_limit(self.max_bytes):
            log(f"WARN event=size_cap_reached recording_id={self.recording_id} "
                f"worker_id={self.worker_id} bytes={size} max_bytes={self.max_bytes} "
                f"— recording truncated, the airing ran longer than estimated")
        log(f"event=saved recording_id={self.recording_id} worker_id={self.worker_id} "
            f"path={self.path} bytes={size} elapsed_s={elapsed:.1f}")
        self.mark_done(size)
        return size

    def mark_done(self, size):
        """Tell the budget this stream is finished (releases its share of
        the reservation). Best-effort: a missing marker only delays the
        release until the reservation expires."""
        try:
            self.done_path.write_text(
                f'{{"worker_id": "{self.worker_id}", "bytes": {int(size)}}}\n',
                encoding="utf-8")
        except OSError as exc:
            log(f"WARN event=done_marker_failed recording_id={self.recording_id} error={exc!r}")

    def _close_stderr(self):
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()
            except OSError:
                pass
            self._stderr_file = None

    def _stderr_tail(self):
        try:
            data = self.stderr_path.read_bytes()
        except OSError:
            return ""
        return data.decode("utf-8", "replace").strip()[-400:]

    def _discard_if_empty(self):
        # The directory itself stays: it holds the reservation and the
        # other streams' files.
        try:
            if self.path.exists() and _file_size(self.path) == 0:
                self.path.unlink()
        except OSError:
            pass


@contextmanager
def recording(record, worker_id, **kwargs):
    """Record the stream for the duration of the `with` block when `record`
    (a replay_request's payload.record) is a valid recording request;
    otherwise a no-op. Yields the StreamRecorder or None. Recording
    failures are logged, never raised — the show goes on either way."""
    parsed = parse_record_request(record)
    if record is not None and parsed is None:
        log(f"ERROR event=invalid_record_request worker_id={worker_id} record={record!r} "
            f"— airing unrecorded")
    if parsed is None:
        yield None
        return
    recording_id, max_bytes = parsed
    recorder = StreamRecorder(recording_id, max_bytes, worker_id, **kwargs)
    started = recorder.start()
    try:
        yield recorder if started else None
    finally:
        if started:
            recorder.stop()
