"""Arbiter state stores (build plan P3.4). JSON dict values keyed by scene id.

InMemoryStateStore is for tests and single-process use; RedisStateStore keeps
the arbiter state in the world-state Redis so a scene survives a worker
restart (see Arbiter.resume in app/turns.py).
"""
import copy
import json
import logging

log = logging.getLogger(__name__)


class InMemoryStateStore:
    def __init__(self):
        self._data = {}

    def load(self, key):
        raw = self._data.get(key)
        return None if raw is None else json.loads(raw)

    def save(self, key, value):
        self._data[key] = json.dumps(value)


class RedisStateStore:
    """JSON values under `prefix + key`. `client` may be injected (tests);
    otherwise a redis.Redis is built from `redis_url`."""

    def __init__(self, redis_url=None, prefix="table:", client=None, socket_timeout=2):
        if client is None:
            import redis
            client = redis.Redis.from_url(redis_url, socket_timeout=socket_timeout)
        self._client = client
        self.prefix = prefix

    def _key(self, key):
        return f"{self.prefix}{key}"

    def load(self, key):
        raw = self._client.get(self._key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def save(self, key, value):
        self._client.set(self._key(key), json.dumps(copy.deepcopy(value)))
        log.debug("state_store save key=%s", self._key(key))
