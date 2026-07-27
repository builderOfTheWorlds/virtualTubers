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

Also exposes the `/campaigns` control endpoints — runtime persona
assignment for the generic `worker-1`..`worker-8` fleet (campaign_platform_contract.md §8,
docs/campaign_control.md, docs/blank_workers.md): which campaign is active
and which worker plays which speaker, backed by `campaign_control.py`. This
service **never reads a campaign's `personas.yaml`** — it only stores
`{campaign, speaker}` pairs in Redis; each worker resolves its own persona
doc from its own mounted config (`app/agent.py`), which is what keeps this
service dumb about persona content.

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

class StartCampaignRequest(BaseModel):
    cast: Dict[str, str]
    force: bool = False

@app.post("/campaigns/{campaign}/start") def start_campaign(body: StartCampaignRequest, campaign: str) -> dict
@app.post("/campaigns/stop") def stop_campaign() -> dict
@app.get("/campaigns/active") def get_active_campaign() -> dict
```

## Parameters

- `to` (str, required) — target worker ID (`worker-1`..`worker-8`, or a
  persona name if it happens to be the display name currently overlaid
  onto one — routing is always by `WORKER_ID`, docs/blank_workers.md) or `broadcast`.
- `type` (str, optional, default `"operator_message"`) — message type; can be overridden to inject any other documented type (e.g. `task_assignment`) for testing.
- `payload` (dict, optional, default `{}`) — free-form message body.
- `worker_id` (str, path param) — worker ID matching `WORKER_ID`/`message_bus.worker_id` (`worker-1`..`worker-8` under the generic-worker fleet — `WORKER_ID_EXAMPLES` genericized from the old hardcoded `coder`/`coder-native`/.../`tester` dropdown once those became persona names rather than worker identities).
- `message_type` (str, path param) — the message `type` field to filter (e.g. `status_update`, `task_complete`); accepts any string.
- `after` (datetime, optional) — deletes `container_logs` rows with `log_timestamp >= after`.
- `before` (datetime, optional) — deletes `container_logs` rows with `log_timestamp < before`.
  At least one of `after`/`before` is required; passing only one deletes everything on that side of the bound.
- `campaign` (str, path param, `/campaigns/{campaign}/start`) — a `config/campaigns/<name>/` directory name (`coder`, `dnd`, ...); any string accepted, `CAMPAIGN_EXAMPLES` is a `/docs` dropdown hint only.
- `cast` (dict[str, str], required, `StartCampaignRequest`) — `{speaker_id: worker_id}`, e.g. `{"coder": "worker-1", "manager": "worker-5"}`.
- `force` (bool, optional, default `False`, `StartCampaignRequest`) — bypass the mid-airing guard (see Error Handling) for the named worker(s).

Environment variables (required at startup): `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`. Optional: `REDIS_URL` (default `redis://redis:6379`, used by the `/workers`, `/log-filter`, and `/campaigns` endpoints). Required for `/logs/prune`: `POSTGRES_HOST`/`POSTGRES_PORT` (code default `localhost`/`5432` if unset, but `docker-compose.yml` requires both to be set explicitly in `.env` — e.g. `192.168.2.158`/`5432` for the d2000 deployment), `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`.

## Return Value

- `GET /healthz` — `{"status": "ok"}`.
- `POST /messages` — the full message envelope that was published (`id`, `from` (always `"operator"`), `to`, `type`, `payload`, `timestamp`), HTTP 200.
- `GET /workers/{worker_id}` — `{"worker_id": ..., "enabled": bool}`, HTTP 200. Defaults to `enabled: true` if the worker has never been toggled.
- `POST /workers/{worker_id}/enable` / `/disable` — same shape as the GET, reflecting the new state, HTTP 200.
- `GET /log-filter/{message_type}` — `{"type": ..., "excluded": bool}`, HTTP 200. Defaults to `excluded: true` for `status_update` and `false` for any other type that's never been toggled.
- `POST /log-filter/{message_type}/exclude` / `/include` — same shape as the GET, reflecting the new state, HTTP 200.
- `POST /logs/prune` — `{"deleted": int, "after": ..., "before": ...}`, HTTP 200.
- `POST /campaigns/{campaign}/start` — `{"campaign": ..., "cast": {...}}`, HTTP 200. HTTP 409 if any named worker's `worker:{id}:airing` flag is set and `force` isn't `true`; HTTP 503 on `redis.RedisError`.
- `POST /campaigns/stop` — `{"stopped": true, "campaign": <previous campaign name, or null>}`, HTTP 200. HTTP 503 on `redis.RedisError`.
- `GET /campaigns/active` — `{"campaign": ..., "cast": {...}}` when a campaign is active, else `{"campaign": null}`, HTTP 200 always (`CampaignControl.get_active()` fails open — this read never 503s).
- Malformed/missing required fields — HTTP 422 (FastAPI/Pydantic validation).

## Dependencies

- `message_bus.build_message`, `message_bus.MessageProducer` (`app/message_bus.py`, copied into this service's image)
- `worker_control.WorkerControl` (`app/worker_control.py`, copied into this service's image; docs/worker_control.md) — also reused directly by `/campaigns/{campaign}/start`'s mid-airing check (`control._client.get("worker:{id}:airing")`)
- `campaign_control.CampaignControl` (`app/campaign_control.py`, copied into this service's image; docs/campaign_control.md)
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

```bash
# Cast the coder-campaign personas onto matching generic workers, check
# what's active, then send everyone back to blank (docs/campaign_control.md,
# docs/blank_workers.md's worker-number convention):
curl -X POST http://localhost:8090/campaigns/coder/start \
  -H "Content-Type: application/json" \
  -d '{"cast": {"coder": "worker-1", "coder-native": "worker-2",
                 "coder-opencode": "worker-3", "coder-aider": "worker-4",
                 "manager": "worker-5", "tester": "worker-6"}}'

curl http://localhost:8090/campaigns/active

curl -X POST http://localhost:8090/campaigns/stop
```

```bash
# Reassigning a worker that's mid-airing refuses with 409 unless you mean it:
curl -X POST http://localhost:8090/campaigns/dnd/start \
  -H "Content-Type: application/json" \
  -d '{"cast": {"gm": "worker-1"}}'
# -> 409 if worker-1 is currently performing a Rerun Theater episode

curl -X POST http://localhost:8090/campaigns/dnd/start \
  -H "Content-Type: application/json" \
  -d '{"cast": {"gm": "worker-1"}, "force": true}'
# -> 200, reassigns worker-1 anyway
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
- **`POST /campaigns/{campaign}/start` refuses a mid-airing reassignment.**
  "Mid-airing" means that worker's `worker:{id}:airing` flag is set —
  written by `app/replay_pane.py` for the duration of one performance,
  cleared in a `finally` so a crashed show can never wedge a worker
  un-reassignable forever (docs/replay_pane.md). The check fails OPEN
  (Redis unreachable or the flag absent both mean "not airing") — a
  control-plane hiccup checking airing state must never block a
  legitimate campaign reassignment, same posture as `worker_control`'s own
  `is_enabled`. `force: true` bypasses the check outright.
- Redis unreachable when reading the active campaign — `GET /campaigns/active`
  fails open (`CampaignControl.get_active()`), returning
  `{"campaign": null}` rather than erroring — this is the one `/campaigns`
  route that never 503s.
- Redis unreachable when writing a campaign assignment — `start`/`stop`
  return HTTP 503; the assignment/clear did not take effect
  (`CampaignControl`'s writes propagate `redis.RedisError` rather than
  failing open — docs/campaign_control.md).

## Changelog

- v1.0.0 (2026-07-01) — Initial version.
- v1.1.0 (2026-07-07) — Added `/workers/{worker_id}` status and `/workers/{worker_id}/enable`/`disable` control endpoints, backed by `worker_control.WorkerControl`.
- v1.2.0 (2026-07-09) — Added `/log-filter/{message_type}` status and `/log-filter/{message_type}/exclude`/`include` control endpoints, backed by `log_filter_control.LogFilterControl`.
- v1.3.0 (2026-07-12) — Added `POST /logs/prune`, a manual time-range delete of `container_logs` rows backed by the new `app/log_prune.py`, complementing log-shipper's automatic age-based retention prune.
- v1.4.0 (2026-07-26, campaign_platform_contract.md §8) — Added `POST /campaigns/{campaign}/start`, `POST /campaigns/stop`, `GET /campaigns/active`, backed by the new `app/campaign_control.py` (docs/campaign_control.md). `start` reads `worker:{id}:airing` (written by `app/replay_pane.py`) to refuse a mid-airing reassignment with HTTP 409 unless `force: true`. `WORKER_ID_EXAMPLES` genericized from the old hardcoded 6-persona-named dropdown (`coder`/`coder-native`/.../`tester`) to `worker-1`..`worker-8`, now that persona names are assigned at runtime rather than baked into a worker's identity (docs/blank_workers.md).
