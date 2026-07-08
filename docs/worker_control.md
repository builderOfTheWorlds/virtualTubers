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

## Signature

```python
def resolve_redis_url(config=None, env_name="REDIS_URL", default="redis://redis:6379") -> str

class WorkerControl:
    def __init__(self, redis_url: str, socket_timeout: int = 2)

    @classmethod
    def from_config(cls, config=None) -> "WorkerControl"

    def is_enabled(self, worker_id: str) -> bool
    def set_enabled(self, worker_id: str, enabled: bool) -> bool
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

## Return Value

- `resolve_redis_url` — the resolved Redis URL string (env > config > default).
- `is_enabled` — `True` unless the stored value is exactly `"0"`; `True` on missing key or Redis error.
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

## Changelog

- v1.0.0 (2026-07-07) — Initial version.
