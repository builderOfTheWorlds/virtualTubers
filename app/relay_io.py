"""
relay_io.py
The ONE implementation of the small JSON relay files processes inside a
worker container use to talk to each other (docs/relay_io.md):

  * agent.py handlers -> replay_pane.py: REPLAY_REQUEST_FILE /
    REPLAY_STOP_FILE / REPLAY_CUE_FILE / REPLAY_READY_FILE
    (defaults /tmp/replay_*.json, docs/duet_replay.md "The 3 relay files");
  * replay_pane.py (the roundtable director) -> tile_pane.py: per-slot
    request/cue files under TILE_RELAY_DIR (default /tmp/tiles).

Every writer and reader of those files goes through the helpers below, so
the race rules live in exactly one place:

  * WRITE (atomic_write_json / atomic_create_json): the JSON goes into a
    temp file with a UNIQUE name in the same directory (O_EXCL, so two
    writers can never share one — the fixed "<path>.tmp" name the old
    copies used let two writers interleave or have one os.replace steal the
    other's temp file), then os.replace / os.link publishes it in one step.
    A reader therefore sees either the old file, the new file, or no file —
    never a half-written one.
  * READ (read_json): missing, unreadable, partial or invalid JSON all mean
    "nothing usable yet" -> None, logged at DEBUG. Never raises.
  * CONSUME (consume_json): a request must be acted on exactly once, and
    consuming it must never delete a NEWER request written in the meantime.
    The file is first claimed by os.replace-ing it onto a unique name (only
    one consumer can win that rename), then read and removed from the
    claimed name. Because every writer is atomic, a file visible under its
    real name is always complete, so claiming it can never capture a half-
    written request. A file that vanishes between steps is simply "nothing
    pending".
  * CLEANUP (delete_stale / remove_file / remove_if): best-effort stale-
    state hygiene, optionally airing-aware so a fresh cue for the CURRENT
    airing is never thrown away as "stale".

Nothing here raises except the write helpers (OSError, so callers decide
how loudly to report a failed write — same contract the old copies had).
"""
import json
import logging
import os
import uuid
from collections import namedtuple

log = logging.getLogger("relay_io")

# ── paths (env var names + defaults are part of the deployment contract) ────
REPLAY_REQUEST_FILE_ENV = "REPLAY_REQUEST_FILE"
DEFAULT_REPLAY_REQUEST_FILE = "/tmp/replay_request.json"

REPLAY_STOP_FILE_ENV = "REPLAY_STOP_FILE"
DEFAULT_REPLAY_STOP_FILE = "/tmp/replay_stop.json"

REPLAY_CUE_FILE_ENV = "REPLAY_CUE_FILE"
DEFAULT_REPLAY_CUE_FILE = "/tmp/replay_cue.json"

REPLAY_READY_FILE_ENV = "REPLAY_READY_FILE"
DEFAULT_REPLAY_READY_FILE = "/tmp/replay_ready.json"

TILE_RELAY_DIR_ENV = "TILE_RELAY_DIR"
DEFAULT_TILE_RELAY_DIR = "/tmp/tiles"


def resolve_path(env_var, default):
    """env var (when set and non-empty) > default — the convention every
    relay path has always used (an empty env var means "use the default")."""
    return os.environ.get(env_var) or default


def resolve_replay_request_file():
    return resolve_path(REPLAY_REQUEST_FILE_ENV, DEFAULT_REPLAY_REQUEST_FILE)


def resolve_replay_stop_file():
    return resolve_path(REPLAY_STOP_FILE_ENV, DEFAULT_REPLAY_STOP_FILE)


def resolve_replay_cue_file():
    return resolve_path(REPLAY_CUE_FILE_ENV, DEFAULT_REPLAY_CUE_FILE)


def resolve_replay_ready_file():
    return resolve_path(REPLAY_READY_FILE_ENV, DEFAULT_REPLAY_READY_FILE)


def resolve_tile_relay_dir(relay_dir=None):
    """explicit (--relay-dir) > TILE_RELAY_DIR env > /tmp/tiles."""
    return str(relay_dir or resolve_path(TILE_RELAY_DIR_ENV, DEFAULT_TILE_RELAY_DIR))


# ── write ────────────────────────────────────────────────────────────────────
def _unique_sibling(path, tag):
    """A unique, hidden name in the SAME directory as `path` (so rename/link
    stay atomic on one filesystem). Hidden + not ending in .json so nothing
    that lists a relay dir mistakes it for a real relay file."""
    directory, base = os.path.split(os.fspath(path))
    name = f".{base}.{os.getpid()}.{uuid.uuid4().hex}.{tag}"
    return os.path.join(directory, name)


def _write_temp(path, data, fsync):
    """Serialize `data` into a fresh unique temp file next to `path` and
    return its name. O_EXCL guarantees no other writer shares this file;
    mode 0o666 & ~umask keeps the same permissions a plain open() gave."""
    text = json.dumps(data)
    tmp = _unique_sibling(path, "tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
    except BaseException:
        _unlink_quietly(tmp)
        raise
    return tmp


def atomic_write_json(path, data, fsync=False):
    """Atomically replace `path` with `data` as JSON. Raises OSError (and
    TypeError/ValueError for unserializable data) — callers decide how
    loudly to report a failed relay write. fsync=True additionally flushes
    to disk; relay files live in /tmp and only need to survive the process,
    so it is off by default."""
    tmp = _write_temp(path, data, fsync)
    try:
        os.replace(tmp, path)
    except BaseException:
        _unlink_quietly(tmp)
        raise
    log.debug("relay_io.write path=%s", path)


def atomic_create_json(path, data, fsync=False):
    """Atomically create `path` with `data` ONLY if it does not exist yet.
    Returns True when written, False when a file was already there (it is
    left untouched). Raises OSError on any other failure.

    This is the race-free form of "if not exists: write" — used for the
    "don't clobber a pending request" rule. os.link fails with
    FileExistsError instead of overwriting, so there is no window between
    the check and the write."""
    tmp = _write_temp(path, data, fsync)
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            log.debug("relay_io.create_skipped path=%s reason=exists", path)
            return False
        except OSError as exc:
            # A filesystem without hard links: fall back to check+replace,
            # which is what callers did before this helper existed.
            log.debug("relay_io.create_link_unsupported path=%s error=%s", path, exc)
            if os.path.lexists(path):
                return False
            os.replace(tmp, path)
            tmp = None
        log.debug("relay_io.create path=%s", path)
        return True
    finally:
        if tmp is not None:
            _unlink_quietly(tmp)


# ── read ─────────────────────────────────────────────────────────────────────
def _load(path):
    """(found, data, error): found=False when the file is missing; data is
    the parsed JSON or None when it could not be read/parsed (error says
    why)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return False, None, None
    except (OSError, ValueError) as exc:  # ValueError: undecodable bytes
        return True, None, exc
    try:
        return True, json.loads(text), None
    except ValueError as exc:
        return True, None, exc


def read_json(path):
    """Best-effort read of a relay file. Missing, unreadable, partial or
    invalid JSON returns None ("nothing usable yet") — never raises."""
    found, data, error = _load(path)
    if error is not None:
        log.debug("relay_io.read_invalid path=%s error=%s", path, error)
    elif not found:
        log.debug("relay_io.read_missing path=%s", path)
    return data


# ── consume ──────────────────────────────────────────────────────────────────
ConsumeResult = namedtuple("ConsumeResult", "found data error")


def _claim(path):
    """Move `path` onto a unique claimed name and return that name, or None
    when there was nothing to claim. The rename is atomic, so exactly one
    of several racing consumers wins; the others see FileNotFoundError."""
    claimed = _unique_sibling(path, "claimed")
    try:
        os.replace(path, claimed)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.debug("relay_io.claim_failed path=%s error=%s", path, exc)
        return None
    return claimed


def consume_json(path):
    """Read-and-remove `path` so its contents are acted on exactly once.

    Returns ConsumeResult(found, data, error): found=False when nothing was
    pending; found=True with data=None and error set when the file existed
    but was unreadable/garbage (it is still removed — a bad request must
    never wedge a poll loop).

    Safe against every interleaving the relay files see: a file that
    vanishes before the claim is "nothing pending"; a NEW request written
    after the claim keeps its real name and is left for the next poll
    (the old read-then-unlink deleted it unread); and since all writers
    publish atomically, a claimed file is always complete."""
    claimed = _claim(path)
    if claimed is None:
        return ConsumeResult(False, None, None)
    try:
        found, data, error = _load(claimed)
    finally:
        _unlink_quietly(claimed)
    if not found:  # removed out from under us after the claim — treat as gone
        return ConsumeResult(False, None, None)
    if error is not None:
        log.debug("relay_io.consume_invalid path=%s error=%s", path, error)
    else:
        log.debug("relay_io.consume path=%s", path)
    return ConsumeResult(True, data, error)


# ── cleanup ──────────────────────────────────────────────────────────────────
def _unlink_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def remove_file(path):
    """Remove `path`. True when this call removed it, False when it was
    already gone. Other OSErrors propagate so a caller that reports on the
    outcome (handle_replay_stop's "cancel queued request") can say so."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    log.debug("relay_io.remove path=%s", path)
    return True


def remove_if(path, predicate):
    """Remove `path` only when predicate(data) is true, where data is the
    parsed JSON (None for garbage). Returns True when removed.

    Race-safe against a writer replacing the file mid-check: the file is
    claimed (atomic rename) before it is inspected, so the predicate always
    judges exactly the file that gets removed. A file the predicate wants
    kept is linked back under its real name — unless a newer file has
    appeared there meanwhile, in which case the newer one wins and the
    inspected one is dropped (the newer file supersedes it anyway)."""
    claimed = _claim(path)
    if claimed is None:
        return False
    try:
        found, data, _error = _load(claimed)
        if not found:
            return False
        try:
            doomed = bool(predicate(data))
        except Exception as exc:  # a buggy predicate must not lose the file
            log.debug("relay_io.remove_if_predicate_failed path=%s error=%s", path, exc)
            doomed = False
        if doomed:
            log.debug("relay_io.remove_if path=%s removed=true", path)
            return True
        try:
            os.link(claimed, path)
        except FileExistsError:
            log.debug("relay_io.remove_if path=%s kept=superseded", path)
        except OSError as exc:
            log.debug("relay_io.remove_if_restore_failed path=%s error=%s", path, exc)
            try:
                if not os.path.lexists(path):
                    os.replace(claimed, path)
                    claimed = None
            except OSError:
                pass
        return False
    finally:
        if claimed is not None:
            _unlink_quietly(claimed)


def _airing_of(data):
    return data.get("airing_id") if isinstance(data, dict) else None


def delete_stale(path, keep_airing_id=None):
    """Best-effort stale-state hygiene (docs/duet_replay.md): remove a
    leftover relay file from a previous show. Never raises.

    keep_airing_id: when given, a file that is a dict for THAT airing is
    kept — it was written for the show that is about to run (e.g. the
    director's scene-0 cue landing before the tile got round to its own
    cleanup) and deleting it would lose the cue. Anything else (another
    airing, garbage, a non-dict) is removed."""
    if keep_airing_id is None:
        try:
            return remove_file(path)
        except OSError as exc:
            log.debug("relay_io.delete_stale_failed path=%s error=%s", path, exc)
            return False
    return remove_if(path, lambda data: _airing_of(data) != keep_airing_id)


def delete_if_airing(path, airing_id):
    """Remove `path` only when it belongs to `airing_id` (or is garbage) —
    for consuming "our" cue at the end of a show without deleting a NEWER
    airing's cue that the director may already have written."""
    return remove_if(path, lambda data: data is None or _airing_of(data) == airing_id)
