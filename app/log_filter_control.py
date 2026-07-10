#!/usr/bin/env python3
"""
log_filter_control.py
Redis-backed exclude flag per message type (key: logfilter:{type}:excluded).
Checked by message-logger before persisting each message to Postgres; set
via message-api's /log-filter/{type}/exclude|include endpoints. Lets an
operator stop (or resume) durably logging a noisy message type — e.g. the
per-tick heartbeat status_update flood — without a stack redeploy.

Unlike worker_control's fail-open-to-True, a missing key or an unreachable
Redis here falls back to DEFAULT_EXCLUDED_TYPES rather than "log everything":
that keeps the heartbeat flood silenced through a control-plane outage
instead of quietly reappearing. Writes do NOT fail open — set_excluded
raises on redis.RedisError so the caller (the API) can tell the operator the
toggle didn't take effect.
"""
import os

import redis

KEY_PREFIX = "logfilter"
KEY_SUFFIX = "excluded"

DEFAULT_EXCLUDED_TYPES = {"status_update"}


def resolve_redis_url(config=None, env_name="REDIS_URL", default="redis://redis:6379"):
    config_value = (config or {}).get("world_state", {}).get("redis_url")
    return os.environ.get(env_name) or config_value or default


class LogFilterControl:
    def __init__(self, redis_url, socket_timeout=2):
        self._client = redis.Redis.from_url(
            redis_url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=True,
        )

    @classmethod
    def from_config(cls, config=None):
        return cls(resolve_redis_url(config))

    def _key(self, message_type):
        return f"{KEY_PREFIX}:{message_type}:{KEY_SUFFIX}"

    def is_excluded(self, message_type):
        default = message_type in DEFAULT_EXCLUDED_TYPES
        try:
            value = self._client.get(self._key(message_type))
        except redis.RedisError as exc:
            print(f"[log_filter_control] WARN redis unreachable, failing open to default "
                  f"({default}) for {message_type}: {exc}")
            return default
        if value is None:
            return default
        return value == "1"

    def set_excluded(self, message_type, excluded):
        self._client.set(self._key(message_type), "1" if excluded else "0")
        return excluded
