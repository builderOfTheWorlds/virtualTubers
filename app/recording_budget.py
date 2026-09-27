"""
recording_budget.py
Storage budget, size estimate and reservations for replay recordings
(docs/recording_budget.md).

A replay airing can be saved to disk (app/stream_recorder.py taps the exact
encoded stream ffmpeg already sends to the RTMP endpoint — no re-encode).
All recordings share ONE hard storage cap (RECORDINGS_MAX_BYTES, default
5 GB). message-api refuses to start a recorded airing whose ESTIMATED size
doesn't fit in what's left of that cap (reserve()), before any replay
request is sent.

How the cap is actually guaranteed (the estimate alone can't be):
  * each admitted recording gets a hard per-stream ffmpeg `-fs` cap
    (estimate x CAP_HEADROOM, never more than its share of what's left);
  * the admission writes `<id>/.reservation.json` holding streams x cap;
    while a recording is live it counts as max(bytes on disk, reserved), so
    a second recorded Play can't be admitted into space the first one may
    still grow into;
  * each worker's recorder drops a `.<worker>.done` marker when it stops;
    once every stream is done (or the reservation expires, for a worker
    that never got the request) the recording counts at its real size.
So the sum of every live recording's caps plus every finished recording's
real size never exceeds the limit.

Why the estimate can be fairly tight: the broadcaster is true CBR
(stream_supervisor.TWITCH_BITRATE_KBPS, nal-hrd=cbr / -rc cbr), so bytes are
~linear in wall-clock seconds. The uncertain part is how long the airing
runs, which is estimated from the same pacing model the performer uses
(replay.estimate_event_seconds via revoice.scene_visual_seconds) plus the
spoken-line length, with a safety margin on top.

No Postgres/Kafka/Redis here — message-api imports it and it's unit-testable
without any services.
"""
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from revoice import WORDS_PER_SECOND, plan_scenes, scene_visual_seconds, target_words

log = logging.getLogger("recording_budget")

RECORDINGS_DIR_ENV = "RECORDINGS_DIR"
DEFAULT_RECORDINGS_DIR = "/data/recordings"
RECORDINGS_MAX_BYTES_ENV = "RECORDINGS_MAX_BYTES"
#: Hard cap for ALL recordings together. Decimal GB (5 * 10**9), the
#: stricter reading of "5GB" — a GiB cap would allow ~7% more.
DEFAULT_RECORDINGS_MAX_BYTES = 5 * 1000 ** 3

#: Must match stream_supervisor.TWITCH_BITRATE_KBPS and its `-b:a` — the
#: recording is a stream copy of that exact encode (asserted by
#: tests/test_recording_budget.py so the two can't drift apart silently).
STREAM_VIDEO_KBPS = 4500
STREAM_AUDIO_KBPS = 128
#: MPEG-TS -> fragmented-MP4 overhead on top of the elementary streams, plus
#: CBR jitter. The spike on the worker image measured ~4.6 Mbit/s of MP4 for
#: a 4.5+0.128 Mbit/s encode, i.e. ~0-2%; 5% is headroom.
CONTAINER_OVERHEAD = 1.05
#: Pacing estimate -> wall clock margin (voice-gate waits, TTS variance,
#: hold times). Deliberately pessimistic: refusing a recording that would
#: have fit is cheaper than truncating one that didn't.
DURATION_SAFETY = 1.25
#: Fixed per-airing time outside the scenes (recorder warm-up, title card,
#: fin banner).
FIXED_OVERHEAD_S = 15.0
MAX_OUTPUT_LINES = 24  # replay.MAX_OUTPUT_LINES — same display cap the performer uses
#: Per-stream hard cap = estimate x this (bounded by the remaining budget):
#: room for the estimate to be wrong without letting one airing hog the cap.
CAP_HEADROOM = 1.5
#: A reservation stops counting after this long even if some stream never
#: reported done (worker down, request lost). Covers voice prep (LLM + TTS
#: before the recorder starts) plus the airing itself, twice over.
RESERVATION_PREP_ALLOWANCE_S = 45 * 60

RESERVATION_FILE = ".reservation.json"
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ── config ───────────────────────────────────────────────────────────────────

def resolve_recordings_dir():
    return os.environ.get(RECORDINGS_DIR_ENV) or DEFAULT_RECORDINGS_DIR


def resolve_max_bytes():
    """RECORDINGS_MAX_BYTES env (int bytes) > 5 GB default. A bad value
    falls back to the default rather than to "unlimited"."""
    raw = os.environ.get(RECORDINGS_MAX_BYTES_ENV)
    if raw:
        try:
            value = int(float(raw))
            if value > 0:
                return value
        except ValueError:
            pass
        log.warning("event=bad_env var=%s value=%r fallback=%d",
                    RECORDINGS_MAX_BYTES_ENV, raw, DEFAULT_RECORDINGS_MAX_BYTES)
    return DEFAULT_RECORDINGS_MAX_BYTES


def sanitize_recording_id(value):
    """Filesystem-safe single path component ([A-Za-z0-9._-], <=128 chars,
    no leading/trailing dot, dash or underscore). Empty string when nothing
    usable is left — callers treat that as invalid."""
    cleaned = _SAFE_ID_RE.sub("_", str(value or "")).strip("._-")
    return cleaned[:128]


def done_marker_name(worker_id):
    return f".{sanitize_recording_id(worker_id) or 'worker'}.done"


def new_recording_id(episode, now=None):
    """<UTC timestamp>_<episode>_<6 hex> — sorts newest-last by name."""
    now = now or datetime.now(timezone.utc)
    stem = sanitize_recording_id(episode)[:80] or "episode"
    return f"{now:%Y%m%dT%H%M%SZ}_{stem}_{uuid.uuid4().hex[:6]}"


# ── estimate ─────────────────────────────────────────────────────────────────

def stream_bytes_per_second():
    return (STREAM_VIDEO_KBPS + STREAM_AUDIO_KBPS) * 1000 / 8 * CONTAINER_OVERHEAD


def estimate_scene_seconds(scene, speed=1.0, line_gap_s=0.0):
    """Wall-clock seconds one planned scene is expected to hold the screen:
    the longer of its visual pacing and its spoken line (audio anchors the
    scene — replay.Performer._perform_scene — so a long line holds a short
    visual), plus the voice gate's inter-line gap."""
    visual = scene_visual_seconds(scene, MAX_OUTPUT_LINES, speed)
    if scene.get("kind") in ("boss", "coder_talk"):
        # Upper bound: a worker with voice.verbatim reads the whole line.
        text = " ".join(e.get("text", "") for e in scene.get("events", []))
        words = len(text.split())
    else:
        words = target_words(visual)
    spoken = words / WORDS_PER_SECOND
    return max(visual, spoken) + max(0.0, float(line_gap_s or 0.0))


def estimate_duration_seconds(script, speed=1.0):
    """Estimated wall-clock length of one airing of `script`."""
    try:
        speed = max(float(speed or 1.0), 0.01)
    except (TypeError, ValueError):
        speed = 1.0
    audio_cfg = ((script or {}).get("show") or {}).get("audio") or {}
    try:
        line_gap_s = float(audio_cfg.get("line_gap_s") or 0.0)
    except (TypeError, ValueError):
        line_gap_s = 0.0
    scenes = plan_scenes((script or {}).get("events", []))
    raw = sum(estimate_scene_seconds(s, speed, line_gap_s) for s in scenes)
    total = raw * DURATION_SAFETY + FIXED_OVERHEAD_S
    log.debug("event=estimate_duration scenes=%d raw_s=%.1f total_s=%.1f speed=%s",
              len(scenes), raw, total, speed)
    return total


def estimate_recording(script, streams=1, speed=1.0):
    """{duration_s, bytes_per_stream, streams, total_bytes} for recording
    `streams` concurrent streams of one airing."""
    streams = max(int(streams), 1)
    duration = estimate_duration_seconds(script, speed)
    per_stream = int(duration * stream_bytes_per_second())
    return {"duration_s": round(duration, 1), "bytes_per_stream": per_stream,
            "streams": streams, "total_bytes": per_stream * streams}


# ── usage ────────────────────────────────────────────────────────────────────

def read_reservation(directory):
    try:
        data = json.loads((Path(directory) / RESERVATION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _files(directory):
    out = []
    for dirpath, _dirs, names in os.walk(directory):
        for name in names:
            path = os.path.join(dirpath, name)
            try:
                out.append((path, os.stat(path).st_size))
            except OSError:
                continue  # removed mid-walk
    return out


def recording_status(directory, now=None):
    """{id, bytes, reserved_bytes, live, files, reservation} for one
    recording directory. `live` = reserved and not yet finished: some stream
    has no done marker and the reservation hasn't expired."""
    directory = Path(directory)
    now = time.time() if now is None else now
    files = _files(directory)
    actual = sum(size for _path, size in files)
    reservation = read_reservation(directory)
    live = False
    reserved = 0
    if reservation:
        reserved = int(reservation.get("reserved_bytes") or 0)
        streams = reservation.get("streams") or []
        done = {os.path.basename(p) for p, _s in files}
        all_done = bool(streams) and all(done_marker_name(w) in done for w in streams)
        expired = now >= float(reservation.get("expires_at") or 0)
        live = not all_done and not expired
    videos = sorted(
        ({"name": os.path.relpath(p, directory).replace(os.sep, "/"), "bytes": s}
         for p, s in files if p.endswith(".mp4")),
        key=lambda f: f["name"])
    return {"id": directory.name, "bytes": actual, "reserved_bytes": reserved,
            "live": live, "files": videos, "reservation": reservation}


def budget_usage(root, now=None):
    """Bytes charged against the cap: every finished recording at its real
    size, every live one at max(real size, its reservation)."""
    root = Path(root)
    if not root.is_dir():
        return 0
    used = 0
    for entry in root.iterdir():
        if entry.is_dir():
            st = recording_status(entry, now)
            used += max(st["bytes"], st["reserved_bytes"]) if st["live"] else st["bytes"]
        elif entry.is_file():
            try:
                used += entry.stat().st_size
            except OSError:
                continue
    return used


def list_recordings(root, now=None):
    """recording_status() for every recording directory, newest first."""
    root = Path(root)
    if not root.is_dir():
        return []
    return [recording_status(e, now)
            for e in sorted(root.iterdir(), key=lambda p: p.name, reverse=True)
            if e.is_dir()]


# ── admission ────────────────────────────────────────────────────────────────

def check_budget(estimate, used_bytes, limit_bytes):
    """Admission decision for a new recording (pure).

    Returns {allowed, reason, used_bytes, limit_bytes, remaining_bytes,
    estimated_bytes, max_bytes_per_stream, reserved_bytes}.
    `max_bytes_per_stream` is the hard ffmpeg -fs cap each recorder gets:
    the estimate x CAP_HEADROOM, never more than an even share of what's
    left — so N recorders together can never exceed the cap even when the
    estimate is wrong.
    """
    streams = max(int(estimate["streams"]), 1)
    remaining = max(int(limit_bytes) - int(used_bytes), 0)
    total = int(estimate["total_bytes"])
    allowed = total <= remaining
    per_stream_cap = min(int(estimate["bytes_per_stream"] * CAP_HEADROOM), remaining // streams)
    if allowed:
        reason = "fits"
    else:
        reason = (f"estimated {format_bytes(total)} for {streams} stream(s) "
                  f"(~{format_duration(estimate['duration_s'])}) exceeds the "
                  f"{format_bytes(remaining)} left of the {format_bytes(limit_bytes)} "
                  f"recording limit ({format_bytes(used_bytes)} used)")
    return {
        "allowed": allowed,
        "reason": reason,
        "used_bytes": int(used_bytes),
        "limit_bytes": int(limit_bytes),
        "remaining_bytes": remaining,
        "estimated_bytes": total,
        "estimated_duration_s": estimate["duration_s"],
        "streams": streams,
        "max_bytes_per_stream": per_stream_cap,
        "reserved_bytes": per_stream_cap * streams,
    }


def reserve(root, episode, script, stream_ids, limit_bytes, speed=1.0, now=None):
    """Estimate, check the budget, and — when it fits — create the
    recording directory with its reservation. Returns check_budget()'s dict
    plus `recording_id` (None when refused). Nothing is written on refusal.
    """
    root = Path(root)
    now = time.time() if now is None else now
    stream_ids = [str(s) for s in stream_ids]
    estimate = estimate_recording(script, streams=len(stream_ids), speed=speed)
    decision = check_budget(estimate, budget_usage(root, now), limit_bytes)
    decision["recording_id"] = None
    if not decision["allowed"]:
        log.info("event=recording_refused episode=%s reason=%r", episode, decision["reason"])
        return decision
    if decision["max_bytes_per_stream"] <= 0:
        decision["allowed"] = False
        decision["reason"] = "no recording budget left"
        return decision
    recording_id = new_recording_id(episode)
    directory = root / recording_id
    directory.mkdir(parents=True, exist_ok=False)
    reservation = {
        "recording_id": recording_id,
        "episode": episode,
        "streams": stream_ids,
        "max_bytes_per_stream": decision["max_bytes_per_stream"],
        "reserved_bytes": decision["reserved_bytes"],
        "estimated_bytes": decision["estimated_bytes"],
        "estimated_duration_s": decision["estimated_duration_s"],
        "created_at": now,
        "expires_at": now + RESERVATION_PREP_ALLOWANCE_S + 2 * decision["estimated_duration_s"],
    }
    tmp = directory / f"{RESERVATION_FILE}.tmp"
    tmp.write_text(json.dumps(reservation, indent=2), encoding="utf-8")
    os.replace(tmp, directory / RESERVATION_FILE)
    decision["recording_id"] = recording_id
    log.info("event=recording_reserved recording_id=%s episode=%s streams=%d "
             "estimated_bytes=%d reserved_bytes=%d", recording_id, episode,
             len(stream_ids), decision["estimated_bytes"], decision["reserved_bytes"])
    return decision


def delete_recording(root, recording_id):
    """Remove one recording directory. Returns True if it existed. The id is
    re-sanitized so a caller can't escape `root`."""
    safe = sanitize_recording_id(recording_id)
    if not safe or safe != recording_id:
        return False
    target = Path(root) / safe
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    log.info("event=recording_deleted recording_id=%s", safe)
    return True


# ── formatting ───────────────────────────────────────────────────────────────

def format_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if abs(n) < 1000:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.2f} GB"


def format_duration(seconds):
    seconds = int(round(float(seconds or 0)))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{secs:02d}s"
