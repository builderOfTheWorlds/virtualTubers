# services/message-api/api.py

## Overview

Minimal HTTP interface for injecting test messages onto the Kafka bus, so an
operator (or another external system, later) can prompt a specific agent
without needing direct Kafka tooling. Pure producer for `/messages` — it
never touches Postgres or the filesystem directly; the separate
`message-logger` service is responsible for durable logging of everything it
(and everyone else) produces.

Also exposes the `/workers` control endpoints — the HTTP surface for turning
a worker on/off without redeploying the stack (docs/worker_control.md). This
is the intended integration point for a future web GUI that toggles workers.

Also exposes the `/log-filter` control endpoints — the HTTP surface for
excluding a noisy message type (e.g. the heartbeat `status_update` flood)
from message-logger's Postgres writes without a stack redeploy
(docs/log_filter_control.md).

Also exposes `POST /logs/prune` — an on-demand delete of `container_logs`
rows in a caller-specified time range, backed by `app/log_prune.py`. This is
one of two endpoint groups that *do* touch Postgres directly (a deliberate
exception to the "pure producer" design above): it complements log-shipper's
own hourly `RETENTION_DAYS`-based prune (docs/log_shipper.md), which only
ever deletes by age, for reclaiming space from a known window without
waiting for the retention cutoff to catch up.

And exposes the `/replays` endpoints — **the only way an episode enters the
Rerun Theater library**. Episodes used to be JSON files hand-copied onto the
deploy host and bind-mounted read-only into every worker at `/data/replays`,
with nothing validating them anywhere in the loop. They are now uploaded
here: `POST /replays` takes a pre-built episode script
(`scripts/build_replay_library.py`), runs it through the four-stage gate in
`app/episode_validator.py` (shape → name → leak audit → **dry-run render**),
and only then inserts it into Postgres via `app/episode_store.py`. The
workers read the library straight from that table, so there is no mount and
no host filesystem access involved in adding a show.

## Signature

```python
class InjectMessage(BaseModel):
    to: str
    type: str = "operator_message"
    payload: dict = {}

@app.get("/healthz") -> dict
@app.post("/messages") def post_message(body: InjectMessage) -> dict

@app.get("/workers/{worker_id}") -> dict
@app.post("/workers/{worker_id}/enable") -> dict
@app.post("/workers/{worker_id}/disable") -> dict

@app.get("/log-filter/{message_type}") -> dict
@app.post("/log-filter/{message_type}/exclude") -> dict
@app.post("/log-filter/{message_type}/include") -> dict

class PruneLogsRequest(BaseModel):
    after: Optional[datetime] = None
    before: Optional[datetime] = None

@app.post("/logs/prune") def prune_logs_endpoint(body: PruneLogsRequest) -> dict

# Rerun Theater episode library (docs/episode_store.md)
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

@app.post("/replays") async def upload_replay(request: Request,
                                              name: Optional[str] = None,
                                              overwrite: bool = False) -> dict
# body is read via `await request.body()`, not a Body(...) param — see Changelog v1.4.1
@app.get("/replays") def list_replays() -> dict
@app.get("/replays/{name}") def get_replay(name: str) -> dict
@app.delete("/replays/{name}") def delete_replay(name: str) -> dict
```

## Parameters

- `to` (str, required) — target worker ID (`coder`/`manager`/`tester`) or `broadcast`.
- `type` (str, optional, default `"operator_message"`) — message type; can be overridden to inject any other documented type (e.g. `task_assignment`) for testing.
- `payload` (dict, optional, default `{}`) — free-form message body.
- `worker_id` (str, path param) — worker ID matching `WORKER_ID`/`message_bus.worker_id` (e.g. `coder`, `coder-native`, `manager`, `tester`).
- `message_type` (str, path param) — the message `type` field to filter (e.g. `status_update`, `task_complete`); accepts any string.
- `after` (datetime, optional) — deletes `container_logs` rows with `log_timestamp >= after`.
- `before` (datetime, optional) — deletes `container_logs` rows with `log_timestamp < before`.
  At least one of `after`/`before` is required; passing only one deletes everything on that side of the bound.

`POST /replays` takes the **raw episode JSON as the request body** —
`application/json`, no multipart wrapper — so `curl --data-binary @file.json`
uploads one directly and `python-multipart` isn't a dependency. Two optional
query parameters:

- `name` (str, optional) — overrides the library key. Defaults to the
  script's own `source` field, which is the same string the episode's
  filename stem used to be, so an unmodified episode keeps the key the rest
  of the stack already knows it by (including `voiced_narration.episode`).
- `overwrite` (bool, optional, default `false`) — replace an episode of the
  same name instead of failing with `409`.

`GET`/`DELETE /replays/{name}` take the library key as a path parameter,
validated against the same `^[A-Za-z0-9._-]{1,128}$` rule the upload path
applies, so a lookup can never be handed something an upload would refuse.

Environment variables (required at startup): `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`. Optional: `REDIS_URL` (default `redis://redis:6379`, used by the `/workers` and `/log-filter` endpoints). Required for `/logs/prune` **and `/replays`**: `POSTGRES_HOST`/`POSTGRES_PORT` (code default `localhost`/`5432` if unset, but `docker-compose.yml` requires both to be set explicitly in `.env` — e.g. `192.168.2.158`/`5432` for the d2000 deployment), `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`.

This service also owns the `replay_episodes` table's DDL at runtime: no
long-lived consumer owns it the way `message-logger` owns `messages`, so
`api.py` calls `episode_store.ensure_schema()` best-effort at import (logged,
never fatal) and retries on every `/replays` request until one succeeds — a
module-level flag stops it costing a DDL round-trip per request thereafter.
A Postgres that's down at container start must not stop `/messages` from
working, and must heal on its own once the database is back.

## Return Value

- `GET /healthz` — `{"status": "ok"}`.
- `POST /messages` — the full message envelope that was published (`id`, `from` (always `"operator"`), `to`, `type`, `payload`, `timestamp`), HTTP 200.
- `GET /workers/{worker_id}` — `{"worker_id": ..., "enabled": bool}`, HTTP 200. Defaults to `enabled: true` if the worker has never been toggled.
- `POST /workers/{worker_id}/enable` / `/disable` — same shape as the GET, reflecting the new state, HTTP 200.
- `GET /log-filter/{message_type}` — `{"type": ..., "excluded": bool}`, HTTP 200. Defaults to `excluded: true` for `status_update` and `false` for any other type that's never been toggled.
- `POST /log-filter/{message_type}/exclude` / `/include` — same shape as the GET, reflecting the new state, HTTP 200.
- `POST /logs/prune` — `{"deleted": int, "after": ..., "before": ...}`, HTTP 200.
- `POST /replays` — `{"name": str, "event_count": int, "byte_size": int, "created": true}`, HTTP 200.
- `GET /replays` — `{"episodes": [...]}` — one dict per episode with `name`, `project`, `session_id`, `date`, `event_count`, `byte_size`, `uploaded_by`, `uploaded_at`, sorted by name. No scripts.
- `GET /replays/{name}` — the full stored episode script, for debugging what a worker will actually perform, HTTP 200.
- `DELETE /replays/{name}` — `{"name": str, "deleted": bool}`, HTTP 200. `deleted: false` means the name was already absent (idempotent, not an error).
- Malformed/missing required fields — HTTP 422 (FastAPI/Pydantic validation).

## Dependencies

- `message_bus.build_message`, `message_bus.MessageProducer` (`app/message_bus.py`, copied into this service's image)
- `worker_control.WorkerControl` (`app/worker_control.py`, copied into this service's image; docs/worker_control.md)
- `log_filter_control.LogFilterControl` (`app/log_filter_control.py`, copied into this service's image; docs/log_filter_control.md)
- `log_prune.prune_logs` (`app/log_prune.py`, copied into this service's image; docs/log_shipper.md)
- `episode_store` and `episode_validator` (`app/episode_store.py`, `app/episode_validator.py`; docs/episode_store.md, docs/episode_validator.md). The validator's dry-run stage renders the episode, so the image also copies in `app/replay.py`, `app/revoice.py`, `app/session_log_parser.py`, `app/agent_state.py` and `app/audio_player.py` — all stdlib-only at import time, which is what makes running the renderer inside this service viable. No new pip dependency.
- `fastapi`, `uvicorn`, `pydantic`, `redis`, `psycopg2`

## Usage Examples

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "say hello"}}'
```

```bash
# Default type (operator_message), broadcast to all agents
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "broadcast", "payload": {"text": "stream starting in 5"}}'
```

```bash
# Turn the coder worker off (pauses the agent AND stops its Twitch stream),
# check its status, then turn it back on — no stack redeploy involved.
curl -X POST http://localhost:8090/workers/coder/disable
curl http://localhost:8090/workers/coder
curl -X POST http://localhost:8090/workers/coder/enable
```

```bash
# Heartbeat (status_update) messages are excluded from Postgres by default.
# Turn logging back on for them, check status, then re-exclude them:
curl -X POST http://localhost:8090/log-filter/status_update/include
curl http://localhost:8090/log-filter/status_update
curl -X POST http://localhost:8090/log-filter/status_update/exclude
```

```bash
# Delete container_logs rows from a known noisy window without waiting for
# the hourly age-based retention prune to reach them.
curl -X POST http://localhost:8090/logs/prune \
  -H "Content-Type: application/json" \
  -d '{"after": "2026-07-01T00:00:00Z", "before": "2026-07-02T00:00:00Z"}'
```

```bash
# Upload one episode built by scripts/build_replay_library.py. The body is
# the raw episode JSON — no multipart, no wrapper object.
curl -X POST http://localhost:8090/replays \
  -H "Content-Type: application/json" \
  --data-binary @replays/2026-07-02_04-27-00_6ecdde82.json
```

```bash
# Upload (or re-upload) a whole staging directory — this is also the
# migration command for a library that used to live in ./replays.
for f in replays/*.json; do
  echo -n "$(basename "$f" .json): "
  curl -sS -X POST http://localhost:8090/replays \
    -H 'Content-Type: application/json' --data-binary @"$f"
  echo
done
```

```bash
# See what's actually in the library, replace one episode, drop another.
curl -sS http://localhost:8090/replays | python3 -m json.tool
curl -X POST "http://localhost:8090/replays?overwrite=true" \
  -H "Content-Type: application/json" --data-binary @replays/sample.json
curl -X DELETE http://localhost:8090/replays/sample
```

## Error Handling

- Missing `to` field — HTTP 422 with a Pydantic validation error body.
- Kafka unreachable at startup — the process fails to construct `MessageProducer` and exits; `restart: unless-stopped` retries.
- Redis unreachable when reading status — `is_enabled` fails open, so `GET /workers/{id}` reports `enabled: true` rather than erroring.
- Redis unreachable when writing status — `enable`/`disable` return HTTP 503; the toggle did not take effect.
- Redis unreachable when reading a log filter — `is_excluded` falls back to `DEFAULT_EXCLUDED_TYPES`, so `GET /log-filter/{type}` keeps reporting `status_update` as excluded rather than erroring.
- Redis unreachable when writing a log filter — `exclude`/`include` return HTTP 503; the toggle did not take effect.
- `/logs/prune` called with neither `after` nor `before` — HTTP 400.
- `/logs/prune` called when Postgres is unreachable — HTTP 503; no rows deleted.
- `POST /replays` with a body over `MAX_UPLOAD_BYTES` (8 MB) — HTTP 413, checked on the raw body before parsing. uvicorn imposes no body-size limit of its own, and every upload is held in memory and then dry-run rendered; the largest real episode is well under 1 MB.
- `POST /replays` with a body that isn't valid JSON — HTTP 400.
- `POST /replays` with an episode that fails validation — HTTP 400 with the validator's reason (bad shape, bad name, over the size limit, leak audit, or a failed dry-run render; docs/episode_validator.md). **A leak-audit failure never echoes the matched text** — it is by construction the secret, so the `detail` names the rule only. `tests/test_episode_validator.py` asserts a planted secret does not appear in the message.
- `POST /replays` for a name that already exists, without `?overwrite=true` — HTTP 409; the stored episode is untouched.
- Any `/replays` call when `POSTGRES_*` isn't configured for this service — HTTP 503 before anything else runs.
- Any `/replays` call when Postgres is unreachable (`psycopg2.OperationalError`) — HTTP 503, mirroring `/logs/prune`. Nothing was written.
- `GET /replays/{name}` for an unknown episode — HTTP 404.
- `GET`/`DELETE /replays/{name}` with a name containing path separators or other disallowed characters — HTTP 400, before any query runs.

## Changelog

- v1.0.0 (2026-07-01) — Initial version.
- v1.1.0 (2026-07-07) — Added `/workers/{worker_id}` status and `/workers/{worker_id}/enable`/`disable` control endpoints, backed by `worker_control.WorkerControl`.
- v1.2.0 (2026-07-09) — Added `/log-filter/{message_type}` status and `/log-filter/{message_type}/exclude`/`include` control endpoints, backed by `log_filter_control.LogFilterControl`.
- v1.3.0 (2026-07-12) — Added `POST /logs/prune`, a manual time-range delete of `container_logs` rows backed by the new `app/log_prune.py`, complementing log-shipper's automatic age-based retention prune.
- v1.4.0 (2026-08-16) — Added the `/replays` endpoints: `POST` (validate + store an uploaded episode), `GET` (library listing), `GET /{name}` (full script) and `DELETE /{name}`, backed by the new `app/episode_store.py` and `app/episode_validator.py`. This service is now the only writer to the Rerun Theater episode library and owns the `replay_episodes` table's `CREATE TABLE IF NOT EXISTS`, replacing the `/data/replays` bind mount that used to carry episodes onto the workers (docs/replay_pane.md v2.0.0).
- v1.4.1 (2026-08-16) — Fixed: `POST /replays` returned `422 Input should be a valid bytes` for the exact call this doc and `scripts/build_replay_library.py` tell you to make (`curl -H 'Content-Type: application/json' --data-binary @file`). On fastapi 0.141.1 (pulled in by a previously-unpinned `fastapi>=0.110`), a `bytes`-typed `Body(...)` param gets JSON-decoded before its own type validator runs whenever the client's Content-Type is `application/json`, regardless of any `media_type=` hint passed to `Body()`. `upload_replay` now takes a `Request` and reads `await request.body()` directly, which always returns the raw bytes no matter the Content-Type header — `json.loads()` inside the handler is what actually parses it, same as before. `fastapi`/`starlette` are now pinned exact in `services/message-api/requirements.txt` so this doesn't silently drift again. No API or client-facing change — the documented curl commands now behave as documented. Needs a `message-api` image rebuild + redeploy.
