"""
Tests for app/worker_control.py.
redis.Redis is mocked — these tests never touch a real Redis instance.
"""
import json
from datetime import datetime, timedelta, timezone
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

    def mget(self, keys):
        self._check()
        return [self.get(k) for k in keys]

    def scan_iter(self, match=None, count=None):
        import fnmatch
        self._check()
        return iter([k for k in list(self.store) if self.get(k) is not None
                     and (match is None or fnmatch.fnmatchcase(k, match))])


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
    value = json.loads(args[1])
    assert datetime.fromisoformat(value["ts"]).tzinfo is not None
    assert value["local_override"] is False
    assert value["ttl_s"] == 15


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


# ── alive value format + health view (v1.3.0) ────────────────────────────────

def test_heartbeat_reports_local_override_in_alive_value(kill_file):
    kill_file.write_text("x")
    fake = _FakeRedisWithTTL()
    with patch("worker_control.redis.Redis.from_url", return_value=fake):
        control = WorkerControl("redis://fake:6379", kill_file=str(kill_file))
    control.heartbeat("coder", 15)
    assert json.loads(fake.store["worker:coder:alive"][0])["local_override"] is True
    assert control.health("coder")["local_override"] is True


def test_last_seen_accepts_old_plain_iso_value():
    control, fake = _control_with_ttl_redis()
    fake.set("worker:coder:alive", "2026-09-27T12:00:00+00:00", ex=15)
    assert control.last_seen("coder") == "2026-09-27T12:00:00+00:00"
    assert control.alive("coder") is True


def test_last_seen_returns_ts_from_new_json_value():
    control, _ = _control_with_ttl_redis()
    control.heartbeat("coder", 15)
    ts = control.last_seen("coder")
    assert not ts.startswith("{")
    assert datetime.fromisoformat(ts).tzinfo is not None


@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("", None),
    ("2026-09-27T12:00:00+00:00", {"ts": "2026-09-27T12:00:00+00:00", "local_override": None, "ttl_s": None}),
    ('{"ts": "T", "local_override": true, "ttl_s": 15}', {"ts": "T", "local_override": True, "ttl_s": 15}),
    ('{"ts": "T", "local_override": "yes", "ttl_s": -1}', {"ts": "T", "local_override": None, "ttl_s": None}),
    ('{not json', {"ts": "{not json", "local_override": None, "ttl_s": None}),
])
def test_parse_alive_value_handles_old_new_and_bad_values(value, expected):
    from worker_control import parse_alive_value
    assert parse_alive_value(value) == expected


def test_health_alive_then_stale_then_down_as_key_ages():
    control, fake = _control_with_ttl_redis()
    t0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    fake.set("worker:coder:alive", json.dumps({"ts": t0.isoformat(), "local_override": False, "ttl_s": 20}), ex=20)

    row = control.health("coder", now=t0 + timedelta(seconds=3))
    assert (row["state"], row["alive"], row["age_s"], row["local_override"], row["enabled"]) \
        == ("alive", True, 3.0, False, True)

    row = control.health("coder", now=t0 + timedelta(seconds=15))
    assert (row["state"], row["alive"]) == ("stale", True)

    fake.now += 21
    row = control.health("coder")
    assert (row["state"], row["alive"], row["last_seen"], row["local_override"]) == ("down", False, None, None)


def test_health_old_value_is_alive_with_unknown_override():
    control, fake = _control_with_ttl_redis()
    t0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    fake.set("worker:coder:alive", t0.isoformat(), ex=15)
    row = control.health("coder", now=t0 + timedelta(seconds=100))
    # no reported TTL => never "stale"; key presence alone means alive
    assert (row["state"], row["local_override"], row["age_s"]) == ("alive", None, 100.0)


def test_health_enabled_is_raw_redis_flag_ignoring_local_kill_file(kill_file):
    kill_file.write_text("x")
    fake = _FakeRedisWithTTL()
    with patch("worker_control.redis.Redis.from_url", return_value=fake):
        control = WorkerControl("redis://fake:6379", kill_file=str(kill_file))
    assert control.health("coder")["enabled"] is True  # missing key => enabled
    control.set_enabled("coder", False)
    assert control.health("coder")["enabled"] is False


def test_health_many_unknown_when_redis_down_single_round_trip():
    control, fake_client = _control_with_fake_client()
    fake_client.mget.side_effect = redis.ConnectionError("refused")
    rows = control.health_many(["coder", "tester"])
    assert [r["state"] for r in rows] == ["unknown", "unknown"]
    assert all(r["alive"] is None and r["enabled"] is None for r in rows)
    fake_client.mget.assert_called_once()
    assert control.health_many([]) == []


def test_known_worker_ids_from_alive_and_enabled_keys():
    control, fake = _control_with_ttl_redis()
    control.heartbeat("roundtable", 15)
    control.set_enabled("tuber_0", False)
    assert control.known_worker_ids() == ["roundtable", "tuber_0"]
    fake.down = True
    assert control.known_worker_ids() == []
