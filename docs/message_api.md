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
the one endpoint that *does* touch Postgres directly (a deliberate exception
to the "pure producer" design above): it complements log-shipper's own
hourly `RETENTION_DAYS`-based prune (docs/log_shipper.md), which only ever
deletes by age, for reclaiming space from a known window without waiting for
the retention cutoff to catch up.

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

Environment variables (required at startup): `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`. Optional: `REDIS_URL` (default `redis://redis:6379`, used by the `/workers` and `/log-filter` endpoints). Required for `/logs/prune`: `POSTGRES_HOST`/`POSTGRES_PORT` (code default `localhost`/`5432` if unset, but `docker-compose.yml` requires both to be set explicitly in `.env` — e.g. `192.168.2.158`/`5432` for the d2000 deployment), `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`.

## Return Value

- `GET /healthz` — `{"status": "ok"}`.
- `POST /messages` — the full message envelope that was published (`id`, `from` (always `"operator"`), `to`, `type`, `payload`, `timestamp`), HTTP 200.
- `GET /workers/{worker_id}` — `{"worker_id": ..., "enabled": bool}`, HTTP 200. Defaults to `enabled: true` if the worker has never been toggled.
- `POST /workers/{worker_id}/enable` / `/disable` — same shape as the GET, reflecting the new state, HTTP 200.
- `GET /log-filter/{message_type}` — `{"type": ..., "excluded": bool}`, HTTP 200. Defaults to `excluded: true` for `status_update` and `false` for any other type that's never been toggled.
- `POST /log-filter/{message_type}/exclude` / `/include` — same shape as the GET, reflecting the new state, HTTP 200.
- `POST /logs/prune` — `{"deleted": int, "after": ..., "before": ...}`, HTTP 200.
- Malformed/missing required fields — HTTP 422 (FastAPI/Pydantic validation).

## Dependencies

- `message_bus.build_message`, `message_bus.MessageProducer` (`app/message_bus.py`, copied into this service's image)
- `worker_control.WorkerControl` (`app/worker_control.py`, copied into this service's image; docs/worker_control.md)
- `log_filter_control.LogFilterControl` (`app/log_filter_control.py`, copied into this service's image; docs/log_filter_control.md)
- `log_prune.prune_logs` (`app/log_prune.py`, copied into this service's image; docs/log_shipper.md)
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

## Error Handling

- Missing `to` field — HTTP 422 with a Pydantic validation error body.
- Kafka unreachable at startup — the process fails to construct `MessageProducer` and exits; `restart: unless-stopped` retries.
- Redis unreachable when reading status — `is_enabled` fails open, so `GET /workers/{id}` reports `enabled: true` rather than erroring.
- Redis unreachable when writing status — `enable`/`disable` return HTTP 503; the toggle did not take effect.
- Redis unreachable when reading a log filter — `is_excluded` falls back to `DEFAULT_EXCLUDED_TYPES`, so `GET /log-filter/{type}` keeps reporting `status_update` as excluded rather than erroring.
- Redis unreachable when writing a log filter — `exclude`/`include` return HTTP 503; the toggle did not take effect.
- `/logs/prune` called with neither `after` nor `before` — HTTP 400.
- `/logs/prune` called when Postgres is unreachable — HTTP 503; no rows deleted.

## Changelog

- v1.0.0 (2026-07-01) — Initial version.
- v1.1.0 (2026-07-07) — Added `/workers/{worker_id}` status and `/workers/{worker_id}/enable`/`disable` control endpoints, backed by `worker_control.WorkerControl`.
- v1.2.0 (2026-07-09) — Added `/log-filter/{message_type}` status and `/log-filter/{message_type}/exclude`/`include` control endpoints, backed by `log_filter_control.LogFilterControl`.
- v1.3.0 (2026-07-12) — Added `POST /logs/prune`, a manual time-range delete of `container_logs` rows backed by the new `app/log_prune.py`, complementing log-shipper's automatic age-based retention prune.
