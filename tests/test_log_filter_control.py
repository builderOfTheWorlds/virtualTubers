"""
Tests for app/log_filter_control.py.
redis.Redis is mocked — these tests never touch a real Redis instance.
"""
from unittest.mock import MagicMock, patch

import pytest
import redis

from log_filter_control import LogFilterControl, resolve_redis_url


def _control_with_fake_client():
    fake_client = MagicMock()
    with patch("log_filter_control.redis.Redis.from_url", return_value=fake_client):
        control = LogFilterControl("redis://fake:6379")
    return control, fake_client


def test_resolve_redis_url_env_overrides_config():
    assert resolve_redis_url(config={"world_state": {"redis_url": "redis://config:6379"}}, env_name="__NOT_SET__") \
        == "redis://config:6379"


def test_resolve_redis_url_default_when_nothing_set():
    assert resolve_redis_url(config=None, env_name="__NOT_SET__") == "redis://redis:6379"


def test_is_excluded_defaults_true_for_status_update_when_key_missing():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    assert control.is_excluded("status_update") is True


def test_is_excluded_defaults_false_for_other_types_when_key_missing():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    assert control.is_excluded("task_complete") is False


def test_is_excluded_true_when_explicitly_excluded():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = "1"
    assert control.is_excluded("task_complete") is True


def test_is_excluded_false_when_explicitly_included():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = "0"
    assert control.is_excluded("status_update") is False


def test_is_excluded_fails_open_to_default_on_redis_error():
    control, fake_client = _control_with_fake_client()
    fake_client.get.side_effect = redis.RedisError("connection refused")
    assert control.is_excluded("status_update") is True
    assert control.is_excluded("task_complete") is False


def test_set_excluded_writes_expected_key_and_value():
    control, fake_client = _control_with_fake_client()
    control.set_excluded("status_update", False)
    fake_client.set.assert_called_once_with("logfilter:status_update:excluded", "0")


def test_set_excluded_raises_on_redis_error():
    control, fake_client = _control_with_fake_client()
    fake_client.set.side_effect = redis.RedisError("connection refused")
    with pytest.raises(redis.RedisError):
        control.set_excluded("status_update", True)
