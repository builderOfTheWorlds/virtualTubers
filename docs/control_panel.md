# services/control-panel/panel.py

## Overview

Browser control panel for `services/message-api` (docs/message_api.md). It
is a second, small FastAPI app — server-rendered with Jinja2 + HTMX, no
Node/build step — that turns message-api's HTTP surface into buttons,
toggles, forms, and tables an operator can drive from a browser instead of
`curl`.

Every route is a thin wrapper around exactly one message-api call, made
through a single chokepoint (`_mapi_request`). **This service never touches
Redis, Kafka, or Postgres directly** — the same trust boundary message-api's
own module docstring describes, just with a friendlier client in front of
it. No changes were made to message-api itself; this is purely additive.

Sections on the one dashboard page (`GET /`):

- **Workers** — enable/disable each of the six known worker IDs
  (docs/worker_control.md), auto-refreshed every 10s.
- **Send a message** — compose an arbitrary Kafka message (`to`/`type`/JSON
  `payload`), same shape as `POST /messages`.
- **Log filter** — exclude/include a message type from `message-logger`'s
  Postgres writes (docs/log_filter_control.md).
- **Prune container logs** — a manual time-range delete of `container_logs`
  rows (docs/log_shipper.md).
- **Rerun Theater replays** — list, upload, view, and delete episodes in the
  library (docs/episode_store.md).

## Signature

```python
GET  /healthz -> dict                                   # bypasses basic auth

GET  /                        -> HTML   # full dashboard
GET  /partials/workers        -> HTML   # workers table fragment (polled every 10s)
POST /workers/{worker_id}/enable  -> HTML   # single updated <tr>
POST /workers/{worker_id}/disable -> HTML

POST /log-filter/add                        -> HTML   # form: message_type
POST /log-filter/{message_type}/exclude     -> HTML   # single updated <tr>
POST /log-filter/{message_type}/include     -> HTML

POST /messages -> HTML   # form: to, type, payload (JSON text)

POST /logs/prune -> HTML   # form: after, before (datetime-local strings)

GET  /partials/replays          -> HTML   # replays section fragment
POST /replays/upload            -> HTML   # form: file, name, overwrite
POST /replays/{name}/delete     -> HTML   # empty body on success (row removed), row+error on failure
GET  /replays/{name}/view       -> HTML   # pretty-printed script fragment
```

Internal:

```python
class MapiResult:
    ok: bool
    status_code: int
    data: Any = None
    error: Optional[str] = None

async def _mapi_request(method: str, path: str, **kwargs) -> MapiResult
```

## Parameters

- `MESSAGE_API_URL` (env, default `http://message-api:8000`) — base URL for
  every outbound call, same pattern `services/twitch-presence` uses for its
  own calls to `POST /messages`.
- `CONTROL_PANEL_BASIC_AUTH_USER` / `CONTROL_PANEL_BASIC_AUTH_PASS` (env,
  both optional) — HTTP Basic Auth. The auth middleware no-ops unless
  **both** are set; `GET /healthz` is always exempt so container health
  checks don't need credentials.
- `WORKER_IDS` / `MESSAGE_TYPE_EXAMPLES` — hardcoded lists mirroring
  `services/message-api/api.py`'s own `WORKER_ID_EXAMPLES` /
  `MESSAGE_TYPE_EXAMPLES`, for the same reason stated there: message-api
  exposes no "list workers" or "list message types" endpoint, and these can
  drift from `docker-compose.yml`.
- `KNOWN_LOG_TYPES` — in-memory list of message types shown in the Log
  Filter table, seeded with `status_update` (the one type
  `log_filter_control.py` excludes by default). Growing this list via the
  "Track type" form is **not persisted** — it resets on container restart,
  same as any other in-memory-only state in this service.

## Return Value

Every route returns rendered HTML (an HTMX fragment, or the full page for
`GET /`) — never raw JSON, except `GET /healthz` (`{"status": "ok"}`, for
container health checks / consistency with message-api's own).

## Dependencies

- `fastapi`, `starlette` (pinned exact, matching `services/message-api`'s
  pins and its documented reason), `uvicorn`, `jinja2`, `httpx`,
  `python-multipart` (the replay upload form)
- `services/message-api` (docs/message_api.md) — the only backend this talks
  to
- Vendored `static/htmx.min.js` (htmx 2.0.4, BSD-2-Clause) — kept local
  rather than loaded from a CDN, so the dashboard works even if outbound
  internet is down mid-stream

## Usage Examples

```bash
# Start alongside the rest of the stack
docker compose up -d control-panel
# Dashboard at:
open http://localhost:8091
```

```bash
# Equivalent curl calls the panel's buttons wrap, for reference —
# see docs/message_api.md for the authoritative list.
curl -X POST http://localhost:8090/workers/coder/disable
curl -X POST http://localhost:8090/log-filter/status_update/include
curl -X POST http://localhost:8090/logs/prune \
  -H "Content-Type: application/json" \
  -d '{"after": "2026-07-01T00:00:00Z"}'
```

```bash
# Gate the panel behind a login before exposing it beyond the LAN
echo "CONTROL_PANEL_BASIC_AUTH_USER=operator" >> .env
echo "CONTROL_PANEL_BASIC_AUTH_PASS=$(openssl rand -hex 16)" >> .env
docker compose up -d control-panel
```

## Error Handling

- message-api unreachable (connection refused/timeout) — `_mapi_request`
  catches `httpx.RequestError` and returns `MapiResult(ok=False,
  status_code=0, error="message-api unreachable: ...")`; every route renders
  that as an inline error badge/banner instead of raising. The dashboard
  itself (`GET /`) still renders — sections just show per-item error state —
  rather than 500ing wholesale.
- message-api returns 4xx/5xx — the `detail` field (FastAPI/Pydantic's
  standard error shape) is extracted and shown verbatim; falls back to
  `"message-api returned HTTP {status}"` if the body has no `detail`.
- `POST /messages` with a `payload` field that isn't valid JSON, or isn't a
  JSON *object* — rejected **before** calling message-api, with an inline
  error; message-api is never contacted for a client-side-catchable mistake.
- Every destructive action (disable a worker, delete a replay, prune logs)
  has an `hx-confirm` prompt in the browser before the request is even
  sent — no server-side undo exists for any of them, same as the endpoints
  they wrap.
- Missing/wrong Basic Auth credentials (when configured) — HTTP 401 with a
  `WWW-Authenticate` header, timing-safe compared via `secrets.compare_digest`.

## Changelog

- v1.0.0 (2026-08-17) — Initial version. Dashboard covering workers,
  log-filter, message injection, log pruning, and the Rerun Theater replay
  library, all proxied through `services/message-api`. Optional HTTP Basic
  Auth via `CONTROL_PANEL_BASIC_AUTH_USER`/`_PASS`.
