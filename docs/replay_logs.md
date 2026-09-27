# app/replay_logs.py

## Overview

Read-only Postgres queries backing `message-api`'s `GET /logs/containers`
and `GET /logs/messages` — the two log sources the control-panel's Rerun
Theater "Play" button surfaces live (docs/replay_pane.md, docs/panels.md).
Exists to close a gap: hitting Play on an episode had no way to see whether
it actually landed on each worker, or what a worker's narration-prep pass
printed, short of SSHing in and running `docker logs`/`tmux capture-pane`
per container.

Two tables, two very different join keys:

- `container_logs` (`services/log-shipper`, docs/log_shipper.md) — one row
  per stdout/stderr line from ANY container in the compose project, keyed by
  the full Docker container name (`virtualtubers-worker-coder-1`). Callers
  pass compose **service** names (`worker-coder`); `_service_suffix_pattern`
  anchors a regex on compose's own naming convention
  `<project>-<service>-<replica>` so a lookup for `worker-coder` can never
  match the container for `worker-coder-native` (a literal prefix of it —
  substring/LIKE matching would get this wrong).
- `messages` (`services/message-logger`, docs/message_logger.md) — one row
  per Kafka bus message, keyed by the bare `from`/`to` worker ids used on
  the bus. Callers pass worker ids (`coder`, `roundtable`) directly.

## Signature

```python
def connect_db() -> psycopg2.extensions.connection
def fetch_container_logs(services: list[str], since: datetime | None = None, limit: int = 200) -> list[dict]
def fetch_messages(worker_ids: list[str], since: datetime | None = None, limit: int = 200) -> list[dict]
```

Internal:

```python
def _service_suffix_pattern(service: str) -> str
```

## Parameters

- `services` / `worker_ids` — the identifier list to filter on. An empty
  list short-circuits to `[]` with no query at all (a Play click always has
  at least 7 targets, but this keeps the functions safe to call generically).
- `since` (optional `datetime`) — only rows strictly after this timestamp.
  Omitted entirely from the query (not compared against `None`) when unset.
- `limit` (default `200`) — bounds the query with `ORDER BY ... DESC LIMIT`
  before reversing to oldest-first, so a large window can't return an
  unbounded result while still reading naturally top-to-bottom.

Connection handling mirrors `app/log_prune.py` deliberately: one fresh
`psycopg2` connection per call, closed in a `finally`, no pooling. This is
polled every few seconds by the control panel while a replay airs, not a
high-throughput path.

## Return Value

Both functions return a list of dicts (`psycopg2.extras.RealDictCursor`),
oldest-first:

- `fetch_container_logs`: `container_name`, `stream`, `message`, `log_timestamp`.
- `fetch_messages`: `from`, `to`, `type`, `payload`, `timestamp`.

## Dependencies

- `psycopg2`, `psycopg2.extras.RealDictCursor`
- `POSTGRES_HOST` (default `localhost`), `POSTGRES_PORT` (default `5432`),
  `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (required) — same env
  contract as `app/log_prune.py`.
- Reads tables owned by `services/log-shipper` (`container_logs`) and
  `services/message-logger` (`messages`); this module creates neither.

## Usage Examples

```python
from datetime import datetime, timezone
from replay_logs import fetch_container_logs, fetch_messages

# Everything the "coder" and "roundtable" containers printed since a Play click
rows = fetch_container_logs(["worker-coder", "worker-roundtable"],
                             since=datetime(2026, 9, 27, tzinfo=timezone.utc))

# Every bus message to/from those same two worker ids
msgs = fetch_messages(["coder", "roundtable"], limit=100)
```

Wired into `message-api` (`services/message-api/api.py`):

```bash
curl "http://localhost:8090/logs/containers?service=worker-coder&service=worker-roundtable&since=2026-09-27T00:00:00Z"
curl "http://localhost:8090/logs/messages?worker_id=coder&worker_id=roundtable"
```

## Error Handling

- `services`/`worker_ids` empty — returns `[]` without touching Postgres.
- Postgres unreachable — raises `psycopg2.OperationalError` uncaught; the
  caller (`api.py`'s `/logs/containers`/`/logs/messages` handlers) turns
  that into an HTTP 503, same convention as `/logs/prune` and `/replays`.

## Changelog

- v1.0.0 (2026-09-27) — Initial version, added alongside `message-api`'s
  `GET /logs/containers`/`GET /logs/messages` and the control-panel's
  Rerun Theater log viewer.
