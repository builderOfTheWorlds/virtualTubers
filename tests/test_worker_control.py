"""
Tests for app/worker_control.py.
redis.Redis is mocked — these tests never touch a real Redis instance.
"""
from unittest.mock import MagicMock, patch

import pytest
import redis

from worker_control import WorkerControl, resolve_redis_url


def _control_with_fake_client():
    fake_client = MagicMock()
    with patch("worker_control.redis.Redis.from_url", return_value=fake_client):
        control = WorkerControl("redis://fake:6379")
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
