# app/worker_control.py

## Overview

Redis-backed enable/disable flag per worker — the mechanism behind turning a
worker on/off without redeploying the stack. One key per worker
(`worker:{worker_id}:enabled`), read by `app/agent.py`'s tick loop and
`app/stream_supervisor.py`'s ffmpeg loop, written by `services/message-api`'s
`/workers/{worker_id}/enable`/`disable` endpoints (docs/message_api.md).

Reads and writes are asymmetric on purpose: `is_enabled` **fails open**
(returns `True`) if Redis is unreachable or the key is missing, so a
control-plane outage or a worker nobody has toggled yet never silently kills
a live stream. `set_enabled` does **not** fail open — it raises so the
caller (the API) can tell the operator the toggle didn't take effect.

**Local kill switch (works with Redis down).** Fail-open has a cost: with
Redis unreachable, the operator can no longer turn a live stream *off*. So
each worker container also honours a **local kill file** — `WORKER_KILL_FILE`
(default `/tmp/worker_disabled`). While it exists, `is_enabled()` returns
`False` **without consulting Redis**, whatever Redis says and whether or not
Redis is reachable. Because the check lives here, both consumers get it:
`agent.py` pauses its loop and `stream_supervisor.py` stops ffmpeg (it also
stats the file every 0.5s between polls, and treats `SIGUSR1` as "write the
kill file and stop now" — docs/stream_supervisor.md). With no kill file,
behaviour is exactly the Redis path above, fail-open included.

Operator path needing only Docker (no Redis, no message-api):

```bash
scripts/emergency_stop.sh            # all running worker-* containers
scripts/emergency_stop.sh coder gm   # specific workers (suffix, service or container name)
scripts/emergency_resume.sh coder    # remove the kill file again
```

`scripts/emergency_stop.ps1` / `emergency_resume.ps1` do the same over SSH
from Windows (`-Worker coder,gm`; `-StopStack` keeps the old whole-stack
`docker compose stop`). The scripts are idempotent, report per container, and
`emergency_stop.sh` verifies `ffmpeg` is gone (warns if an older image
ignores the file). Removing the kill file only hands control back to Redis:
the worker streams again only if `worker:{id}:enabled` isn't `"0"` (or Redis
is down → fail-open).

**Lifetime.** `startup.sh`'s `/tmp` cleanup only removes the X11 lock/socket
and `pulse-*`, so the kill file survives `agent.py`/supervisor restarts and a
plain `docker restart` (the supervisor logs
`WARN event=local_override_active_at_startup` and never starts ffmpeg). It
does **not** survive container re-creation (`docker compose up` with a
changed spec, `./redeploy.sh`) — the container's writable layer is replaced
and the worker comes back under Redis control.

**Scope.** The file is per container, not per worker id: every
`WorkerControl` in a process that can see the file reports *every* id as
disabled. That is right for worker containers (one worker each); never
create it in the `message-api` container, whose `GET /workers/{id}` would
then report all workers disabled.

**Liveness key (v1.2.0).** Separately from the enable flag, `agent.py`
writes `worker:{worker_id}:alive` every tick — value: the tick's ISO-8601
UTC timestamp, with a TTL (`SET ... EX ttl_s`). The key existing means "the
agent loop ticked within the last `ttl_s` seconds"; it expires on its own
when the loop dies or wedges, so no reaper is needed. This replaces
inferring liveness from the per-tick `status_update` bus heartbeat (now
rate-limited by `agent.bus_heartbeat_every`, docs/agent.md). TTL: config
`agent.liveness_ttl_s`, default `max(3 × tick, 15s)`. It is written even
while the worker is disabled — "alive" (process ticking) and "enabled"
(operator toggle) are independent.

- `heartbeat()` never raises: a Redis outage logs
  `WARN event=liveness_write_failed` once and `INFO event=liveness_write_recovered`
  once when writes succeed again.
- `last_seen()` / `alive()` return `None` / `False` when the key expired,
  was never written, or Redis is unreachable — "unknown" is never reported
  as alive, and readers stay silent so a polling health view can't flood the log.

**Reader API for a health view** (message-api / control-panel — not wired
yet): construct `WorkerControl.from_config(config)` (or
`WorkerControl(redis_url)`) once, then per worker id call
`control.last_seen(worker_id)` → ISO timestamp string or `None`, and/or
`control.alive(worker_id)` → bool. Combine with `is_enabled()` and
`local_override_active()` for a full status row. Worker ids are the same
`WORKER_ID`s used on the bus (`coder`, `tester`, `manager`, ...).

## Signature

```python
def resolve_redis_url(config=None, env_name="REDIS_URL", default="redis://redis:6379") -> str
def resolve_kill_file(config=None, env_name="WORKER_KILL_FILE", default="/tmp/worker_disabled") -> str

class WorkerControl:
    def __init__(self, redis_url: str, socket_timeout: int = 2, kill_file: str | None = None)
    kill_file: str  # resolved path

    @classmethod
    def from_config(cls, config=None) -> "WorkerControl"

    def is_enabled(self, worker_id: str) -> bool
    def set_enabled(self, worker_id: str, enabled: bool) -> bool
    def local_override_active(self) -> bool
    def engage_local_override(self, reason: str = "manual") -> None
    def release_local_override(self) -> bool

    # liveness (v1.2.0)
    def heartbeat(self, worker_id: str, ttl_s: int) -> bool
    def last_seen(self, worker_id: str) -> str | None
    def alive(self, worker_id: str) -> bool
```

## Parameters

- `config` (dict, optional) — a loaded worker config (`message_bus.load_worker_config`);
  `resolve_redis_url` reads `config["world_state"]["redis_url"]` as a fallback.
- `env_name` (str, default `"REDIS_URL"`) — environment variable checked first.
- `redis_url` (str, required for `WorkerControl.__init__`) — full Redis connection URL.
- `socket_timeout` (int, default `2`) — seconds before a Redis call times out; kept short
  so a hung Redis never stalls the agent tick loop or the stream supervisor's poll loop.
- `worker_id` (str) — the worker's ID, matching `WORKER_ID`/`message_bus.worker_id` elsewhere.
- `enabled` (bool) — desired state for `set_enabled`.
- `kill_file` (str, optional) — local kill file path. `None` resolves via
  `resolve_kill_file()`: env `WORKER_KILL_FILE` > config
  `worker_control.kill_file` (config/worker.yaml) > `/tmp/worker_disabled`.
- `reason` (str) — free text written into the kill file by `engage_local_override`.
- `ttl_s` (int) — liveness key TTL in seconds for `heartbeat`; clamped to at least 1.

## Return Value

- `resolve_redis_url` — the resolved Redis URL string (env > config > default).
- `resolve_kill_file` — the resolved kill file path.
- `is_enabled` — `False` if the kill file exists (Redis not consulted); otherwise `True` unless the stored value is exactly `"0"`; `True` on missing key or Redis error.
- `local_override_active` — whether the kill file exists (for status reporting).
- `engage_local_override` — `None`; creates the kill file.
- `release_local_override` — `True` if a kill file was removed, `False` if none existed.
- `heartbeat` — `True` if the liveness key was written, `False` on a Redis error (never raises).
- `last_seen` — ISO-8601 UTC timestamp of the last tick, or `None` (expired / never written / Redis down).
- `alive` — `last_seen(...) is not None`.
- `set_enabled` — echoes back the `enabled` value passed in on success; raises `redis.RedisError` on failure.

## Dependencies

- `redis` (Python client, `redis>=5.0` — already declared in root `requirements.txt`
  and `services/message-api/requirements.txt`)
- Consumed by `app/agent.py` (tick loop gate) and `app/stream_supervisor.py`
  (ffmpeg start/stop loop); constructed in `services/message-api/api.py` for the HTTP endpoints.

## Usage Examples

```python
from message_bus import load_worker_config
from worker_control import WorkerControl

config = load_worker_config("/config/worker.yaml")
control = WorkerControl.from_config(config)

if not control.is_enabled("coder"):
    ...  # skip this tick

control.set_enabled("coder", False)  # turn the worker off

if control.local_override_active():
    print("forced off locally:", control.kill_file)
```

```python
# health view: who is ticking?
for wid in ("coder", "tester", "manager"):
    print(wid, "alive" if control.alive(wid) else "down/unknown", control.last_seen(wid))
```

```bash
redis-cli GET worker:coder:alive   # "2026-09-27T12:00:05.123456+00:00"
redis-cli TTL worker:coder:alive   # seconds until it counts as dead
```

```bash
# same effect via the HTTP API (services/message-api)
curl -X POST http://localhost:8090/workers/coder/disable
curl http://localhost:8090/workers/coder
```

## Error Handling

- Redis unreachable during `is_enabled` — caught, logged (`[worker_control] WARN ...`), returns `True`.
- Redis unreachable during `set_enabled` — `redis.RedisError` propagates; `message-api` catches it and returns HTTP 503.
- Missing/unset key — treated as enabled (no seeding required before first use).
- Kill file present — logged once per transition as
  `[worker_control] WARN event=local_override_active worker_id=... kill_file=... enabled=False redis=skipped`,
  and `INFO event=local_override_cleared` when it goes away (not on every poll).
- `engage_local_override` — raises `OSError` if the file can't be written (caller decides the fallback).

## Changelog

- v1.2.0 (2026-09-27) — Liveness key `worker:{id}:alive` (ISO timestamp, TTL):
  `heartbeat(worker_id, ttl_s)` (never raises, logs outage/recovery once),
  `last_seen(worker_id)` / `alive(worker_id)` readers (None/False when
  expired or Redis down). Written every tick by `agent.py`. Kill-file and
  enable-flag semantics unchanged.
- v1.1.0 (2026-09-27) — Local kill switch: `WORKER_KILL_FILE` (default
  `/tmp/worker_disabled`, config `worker_control.kill_file`) forces the worker
  off without consulting Redis; added `resolve_kill_file`,
  `local_override_active`, `engage_local_override`, `release_local_override`
  and the `kill_file` constructor argument; `scripts/emergency_stop.sh` /
  `emergency_resume.sh` (+ `.ps1`). Redis-path semantics unchanged.
- v1.0.0 (2026-07-07) — Initial version.
