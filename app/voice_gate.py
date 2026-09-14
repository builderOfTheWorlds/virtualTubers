"""
voice_gate.py
Cross-process "voice seat" semaphore: at most N audible voices at once.

Why this exists (roundtable_stream_design.md §16, docs/voice_gate.md):
the roundtable channel mixes every tile's audio (seven paplay processes,
one shared Pulse `vout` sink ffmpeg captures) and a duet scatters audio
across containers. Nothing before this module guaranteed that a new voice
line cannot start while the previous one is still sounding:

  * the director's master clock can cue scene N+1 a beat before tile N's
    audio has physically finished (paplay buffering, polling cadence), so
    the next character bled into the previous one;
  * in a roundtable cast the director ALSO played the GM's own lines
    alongside the GM tile, so the same voice sounded twice, offset.

The fix is a semaphore with N "seats", default N=1: every place that
starts a voice line requests a seat before spawning its player and holds
it until that line's audio has finished. With one seat, a second voice
physically cannot start until the first has released — independent of how
any of the timelines drift. N>=2 is the deliberate-overlap escape hatch
(a show header's `audio.max_concurrent`, see resolve in replay_pane.py).

Mechanism: fcntl.flock on N seat files (seat_000..seat_N-1) in a shared
gate directory. N files + non-blocking exclusive tries = a counting lock,
with none of the failure modes of a daemon/lock-server:

  * the OS releases the lock when the holder DIES — a crashed tile or
    worker can never wedge the show (no orphaned leases to clean up);
  * no new protocol, no new service, no shared package dependency;
  * the default gate dir is the CONTAINER's own /tmp — which is exactly
    the processes whose voices mix into that channel's one Pulse sink
    (a roundtable container's director + its seven tiles share that /tmp;
    two character containers are different /tmps, so they can never
    contend, which is correct: each channel is one Pulse sink each).
    VOICE_GATE_DIR (env) or show.audio.gate_dir point the gate at a
    shared dir if an operator wants a different scope.

Failure policy — same contract as the rest of replay.py: a voice line is
NEVER withheld. A missing/unwritable gate dir, a seat-file error, or an
acquire timeout (VOICE_GATE_ACQUIRE_TIMEOUT_S, default 90s) all degrade
to "play anyway" with one WARN on stderr. Acquire is bounded on purpose:
the only way it can run long is if a previous holder is genuinely still
playing a long line — and that is exactly the overlap we are preventing.

Observability: every acquire/release appends one JSON line to
<gate_dir>/events.jsonl (best-effort), so an operator can reconstruct the
exact seat timeline after the fact ("did two voices really overlap?").
"""
import json
import os
import time

try:
    import fcntl
except ImportError:  # pragma: no cover — POSIX-only stack, but keep import clean
    fcntl = None

DEFAULT_SEATS = 1
DEFAULT_ACQUIRE_TIMEOUT_S = 90.0
POLL_INTERVAL_S = 0.05
SEAT_NAME = "seat_{:03d}"


def _warn(message):
    print(f"[voice_gate] WARN: {message}", flush=True)


def _event(gate_dir, event, **fields):
    """Append one structured event line to <gate_dir>/events.jsonl.
    Best-effort: observability must never block or fail a voice line."""
    try:
        with open(os.path.join(gate_dir, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), "pid": os.getpid(),
                                "event": event, **fields}) + "\n")
    except OSError:
        pass


def _ensure_seat_files(gate_dir, seats):
    """Create the seat files if absent and return their paths. Raises
    OSError on an unusable directory — callers treat that as
    'gate unavailable', not an error worth raising further."""
    os.makedirs(gate_dir, exist_ok=True)
    return [os.path.join(gate_dir, SEAT_NAME.format(i)) for i in range(seats)]
    # Note: files are created (touched) per-acquire; existing files are
    # reused deliberately — the seat inode must STABLE across processes,
    # so release must never unlink (unlinking under contention breaks
    # mutual exclusion for holders of deleted inodes).


def _touch(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    return fd


class Seat:
    """A held voice seat. release() is idempotent — call it from a
    `finally` unconditionally; releasing twice is a no-op."""

    def __init__(self, index, fd, gate_dir, tag):
        self.index = index
        self.fd = fd
        self.gate_dir = gate_dir
        self.tag = tag
        self.released = False

    @property
    def seat(self):
        return self.index

    def release(self):
        if self.released:
            return
        self.released = True
        if fcntl is None:
            # Cannot happen (Seat is only built inside acquire(), which
            # requires flock) — kept so the type-checker sees the guard.
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        except (OSError, AttributeError):
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        _event(self.gate_dir, "release", seat=self.index, tag=self.tag)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


class VoiceGate:
    """Request seats before starting a voice line, hold one until the
    audio finishes, release once. One instance per (gate_dir, seats) pair;
    construct once per process and pass it to every Performer that plays
    owned audio in that show (replay.Performer(voice_gate=...)).

    Acquire is BOUNDED: on timeout it returns None and the caller degrades
    (plays without a seat). That is the intended liveness guarantee — a
    gate must never keep a line from being heard. The default timeout is
    generous on purpose: the longest legal line is ~52 s of speech
    (revoice MAX_WORDS=130 @ 2.5 wps) plus wait_extra's 10 s grace, and a
    waiter may legitimately be behind a queue of up to (seats) such lines.
    """

    def __init__(self, gate_dir, seats=DEFAULT_SEATS,
                 acquire_timeout_s=DEFAULT_ACQUIRE_TIMEOUT_S, tag="unknown"):
        self.gate_dir = str(gate_dir)
        self.seats = max(1, int(seats))
        self.acquire_timeout_s = float(acquire_timeout_s)
        self.tag = tag

    def available(self):
        """True when the gate dir is usable and flock is usable at all."""
        if fcntl is None:
            return False
        try:
            os.makedirs(self.gate_dir, exist_ok=True)
            probe = os.path.join(self.gate_dir, ".probe")
            fd = os.open(probe, os.O_RDWR | os.O_CREAT, 0o644)
            os.close(fd)
            return True
        except OSError:
            return False

    def acquire(self):
        """Grab one seat (lowest free index) or None on timeout/unusable
        gate. Never raises.

        NOTE on file descriptors: each attempt opens its OWN fresh fds and
        closes exactly what it used. Reusing previously-opened fds across
        poll iterations is what produces EBADF (a closed fd re-flocked on
        the next round) — do not "optimize" the open back out."""
        if fcntl is None:
            _warn("flock unavailable — gate disabled, playing without it (tag=%s)" % self.tag)
            return None
        try:
            seat_paths = _ensure_seat_files(self.gate_dir, self.seats)
        except OSError as exc:
            _warn(f"gate dir {self.gate_dir!r} unusable ({exc}) — playing without gate "
                  f"(tag={self.tag})")
            return None

        deadline = time.monotonic() + self.acquire_timeout_s
        while time.monotonic() < deadline:
            for index, path in enumerate(seat_paths):
                fd = None
                try:
                    fd = _touch(path)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, InterruptedError):
                    if fd is not None:
                        os.close(fd)
                    continue  # seat held by another process — try the next
                except OSError as exc:
                    if fd is not None:
                        os.close(fd)
                    _warn(f"flock failed on seat {index} ({exc}) — "
                          f"playing without gate (tag={self.tag})")
                    return None
                _event(self.gate_dir, "acquire", seat=index, tag=self.tag)
                return Seat(index, fd, self.gate_dir, self.tag)
            time.sleep(POLL_INTERVAL_S)

        _warn(f"timed out ({self.acquire_timeout_s:.0f}s) waiting for a voice seat "
              f"— a line may overlap (tag={self.tag})")
        return None


def resolve_voice_gate(script, config, env=None, roster_size=7):
    """Resolve the gate parameters for one airing from the layered sources
    (env > show header `audio` > worker config `voice.audio` > defaults):

      seats: VOICE_GATE_CONCURRENT > show.audio.max_concurrent
             > voice.audio.max_concurrent > 1     (clamped to 1..roster_size)
      line gap: VOICE_GATE_LINE_GAP_S > show.audio.line_gap_s
             > voice.audio.line_gap_s > 0.0       (clamped to >= 0)
      gate dir: VOICE_GATE_DIR > voice.audio.gate_dir > show.audio.gate_dir
             > /tmp/voice_gate (per-container private — see _default_gate_dir)

    Returns (gate_dir, seats, line_gap_s, acquire_timeout_s). Bad values
    degrade to the next layer, never raise — a misconfigured header must
    not stop a show.
    """
    env = os.environ if env is None else env
    show_audio = ((script or {}).get("show") or {}).get("audio") if isinstance(
        (script or {}).get("show"), dict) else None
    if not isinstance(show_audio, dict):
        show_audio = {}
    voice_audio = ((config or {}).get("voice") or {}).get("audio")
    if not isinstance(voice_audio, dict):
        voice_audio = {}

    def _resolve_number(env_key, show_key, cfg_key, default, lo, hi):
        # Order: env (strings "3" allowed — env vars ARE strings) > show
        # header > worker config > default. bool is explicitly rejected
        # (it subclasses int and would silently become 1/0). Non-numeric
        # strings degrade to the next layer — a typo must not stop a show.
        for source in (env.get(env_key), show_audio.get(show_key),
                       voice_audio.get(cfg_key), default):
            if source is None or isinstance(source, bool):
                continue
            try:
                value = float(source)
            except (TypeError, ValueError):
                continue
            if value != value:  # NaN guard
                continue
            return min(hi, max(lo, value))
        return default

    seats = int(_resolve_number(
        "VOICE_GATE_CONCURRENT", "max_concurrent", "max_concurrent",
        DEFAULT_SEATS, 1, roster_size))

    line_gap_s = _resolve_number(
        "VOICE_GATE_LINE_GAP_S", "line_gap_s", "line_gap_s", 0.0, 0.0, 60.0)

    acquire_timeout_s = _resolve_number(
        "VOICE_GATE_ACQUIRE_TIMEOUT_S", "acquire_timeout_s", "acquire_timeout_s",
        DEFAULT_ACQUIRE_TIMEOUT_S, 5.0, 3600.0)

    gate_dir = (env.get("VOICE_GATE_DIR")
                or show_audio.get("gate_dir")
                or voice_audio.get("gate_dir")
                or _default_gate_dir())
    return str(gate_dir), seats, line_gap_s, acquire_timeout_s


def _default_gate_dir():
    """The default seat dir: **/tmp/voice_gate — i.e. the container's own
    private space.** That is exactly the right scope, and it needs no env
    at all:

    * A roundtable container runs its director AND all seven tiles in ONE
      container → they share that container's /tmp → the gate arbitrates
      precisely the processes whose voices mix into the channel's one Pulse
      sink. Nothing to configure.
    * A character worker is ONE process in its own container → the gate is
      uncontended but harmless, and it can never see another channel's
      seats (different container, different /tmp).

    This deliberately avoids /data/world-state (a named volume shared by ALL
      workers): a two-seat roundtable line must not be able to wedge a
    character channel's line behind the acquire timeout, and it deliberately
    avoids $TILE_RELAY_DIR: the relay dir is a pure-file wire whose contents
    are asserted exactly by its own test suite (a 'voice_gate' subdir would
    fail the relay-purity contracts).

    Ops can still override (VOICE_GATE_DIR env or show.audio.gate_dir) — e.g.
    to keep seats across a live container rebuild by pointing at a volume."""
    return "/tmp/voice_gate"
