# app/log_filter_control.py

## Overview

Redis-backed exclude flag per message type — the mechanism behind stopping
(or resuming) durable Postgres logging of a noisy message type without
redeploying the stack. One key per type (`logfilter:{type}:excluded`), read
by `services/message-logger/logger.py` before every INSERT, written by
`services/message-api`'s `/log-filter/{type}/exclude`/`include` endpoints
(docs/message_api.md).

Built to stop the per-tick heartbeat flood: `app/agent.py` publishes a
`status_update` message every tick (5-8s per worker, see `worker.yaml` /
`config/workers/*.yaml`'s `tick_rate_ms`), and until this filter existed
every one of those was durably inserted into the `messages` table with no
retention policy — pure clutter, since nothing reads heartbeats back out of
Postgres today.

Reads and writes are asymmetric on purpose, mirroring `worker_control.py`,
but with the opposite fail-open default: `is_excluded` falls back to
`DEFAULT_EXCLUDED_TYPES` (currently just `status_update`) when the key is
missing or Redis is unreachable, rather than "log everything." That keeps
the heartbeat flood silenced through a control-plane outage instead of
quietly reappearing. `set_excluded` does **not** fail open — it raises so
the caller (the API) can tell the operator the toggle didn't take effect.

## Signature

```python
def resolve_redis_url(config=None, env_name="REDIS_URL", default="redis://redis:6379") -> str

class LogFilterControl:
    def __init__(self, redis_url: str, socket_timeout: int = 2)

    @classmethod
    def from_config(cls, config=None) -> "LogFilterControl"

    def is_excluded(self, message_type: str) -> bool
    def set_excluded(self, message_type: str, excluded: bool) -> bool
```

## Parameters

- `config` (dict, optional) — a loaded worker config (`message_bus.load_worker_config`);
  `resolve_redis_url` reads `config["world_state"]["redis_url"]` as a fallback.
- `env_name` (str, default `"REDIS_URL"`) — environment variable checked first.
- `redis_url` (str, required for `LogFilterControl.__init__`) — full Redis connection URL.
- `socket_timeout` (int, default `2`) — seconds before a Redis call times out; kept short
  so a hung Redis never stalls the message-logger consume loop.
- `message_type` (str) — the message `type` field (e.g. `status_update`, `task_complete`).
- `excluded` (bool) — desired state for `set_excluded`.

## Return Value

- `resolve_redis_url` — the resolved Redis URL string (env > config > default).
- `is_excluded` — `True` if the stored value is exactly `"1"`; falls back to
  `message_type in DEFAULT_EXCLUDED_TYPES` on a missing key or Redis error.
- `set_excluded` — echoes back the `excluded` value passed in on success; raises `redis.RedisError` on failure.

## Dependencies

- `redis` (Python client, `redis>=5.0` — declared in `services/message-logger/requirements.txt`
  and `services/message-api/requirements.txt`)
- Consumed by `services/message-logger/logger.py` (pre-INSERT gate); constructed in
  `services/message-api/api.py` for the HTTP endpoints.

## Usage Examples

```python
from log_filter_control import LogFilterControl

log_filter = LogFilterControl.from_config()

if log_filter.is_excluded(msg["type"]):
    continue  # skip the INSERT for this message

log_filter.set_excluded("status_update", False)  # resume logging heartbeats
```

```bash
# same effect via the HTTP API (services/message-api)
curl -X POST http://localhost:8090/log-filter/status_update/include
curl http://localhost:8090/log-filter/status_update
curl -X POST http://localhost:8090/log-filter/status_update/exclude
```

## Error Handling

- Redis unreachable during `is_excluded` — caught, logged (`[log_filter_control] WARN ...`),
  falls back to `DEFAULT_EXCLUDED_TYPES` membership.
- Redis unreachable during `set_excluded` — `redis.RedisError` propagates; `message-api`
  catches it and returns HTTP 503.
- Missing/unset key — falls back to `DEFAULT_EXCLUDED_TYPES` membership (no seeding
  required before first use; `status_update` is filtered out of the box).

## Changelog

- v1.0.0 (2026-07-09) — Initial version. Filters `status_update` (heartbeat) messages
  out of `messages` table inserts by default; configurable per message type via
  `services/message-api`'s `/log-filter` endpoints without a stack redeploy.
