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
```

## Parameters

- `to` (str, required) — target worker ID (`coder`/`manager`/`tester`) or `broadcast`.
- `type` (str, optional, default `"operator_message"`) — message type; can be overridden to inject any other documented type (e.g. `task_assignment`) for testing.
- `payload` (dict, optional, default `{}`) — free-form message body.
- `worker_id` (str, path param) — worker ID matching `WORKER_ID`/`message_bus.worker_id` (e.g. `coder`, `coder-native`, `manager`, `tester`).

Environment variables (required at startup): `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`. Optional: `REDIS_URL` (default `redis://redis:6379`, used by the `/workers` endpoints).

## Return Value

- `GET /healthz` — `{"status": "ok"}`.
- `POST /messages` — the full message envelope that was published (`id`, `from` (always `"operator"`), `to`, `type`, `payload`, `timestamp`), HTTP 200.
- `GET /workers/{worker_id}` — `{"worker_id": ..., "enabled": bool}`, HTTP 200. Defaults to `enabled: true` if the worker has never been toggled.
- `POST /workers/{worker_id}/enable` / `/disable` — same shape as the GET, reflecting the new state, HTTP 200.
- Malformed/missing required fields — HTTP 422 (FastAPI/Pydantic validation).

## Dependencies

- `message_bus.build_message`, `message_bus.MessageProducer` (`app/message_bus.py`, copied into this service's image)
- `worker_control.WorkerControl` (`app/worker_control.py`, copied into this service's image; docs/worker_control.md)
- `fastapi`, `uvicorn`, `pydantic`, `redis`

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

## Error Handling

- Missing `to` field — HTTP 422 with a Pydantic validation error body.
- Kafka unreachable at startup — the process fails to construct `MessageProducer` and exits; `restart: unless-stopped` retries.
- Redis unreachable when reading status — `is_enabled` fails open, so `GET /workers/{id}` reports `enabled: true` rather than erroring.
- Redis unreachable when writing status — `enable`/`disable` return HTTP 503; the toggle did not take effect.

## Changelog

- v1.0.0 (2026-07-01) — Initial version.
- v1.1.0 (2026-07-07) — Added `/workers/{worker_id}` status and `/workers/{worker_id}/enable`/`disable` control endpoints, backed by `worker_control.WorkerControl`.
