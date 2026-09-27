# relay_io

## Overview

`app/relay_io.py` is the single implementation of the file-based IPC that
processes inside one worker container use to talk to each other. Before it
existed, `app/agent_handlers/relay_files.py`, `app/replay_pane.py` and
`app/tile_pane.py` each carried their own copies of "atomic write", "read a
JSON file", "delete a stale file" and the path resolvers — with slightly
different race behaviour in each. Every reader and writer of these files now
goes through this module:

| File | Env var | Default | Writer | Reader |
|---|---|---|---|---|
| request | `REPLAY_REQUEST_FILE` | `/tmp/replay_request.json` | agent (`replay_request`, `replay_invite`, `viewer_joined`) | `replay_pane.read_request` (consume) |
| stop | `REPLAY_STOP_FILE` | `/tmp/replay_stop.json` | agent (`replay_stop`) | replay/tile panes (`os.path.exists`) |
| cue | `REPLAY_CUE_FILE` | `/tmp/replay_cue.json` | agent (`replay_cue`, `replay_end`) | follower `wait_for_scene` |
| ready | `REPLAY_READY_FILE` | `/tmp/replay_ready.json` | agent (`replay_ready`, read-modify-write) | director ready-wait |
| tile request | `TILE_RELAY_DIR` | `/tmp/tiles/<slot>.request.json` | director (`_write_tile_requests`) | `tile_pane.handle_once` (consume) |
| tile cue | `TILE_RELAY_DIR` | `/tmp/tiles/<slot>.cue.json` | director (`_write_tile_cues`/`_write_tile_end`) | tile `wait_for_scene` |

(`<relay-dir>/stage.json` and `<slot>.state.json` are written by
`app/gaze.py` and `app/agent_state.py`, which already use per-process unique
temp names and are out of scope here.)

File names, JSON shapes and timing are unchanged — this module only changes
*how* the bytes get there.

### Race rules

- **Write** — `json.dumps` into a temp file with a unique name
  (`.<basename>.<pid>.<uuid>.tmp`, created `O_EXCL` in the same directory),
  then `os.replace` onto the real name. A reader sees the old file, the new
  file, or no file — never a partial one. The old copies used a fixed
  `<path>.tmp`, so two writers of one file could interleave into the same
  temp file, or one writer's `os.replace` could consume the other's temp file
  and make the second `os.replace` raise `FileNotFoundError` (the same class
  of bug commit 12ae124 fixed in `agent_state.py`).
- **Create-if-absent** — `atomic_create_json` publishes with `os.link`, which
  fails with `FileExistsError` instead of overwriting. This is the race-free
  form of the "don't clobber a pending request" rule.
- **Read** — missing, unreadable, partial or invalid JSON (including
  undecodable bytes or a directory in the way) returns `None`, logged at
  DEBUG. Never raises.
- **Consume** — the file is first *claimed* by `os.replace`-ing it onto a
  unique `.claimed` name, then read and removed from there. Only one consumer
  can win the rename, so a request is acted on exactly once; and a new
  request written after the claim keeps the real name and is picked up on
  the next poll instead of being deleted unread. A claimed file can never be
  half-written because every writer publishes atomically — the only way a
  file appears under its real name is fully formed.
- **Stale cleanup** — `delete_stale(path)` is the old unconditional,
  never-raising delete. With `keep_airing_id=` it spares a file that belongs
  to the airing about to run. `delete_if_airing(path, airing_id)` removes a
  file only if it belongs to the airing that just finished. Both use
  `remove_if`, which claims the file before inspecting it and, if the file
  should be kept, links it back — unless a newer file has appeared there
  meanwhile, in which case the newer one wins.

## Signature

```python
# paths
REPLAY_REQUEST_FILE_ENV, DEFAULT_REPLAY_REQUEST_FILE   # "REPLAY_REQUEST_FILE", "/tmp/replay_request.json"
REPLAY_STOP_FILE_ENV,    DEFAULT_REPLAY_STOP_FILE      # "REPLAY_STOP_FILE",    "/tmp/replay_stop.json"
REPLAY_CUE_FILE_ENV,     DEFAULT_REPLAY_CUE_FILE       # "REPLAY_CUE_FILE",     "/tmp/replay_cue.json"
REPLAY_READY_FILE_ENV,   DEFAULT_REPLAY_READY_FILE     # "REPLAY_READY_FILE",   "/tmp/replay_ready.json"
TILE_RELAY_DIR_ENV,      DEFAULT_TILE_RELAY_DIR        # "TILE_RELAY_DIR",      "/tmp/tiles"

def resolve_path(env_var: str, default: str) -> str
def resolve_replay_request_file() -> str
def resolve_replay_stop_file() -> str
def resolve_replay_cue_file() -> str
def resolve_replay_ready_file() -> str
def resolve_tile_relay_dir(relay_dir: str | None = None) -> str

# write
def atomic_write_json(path, data, fsync: bool = False) -> None
def atomic_create_json(path, data, fsync: bool = False) -> bool

# read / consume
def read_json(path) -> object | None
ConsumeResult = namedtuple("ConsumeResult", "found data error")
def consume_json(path) -> ConsumeResult

# cleanup
def remove_file(path) -> bool
def remove_if(path, predicate: Callable[[object | None], bool]) -> bool
def delete_stale(path, keep_airing_id: str | None = None) -> bool
def delete_if_airing(path, airing_id: str) -> bool
```

## Parameters

- `path` — `str` or `os.PathLike`. The directory must already exist; nothing
  here creates directories (`tile_pane.ensure_relay_dir` does that for tiles).
- `data` — any JSON-serializable value. Serialized with `json.dumps(data)`,
  byte-identical to the old `json.dump(data, f)`.
- `fsync` — optional, default `False`. Flushes the temp file to disk before
  publishing it. Relay files live in `/tmp` and only need to outlive the
  process, so no current caller turns it on.
- `env_var` / `default` — `resolve_path` returns the env var when it is set
  and non-empty, else `default`. An empty env var means "use the default".
- `relay_dir` — explicit tile relay dir (the tile pane's `--relay-dir`); wins
  over `TILE_RELAY_DIR`.
- `predicate` — called with the parsed JSON (`None` for garbage); a truthy
  result removes the file. A predicate that raises keeps the file.
- `keep_airing_id` — when set, a dict whose `airing_id` equals it is kept;
  everything else is removed.
- `airing_id` (`delete_if_airing`) — the file is removed when its
  `airing_id` equals this, or when it is garbage.

## Return Value

- `atomic_write_json` — `None`.
- `atomic_create_json` — `True` when written, `False` when a file already
  existed (left untouched).
- `read_json` — the parsed value, or `None`.
- `consume_json` — `ConsumeResult(found, data, error)`: `(False, None, None)`
  when nothing was pending; `(True, data, None)` on success;
  `(True, None, exc)` when a file was there but could not be parsed (it is
  still removed).
- `remove_file` / `remove_if` / `delete_stale` / `delete_if_airing` — `True`
  when this call removed the file.

## Dependencies

Standard library only: `json`, `logging`, `os`, `uuid`, `collections`.
Used by `app/agent_handlers/relay_files.py`, `replay_relay.py`, `viewer.py`,
`app/replay_pane.py` and `app/tile_pane.py`. Those modules keep their old
underscore names (`_atomic_write_json`, `_read_json_file`,
`_delete_stale_file`, `_resolve_replay_*_file`) as thin aliases, so
`app/agent.py`'s re-exports and tests that monkeypatch them still work.

## Usage Examples

```python
import relay_io

# Agent: queue a follower request unless one is already pending.
if not relay_io.atomic_create_json(relay_io.resolve_replay_request_file(),
                                   {"episode": "ep1", "mode": "follow"}):
    print("a replay request is already pending")

# Pane: consume the request exactly once.
found, request, error = relay_io.consume_json(relay_io.resolve_replay_request_file())
if found and error is None and isinstance(request, dict):
    perform(request)
```

```python
# Tile: before performing airing "a1", drop stale cues but keep a cue the
# director already wrote for a1; after the show, consume only a1's cue.
relay_io.delete_stale(cue_file, keep_airing_id="a1")
...
relay_io.delete_if_airing(cue_file, "a1")
```

## Error Handling

- `atomic_write_json` / `atomic_create_json` raise `OSError` (missing
  directory, read-only fs, disk full) and `TypeError`/`ValueError` for
  data that can't be serialized. The temp file is always removed on failure.
  Callers already catch `OSError` and decide how loudly to report it.
  `atomic_create_json` falls back to check-then-replace on a filesystem
  without hard links.
- `remove_file` returns `False` for a missing file and re-raises any other
  `OSError`, so `handle_replay_stop` can still report "failed to cancel".
- Everything else never raises. Failures are logged at DEBUG on the
  `relay_io` logger as `relay_io.<event> path=... error=...`.

## Changelog

- **v1.0.0** (2026-09-27): Initial version. Consolidates the duplicated
  relay helpers from `agent_handlers/relay_files.py`, `replay_pane.py` and
  `tile_pane.py`. Fixes: a fixed `<path>.tmp` temp name shared by writers;
  `read_request` deleting a newer request written while it read the old one;
  check-then-write (`os.path.exists` then write) in `replay_invite` and
  `viewer_joined`; exists-then-remove in `replay_stop`; the tile clearing
  its own airing's scene-0 cue before performing; and the tile's post-show
  cleanup deleting a cue the director had already written for the next
  airing.
