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
  (docs/worker_control.md), auto-refreshed every 10s. A **health** column
  (from message-api's `GET /workers/health`, one call per refresh) shows
  `alive` / `stale` with "last seen Ns ago", `down` (no heartbeat within the
  worker's liveness TTL) or `unknown` (message-api/Redis unreachable), and a
  red **local kill-switch engaged** badge when the worker reports its
  `WORKER_KILL_FILE` exists. That switch lives inside the worker container,
  so the panel's Enable button cannot override it — the badge says to clear
  it with `scripts/emergency_resume.sh <worker>`.
- **Send a message** — compose an arbitrary Kafka message (`to`/`type`/JSON
  `payload`), same shape as `POST /messages`.
- **Log filter** — exclude/include a message type from `message-logger`'s
  Postgres writes (docs/log_filter_control.md).
- **Prune container logs** — a manual time-range delete of `container_logs`
  rows (docs/log_shipper.md).
- **Rerun Theater replays** — list, upload, view, and delete episodes in the
  library (docs/episode_store.md). Hitting Play shows a live, auto-refreshing
  log viewer (container stdout/stderr + Kafka bus messages for the 7
  targeted streams, docs/replay_logs.md) underneath the play banner.
  Above the library table, **Drafts awaiting review** lists episodes stored
  with `status=draft` (e.g. auto-submitted by the 3layer-generator,
  docs/draft_submitter.md) with **View / Approve / Delete** — and no Play
  button: a draft never airs until it is approved. Approve moves it into
  the library table below; Delete rejects it (message-api's existing
  `DELETE /replays/{name}`).
- **Console theme** — live-switch a worker's terminal color scheme (any of
  the 1247 Gogh schemes, see `app/console_theme.py`), auto-refreshed every
  15s. Applies without a redeploy or stream interruption
  (`app/theme_watcher.py` repaints the running xterm via an OSC escape
  sequence). Covers all 8 worker containers, including `tuber_0` (the GM's
  own channel) and `roundtable` — both run `theme_watcher.py` unconditionally
  even though they're excluded from the Workers/message-composer sections
  above (which only address the six character-worker IDs valid for
  `operator_message`/`replay_request`).
- **Music** — GM live control of the roundtable's background score
  (docs/music_engine.md), auto-refreshed every 5s. Shows whether
  `music_director.py` is running (its now-playing heartbeat: mood, tempo,
  mode, theme, source, scene, bar, recording session) and whether a GM
  override is active or the score is following scene moods. "Hold mood"
  pins a mood (9 GEMS moods, `neutral`, or `silence` to fade out) at an
  intensity slider value; "Follow scene" clears the override. Targets
  `roundtable` only — the one container running the music director.

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
GET  /replays/{name}/log        -> HTML   # merged container-log + bus-message tail, polled every 3s after Play
POST /replays/{name}/approve    -> HTML   # promote a draft; re-renders the whole replays section
POST /replays/{name}/reject     -> HTML   # delete a draft; empty body on success, draft row+error on failure

GET  /partials/theme-workers            -> HTML   # theme table fragment (polled every 15s)
POST /console-theme/{worker_id}         -> HTML   # form: theme; single updated <tr>
POST /console-theme/{worker_id}/clear   -> HTML   # revert to config default; single updated <tr>

GET  /partials/music   -> HTML   # music card fragment (polled every 5s)
POST /music/set        -> HTML   # form: mood, intensity (0..1); re-rendered card
POST /music/clear      -> HTML   # follow scene moods again; re-rendered card
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
- `THEME_WORKER_IDS` — `WORKER_IDS` plus `tuber_0` and `roundtable`. Both run
  `app/theme_watcher.py` unconditionally at boot (`startup.sh` §7.6) and so
  are valid live-retheme targets even though they're deliberately excluded
  from `WORKER_IDS` for message-composer/replay-play purposes (see
  `panel.py`'s own comment on `WORKER_TO_TUBER_SLOT`).
- `MUSIC_WORKER_ID` — `roundtable`, the only container running
  `app/music_director.py`. `MUSIC_MOODS_FALLBACK` mirrors message-api's
  `GET /music-moods` so the mood picker still renders if that call fails;
  a successful fetch is cached in-memory for the process lifetime.
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
curl -X POST http://localhost:8090/music/roundtable \
  -H "Content-Type: application/json" -d '{"mood": "tension", "intensity": 0.7}'
curl -X DELETE http://localhost:8090/music/roundtable
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
- Music set/clear failures (unknown mood → 400, Redis down → 503, or
  message-api unreachable) re-render the card with the error in a banner
  above the current (re-read) state, so the GM sees both that the change
  didn't land and what is actually playing.
- The dashboard and the replays section make two listing calls:
  `GET /replays` (approved library) and `GET /replays?status=draft` (review
  queue). Either can fail independently — the other table still renders,
  with its own error banner.
- `GET /workers/health` failing (message-api down, or an older message-api
  without the route) — every worker's health cell shows `unknown`; the
  on/off status column is unaffected (separate per-worker calls).
- Every destructive action (disable a worker, delete a replay, prune logs)
  has an `hx-confirm` prompt in the browser before the request is even
  sent — no server-side undo exists for any of them, same as the endpoints
  they wrap.
- Missing/wrong Basic Auth credentials (when configured) — HTTP 401 with a
  `WWW-Authenticate` header, timing-safe compared via `secrets.compare_digest`.

## Changelog

- v1.4.0 (2026-09-27) — Worker health in the Workers table: new health
  column (alive/stale/down/unknown + "last seen Ns ago" via the new `age`
  Jinja filter, `format_age()`) and a local kill-switch badge pointing at
  `scripts/emergency_resume.sh`. Fed by message-api's new
  `GET /workers/health` (dashboard + `/partials/workers`) and
  `GET /workers/{id}/health` (re-read after an Enable/Disable so the
  swapped-in row keeps its badges). New helpers `_workers_health`,
  `_worker_health`, `_workers_view`; CSS `.badge.warn` / `.badge.kill`.
  Also fixed `tests/test_control_panel.py::test_dashboard_renders_worker_and_replay_data`,
  a stale test: its strict message-api fake predated the v1.1.0 Console
  theme section and raised on the dashboard's legitimate
  `GET /console-themes` call (plus a module-level theme-name cache made the
  outcome test-order dependent — now reset per test). No panel bug.
- v1.3.0 (2026-09-27) — Music section: GM live control of the roundtable's
  background score via message-api's new `/music-moods` and
  `/music/{worker_id}` endpoints (`app/music/control.py`). New template
  `_music_card.html`; routes `GET /partials/music`, `POST /music/set`,
  `POST /music/clear`. No image change beyond the new template.
- v1.3.0 (2026-09-27) — Draft review gate: a "Drafts awaiting review"
  table in the Rerun Theater section (new `_draft_row.html`), fed by
  message-api's `GET /replays?status=draft`, with View / Approve / Delete.
  New routes `POST /replays/{name}/approve` (wraps message-api's new
  `POST /replays/{name}/approve`, re-renders the section so the episode
  moves into the library) and `POST /replays/{name}/reject` (wraps the
  existing `DELETE /replays/{name}`, re-renders a draft row on error).
  Drafts never get a Play button. The upload form is unchanged — operator
  uploads are still stored `approved` and air immediately.

- v1.2.0 (2026-09-27) — Rerun Theater "Play" now shows a live log viewer:
  a new `GET /replays/{name}/log` route merges `message-api`'s
  `GET /logs/containers` (container stdout/stderr for the 7 targeted
  worker/roundtable containers) and `GET /logs/messages` (bus messages
  to/from those same 7 ids) into one chronological tail, rendered in the
  replays section under the play banner and auto-refreshed every 3s
  (docs/replay_logs.md).
- v1.1.0 (2026-09-24) — Console theme section: live-switch/clear a worker's
  terminal color scheme via `services/message-api`'s
  `/console-theme(s)` endpoints (`app/console_theme.py`). Covers all 8
  worker containers via the new `THEME_WORKER_IDS`.
- v1.0.0 (2026-08-17) — Initial version. Dashboard covering workers,
  log-filter, message injection, log pruning, and the Rerun Theater replay
  library, all proxied through `services/message-api`. Optional HTTP Basic
  Auth via `CONTROL_PANEL_BASIC_AUTH_USER`/`_PASS`.
