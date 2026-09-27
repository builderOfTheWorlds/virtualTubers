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
