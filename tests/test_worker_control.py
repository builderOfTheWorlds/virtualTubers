"""
Tests for app/worker_control.py.
redis.Redis is mocked — these tests never touch a real Redis instance.
"""
from unittest.mock import MagicMock, patch

import pytest
import redis

from worker_control import DEFAULT_KILL_FILE, WorkerControl, resolve_kill_file, resolve_redis_url

# Never the real /tmp/worker_disabled — a kill file left on a dev box must
# not flip the Redis-path tests below.
_NO_KILL_FILE = "/nonexistent-dir/worker_disabled"


def _control_with_fake_client(kill_file=_NO_KILL_FILE):
    fake_client = MagicMock()
    with patch("worker_control.redis.Redis.from_url", return_value=fake_client):
        control = WorkerControl("redis://fake:6379", kill_file=str(kill_file))
    return control, fake_client


def test_resolve_redis_url_env_overrides_config():
    assert resolve_redis_url(config={"world_state": {"redis_url": "redis://config:6379"}}, env_name="__NOT_SET__") \
        == "redis://config:6379"


def test_resolve_redis_url_default_when_nothing_set():
    assert resolve_redis_url(config=None, env_name="__NOT_SET__") == "redis://redis:6379"


def test_is_enabled_defaults_true_when_key_missing():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    assert control.is_enabled("coder") is True


def test_is_enabled_false_when_explicitly_disabled():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = "0"
    assert control.is_enabled("coder") is False


def test_is_enabled_true_when_explicitly_enabled():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = "1"
    assert control.is_enabled("coder") is True


def test_is_enabled_fails_open_on_redis_error():
    control, fake_client = _control_with_fake_client()
    fake_client.get.side_effect = redis.RedisError("connection refused")
    assert control.is_enabled("coder") is True


def test_set_enabled_writes_expected_key_and_value():
    control, fake_client = _control_with_fake_client()
    control.set_enabled("coder", False)
    fake_client.set.assert_called_once_with("worker:coder:enabled", "0")


def test_set_enabled_raises_on_redis_error():
    control, fake_client = _control_with_fake_client()
    fake_client.set.side_effect = redis.RedisError("connection refused")
    with pytest.raises(redis.RedisError):
        control.set_enabled("coder", True)


# ── local kill file override ──────────────────────────────────────────────────
@pytest.fixture
def kill_file(tmp_path):
    return tmp_path / "worker_disabled"


def test_is_enabled_false_when_kill_file_present_and_redis_says_enabled(kill_file):
    control, fake_client = _control_with_fake_client(kill_file)
    fake_client.get.return_value = "1"
    kill_file.write_text("x")
    assert control.is_enabled("coder") is False
    fake_client.get.assert_not_called()


def test_is_enabled_false_when_kill_file_present_and_redis_down(kill_file):
    control, fake_client = _control_with_fake_client(kill_file)
    fake_client.get.side_effect = redis.RedisError("connection refused")
    kill_file.write_text("x")
    assert control.is_enabled("coder") is False
    assert control.local_override_active() is True


@pytest.mark.parametrize("redis_value,side_effect,expected", [
    ("1", None, True),
    ("0", None, False),
    (None, None, True),
    (None, redis.RedisError("down"), True),   # fail-open unchanged
])
def test_is_enabled_without_kill_file_keeps_redis_semantics(kill_file, redis_value, side_effect, expected):
    control, fake_client = _control_with_fake_client(kill_file)
    fake_client.get.return_value = redis_value
    fake_client.get.side_effect = side_effect
    assert control.local_override_active() is False
    assert control.is_enabled("coder") is expected


def test_is_enabled_resumes_redis_after_kill_file_removed(kill_file, capsys):
    control, fake_client = _control_with_fake_client(kill_file)
    fake_client.get.return_value = "1"
    control.engage_local_override(reason="test")
    assert control.is_enabled("coder") is False
    assert control.is_enabled("coder") is False
    out = capsys.readouterr().out
    assert out.count("event=local_override_active") == 1  # logged once, not per poll
    assert "WARN" in out
    assert control.release_local_override() is True
    assert control.is_enabled("coder") is True
    assert "event=local_override_cleared" in capsys.readouterr().out


def test_release_local_override_is_idempotent(kill_file):
    control, _ = _control_with_fake_client(kill_file)
    assert control.release_local_override() is False


def test_engage_local_override_writes_reason(kill_file):
    control, _ = _control_with_fake_client(kill_file)
    control.engage_local_override(reason="SIGUSR1")
    assert "reason=SIGUSR1" in kill_file.read_text()


def test_resolve_kill_file_precedence(monkeypatch):
    monkeypatch.delenv("WORKER_KILL_FILE", raising=False)
    assert resolve_kill_file(None) == DEFAULT_KILL_FILE
    cfg = {"worker_control": {"kill_file": "/cfg/killed"}}
    assert resolve_kill_file(cfg) == "/cfg/killed"
    monkeypatch.setenv("WORKER_KILL_FILE", "/env/killed")
    assert resolve_kill_file(cfg) == "/env/killed"


def test_from_config_uses_resolved_kill_file(monkeypatch, kill_file):
    monkeypatch.setenv("WORKER_KILL_FILE", str(kill_file))
    with patch("worker_control.redis.Redis.from_url", return_value=MagicMock()):
        control = WorkerControl.from_config({})
    assert control.kill_file == str(kill_file)


# ── liveness key (worker:{id}:alive) ─────────────────────────────────────────

class _FakeRedisWithTTL:
    """Tiny in-memory Redis stand-in honouring SET ... EX via an injectable
    clock, so key expiry can be tested without sleeping."""

    def __init__(self):
        self.now = 0.0
        self.store = {}
        self.down = False

    def _check(self):
        if self.down:
            raise redis.ConnectionError("connection refused")

    def set(self, key, value, ex=None):
        self._check()
        self.store[key] = (value, self.now + ex if ex else None)
        return True

    def get(self, key):
        self._check()
        value, expires = self.store.get(key, (None, None))
        if expires is not None and self.now >= expires:
            self.store.pop(key, None)
            return None
        return value


def _control_with_ttl_redis():
    fake = _FakeRedisWithTTL()
    with patch("worker_control.redis.Redis.from_url", return_value=fake):
        control = WorkerControl("redis://fake:6379", kill_file=_NO_KILL_FILE)
    return control, fake


def test_heartbeat_sets_alive_key_with_ttl():
    from datetime import datetime

    control, fake_client = _control_with_fake_client()
    assert control.heartbeat("coder", 15) is True
    args, kwargs = fake_client.set.call_args
    assert args[0] == "worker:coder:alive"
    assert kwargs == {"ex": 15}
    assert datetime.fromisoformat(args[1]).tzinfo is not None


def test_heartbeat_clamps_ttl_to_at_least_one_second():
    control, fake_client = _control_with_fake_client()
    control.heartbeat("coder", 0)
    assert fake_client.set.call_args.kwargs == {"ex": 1}


def test_last_seen_and_alive_round_trip_while_key_live():
    control, _ = _control_with_ttl_redis()
    control.heartbeat("coder", 15)
    assert control.last_seen("coder") is not None
    assert control.alive("coder") is True


def test_last_seen_none_and_not_alive_after_expiry():
    control, fake = _control_with_ttl_redis()
    control.heartbeat("coder", 15)
    fake.now += 16
    assert control.last_seen("coder") is None
    assert control.alive("coder") is False


def test_last_seen_none_when_never_written():
    control, _ = _control_with_ttl_redis()
    assert control.last_seen("tester") is None
    assert control.alive("tester") is False


def test_readers_return_none_false_when_redis_down():
    control, fake = _control_with_ttl_redis()
    control.heartbeat("coder", 15)
    fake.down = True
    assert control.last_seen("coder") is None
    assert control.alive("coder") is False


def test_heartbeat_fails_silently_and_logs_once_when_redis_down(capsys):
    control, fake = _control_with_ttl_redis()
    fake.down = True
    assert control.heartbeat("coder", 15) is False
    assert control.heartbeat("coder", 15) is False
    assert capsys.readouterr().out.count("liveness_write_failed") == 1

    fake.down = False
    assert control.heartbeat("coder", 15) is True
    assert "liveness_write_recovered" in capsys.readouterr().out
    assert control.alive("coder") is True


def test_heartbeat_does_not_touch_enabled_flag():
    control, fake = _control_with_ttl_redis()
    control.heartbeat("coder", 15)
    assert "worker:coder:enabled" not in fake.store
    assert control.is_enabled("coder") is True
