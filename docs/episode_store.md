# episode_store.py

## Overview

Postgres-backed storage for the Rerun Theater **episode library** — the
pre-built, pre-redacted session scripts a worker performs (docs/replay.md,
docs/replay_pane.md).

Episodes used to be JSON files. `scripts/build_replay_library.py` wrote
`replays/<session>.json` on the dev box, an operator hand-copied that folder
to the deploy host, and every worker bind-mounted it read-only at
`/data/replays`. Nothing between "a file appeared in `replays/`" and "it
plays on a live Twitch stream" ever checked the file, and adding an episode
meant filesystem access to the host.

Now the library is the `replay_episodes` table. Episodes are uploaded
through `message-api`'s `POST /replays` (docs/message_api.md), which
validates each one (docs/episode_validator.md) before inserting it, and the
workers read straight from Postgres. **There is no `/data/replays` mount
any more** — `docker-compose.yml` grants workers `POSTGRES_*` and nothing
else.

Two very different callers share this module:

- **`services/message-api/api.py`** — the only writer. Validates an upload,
  then `save_episode()`s it. Also serves `GET /replays`,
  `GET /replays/{name}` and `DELETE /replays/{name}`.
- **`app/replay_pane.py` and `app/agent.py`** — readers. `list_episodes()`
  for the pane's idle screen and the agent's random `viewer_joined` pick;
  `load_episode()` to turn a requested episode name into its script.

Connection handling deliberately mirrors `app/narration_store.py`
(docs/narration_store.md): lazy `psycopg2` import, one connection per call,
5s `connect_timeout`, `autocommit`. A worker without `psycopg2` or without
the `POSTGRES_*` env must still *import* this module and get a clean
`available() == False`, rather than an `ImportError` at pane startup.

Like `narration_store`, **every function here raises on database failure.**
Turning a failure into "no episodes" is the caller's job. That keeps this
module's failure modes visible to its tests, and lets each caller pick its
own degradation — the pane's idle screen says the store is unreachable
(the one failure an operator has to act on), while the agent's viewer
greeting just skips the rerun and still says hello.

**Accepted trade-off:** with no filesystem fallback, a Postgres outage means
no reruns at all. Every read path above degrades visibly and nothing
crash-loops, but the shows don't air until the database is back.

## Signature

```python
_REQUIRED_ENV = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")

def available() -> bool

def ensure_schema() -> None

def save_episode(name, script, uploaded_by="operator", overwrite=False) -> bool

def load_episode(name) -> dict | None

def list_episodes() -> list[str]

def list_episodes_detailed() -> list[dict]

def delete_episode(name) -> bool
```

## Parameters

- `name` (str, required) — the library key. This is the canonical episode
  string used everywhere else in the stack: the old filename stem, which is
  also `script["source"]`, e.g. `"2026-07-02_04-27-00_6ecdde82"`. Keeping
  that exact value as the primary key is what lets the existing
  `voiced_narration.episode` narration cache and the duet protocol
  (docs/duet_replay.md) carry on unchanged — no migration was needed.
  Callers pass a name already validated by
  `episode_validator.resolve_name`; the lookup itself is a parameterized
  equality match.
- `script` (dict, required) — the whole episode dict from
  `session_log_parser.parse_session`: `source`, `project`, `session_id`,
  `date`, `events`. Stored verbatim as `jsonb`; the metadata columns are
  denormalized copies so `GET /replays` can list the library without
  reading every script.
- `uploaded_by` (str, optional, default `"operator"`) — free-text
  attribution for the upload.
- `overwrite` (bool, optional, default `False`) — when `False` an upload of
  an existing name is refused (`ON CONFLICT DO NOTHING`, return `False`);
  when `True` it replaces the row and stamps a new `uploaded_at`.

## Return Value

- `available()` — `True` when this process can reach the store: `psycopg2`
  importable **and** all of `POSTGRES_DB`/`POSTGRES_USER`/
  `POSTGRES_PASSWORD` present. Same contract as
  `narration_store.available()`; callers treat `False` as "no library",
  never as an error.
- `ensure_schema()` — `None`. Runs `CREATE TABLE IF NOT EXISTS` +
  `CREATE INDEX IF NOT EXISTS`. Unlike `messages`/`container_logs`, no
  long-lived consumer owns this table, so `message-api` calls this
  best-effort at import and retries on every `/replays` request until one
  succeeds. Workers never call it — they only read.
- `save_episode()` — `True` when the row was written, `False` when an
  episode of that name already existed and `overwrite` is `False`
  (`message-api` turns that `False` into a `409`).
- `load_episode()` — the episode script dict, or `None` when the library
  has no such episode. `psycopg2` decodes `jsonb` to a dict already; a
  `str`/`bytes` column value is tolerated and decoded, for drivers and test
  doubles that hand the raw column back.
- `list_episodes()` — episode names, sorted. The pane's idle listing and
  the agent's random `viewer_joined` pick.
- `list_episodes_detailed()` — one dict per episode with `name`, `project`,
  `session_id`, `date`, `event_count`, `byte_size`, `uploaded_by`,
  `uploaded_at` (ISO 8601 string). No scripts — this backs `GET /replays`.
- `delete_episode()` — `True` when a row was deleted, `False` when the name
  was already absent.

## Dependencies

- `psycopg2` (imported lazily inside `available()` and `_connect()`).
- Postgres table `replay_episodes` — DDL owned by this module as
  `CREATE_TABLE_SQL`, mirrored into `docs/sql/02_create_tables.sql` and
  documented in `docs/database_schema.md`. There is no migration framework
  in this project, so all three copies must be kept in sync by hand.
- Standard library: `json`, `os`.

## Usage Examples

How `message-api` stores a validated upload (`POST /replays`):

```python
import episode_store
from episode_validator import EpisodeInvalid, validate_episode

info = validate_episode(script)                     # raises EpisodeInvalid
created = episode_store.save_episode(info["name"], script, overwrite=False)
if not created:
    raise HTTPException(409, f"episode {info['name']!r} already exists")
```

How `replay_pane.resolve_episode` turns a requested name into a script,
degrading a store outage into "no episode" rather than a crashed pane:

```python
import episode_store

if not episode_store.available():
    return None, None
try:
    script = episode_store.load_episode(name)
except Exception as exc:
    print(f"[replay_pane] episode store unreachable: {exc}", file=sys.stderr)
    return None, None
```

## Error Handling

- `available()` never raises — a missing env var or a missing `psycopg2`
  just returns `False`.
- `_connect()` uses a 5s `connect_timeout`: a down database must stall the
  pane for seconds, not minutes, since a read sits directly in the path of
  a show starting.
- Every other function raises on any DB failure (connection refused, bad
  credentials, query error) rather than swallowing it. Callers own the
  degradation:
  - `replay_pane.resolve_episode` / `list_episodes` → the request is
    reported on stderr and the idle screen shows **"episode store
    unreachable"**, distinct from "library empty".
  - `agent._pick_rerun_episode` → returns `None`, and
    `handle_viewer_joined` greets the viewer without a rerun.
  - `message-api` → `503 postgres unavailable: …`, matching the existing
    `POST /logs/prune` handler.
- Every connection is closed in a `finally`, including when the query
  itself raises.
- This module does **no** validation, redaction or schema checking of its
  own — it stores what it is handed. `episode_validator` is the gate, and
  `message-api` is the only caller that writes.

## Changelog

- **v1.0.0** (2026-08-16): Initial version. Replaces the `/data/replays`
  bind mount as the Rerun Theater episode library. `available()`,
  `ensure_schema()`, `save_episode()` (`ON CONFLICT DO NOTHING` /
  `DO UPDATE` under `overwrite`), `load_episode()`, `list_episodes()`,
  `list_episodes_detailed()`, `delete_episode()`. Wired into
  `services/message-api/api.py`'s `/replays` routes,
  `app/replay_pane.py`'s `resolve_episode`/`list_episodes`, and
  `app/agent.py`'s `_pick_rerun_episode`.
