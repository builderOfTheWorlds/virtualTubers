#!/usr/bin/env python3
"""
worker_control.py
Redis-backed enable/disable flag per worker (one key: worker:{id}:enabled).
Checked by agent.py's tick loop and stream_supervisor.py's ffmpeg loop; set
via message-api's /workers/{worker_id}/enable|disable endpoints. This is how
a worker is turned on/off without redeploying the stack.

Reads fail open (missing key or unreachable Redis => enabled) so a
control-plane outage never silently kills a live stream. Writes do NOT fail
open — set_enabled raises on redis.RedisError so the caller (the API) can
tell the operator the toggle didn't take effect.
"""
import os

import redis

KEY_PREFIX = "worker"
KEY_SUFFIX = "enabled"


def resolve_redis_url(config=None, env_name="REDIS_URL", default="redis://redis:6379"):
    config_value = (config or {}).get("world_state", {}).get("redis_url")
    return os.environ.get(env_name) or config_value or default


class WorkerControl:
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

    def _key(self, worker_id):
        return f"{KEY_PREFIX}:{worker_id}:{KEY_SUFFIX}"

    def is_enabled(self, worker_id):
        try:
            value = self._client.get(self._key(worker_id))
        except redis.RedisError as exc:
            print(f"[worker_control] WARN redis unreachable, failing open (enabled) for {worker_id}: {exc}")
            return True
        return value != "0"

    def set_enabled(self, worker_id, enabled):
        self._client.set(self._key(worker_id), "1" if enabled else "0")
        return enabled
