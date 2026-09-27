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

Local kill switch: fail-open means Redis being down also takes away the
operator's ability to turn a stream OFF. So a per-container kill file
(WORKER_KILL_FILE, default /tmp/worker_disabled) overrides Redis in both
directions of failure: while it exists, is_enabled() returns False without
consulting Redis at all. It is created/removed with nothing but Docker
(scripts/emergency_stop.sh / emergency_resume.sh, `docker exec`), or by
sending the stream supervisor SIGUSR1. See docs/worker_control.md.
"""
import os
import time

import redis

KEY_PREFIX = "worker"
KEY_SUFFIX = "enabled"

KILL_FILE_ENV = "WORKER_KILL_FILE"
#: /tmp in the worker container: startup.sh's /tmp cleanup only removes the
#: X11 lock/socket and pulse-* dirs, so this survives agent.py/supervisor
#: restarts and a plain `docker restart`; only re-creating the container
#: (compose up with a changed spec, ./redeploy.sh) wipes it.
DEFAULT_KILL_FILE = "/tmp/worker_disabled"


def log(level, event, **fields):
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[worker_control] {level} event={event} {kv}".rstrip(), flush=True)


def resolve_redis_url(config=None, env_name="REDIS_URL", default="redis://redis:6379"):
    config_value = (config or {}).get("world_state", {}).get("redis_url")
    return os.environ.get(env_name) or config_value or default


def resolve_kill_file(config=None, env_name=KILL_FILE_ENV, default=DEFAULT_KILL_FILE):
    """env WORKER_KILL_FILE > config worker_control.kill_file > default."""
    config_value = ((config or {}).get("worker_control") or {}).get("kill_file")
    return os.environ.get(env_name) or config_value or default


class WorkerControl:
    def __init__(self, redis_url, socket_timeout=2, kill_file=None):
        self._client = redis.Redis.from_url(
            redis_url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=True,
        )
        # None => resolve from env/default, so every WorkerControl in a
        # worker container honours the same file with no caller changes.
        self.kill_file = kill_file or resolve_kill_file()
        # Last override state we logged, so a loop polling every few seconds
        # logs the transition once rather than on every call.
        self._override_logged = False

    @classmethod
    def from_config(cls, config=None):
        return cls(resolve_redis_url(config), kill_file=resolve_kill_file(config))

    def _key(self, worker_id):
        return f"{KEY_PREFIX}:{worker_id}:{KEY_SUFFIX}"

    def local_override_active(self):
        """True while the local kill file exists (worker forced off)."""
        return os.path.exists(self.kill_file)

    def engage_local_override(self, reason="manual"):
        """Create the kill file. Raises OSError if it can't be written."""
        with open(self.kill_file, "w") as f:
            f.write(f"disabled_at={time.strftime('%Y-%m-%dT%H:%M:%S%z')} reason={reason}\n")
        log("WARN", "local_override_engaged", kill_file=self.kill_file, reason=reason)

    def release_local_override(self):
        """Remove the kill file; returns True if one was removed."""
        try:
            os.remove(self.kill_file)
        except FileNotFoundError:
            return False
        log("INFO", "local_override_released", kill_file=self.kill_file)
        return True

    def is_enabled(self, worker_id):
        if self.local_override_active():
            if not self._override_logged:
                log("WARN", "local_override_active", worker_id=worker_id,
                    kill_file=self.kill_file, enabled=False, redis="skipped")
                self._override_logged = True
            return False
        if self._override_logged:
            log("INFO", "local_override_cleared", worker_id=worker_id,
                kill_file=self.kill_file, redis="consulted")
            self._override_logged = False
        try:
            value = self._client.get(self._key(worker_id))
        except redis.RedisError as exc:
            print(f"[worker_control] WARN redis unreachable, failing open (enabled) for {worker_id}: {exc}")
            return True
        return value != "0"

    def set_enabled(self, worker_id, enabled):
        self._client.set(self._key(worker_id), "1" if enabled else "0")
        return enabled
