# services/message-api/api.py

## Overview

Minimal HTTP interface for injecting test messages onto the Kafka bus, so an
operator (or another external system, later) can prompt a specific agent
without needing direct Kafka tooling. Pure producer — it never touches
Postgres or the filesystem directly; the separate `message-logger` service is
responsible for durable logging of everything it (and everyone else) produces.

## Signature

```python
class InjectMessage(BaseModel):
    to: str
    type: str = "operator_message"
    payload: dict = {}

@app.get("/healthz") -> dict
@app.post("/messages") def post_message(body: InjectMessage) -> dict
```

## Parameters

- `to` (str, required) — target worker ID (`coder`/`manager`/`tester`) or `broadcast`.
- `type` (str, optional, default `"operator_message"`) — message type; can be overridden to inject any other documented type (e.g. `task_assignment`) for testing.
- `payload` (dict, optional, default `{}`) — free-form message body.

Environment variables (required at startup): `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`.

## Return Value

- `GET /healthz` — `{"status": "ok"}`.
- `POST /messages` — the full message envelope that was published (`id`, `from` (always `"operator"`), `to`, `type`, `payload`, `timestamp`), HTTP 200.
- Malformed/missing required fields — HTTP 422 (FastAPI/Pydantic validation).

## Dependencies

- `message_bus.build_message`, `message_bus.MessageProducer` (`app/message_bus.py`, copied into this service's image)
- `fastapi`, `uvicorn`, `pydantic`

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

## Error Handling

- Missing `to` field — HTTP 422 with a Pydantic validation error body.
- Kafka unreachable at startup — the process fails to construct `MessageProducer` and exits; `restart: unless-stopped` retries.

## Changelog

- v1.0.0 (2026-07-01) — Initial version.
