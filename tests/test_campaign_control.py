"""
Tests for app/campaign_control.py.
redis.Redis is mocked — these tests never touch a real Redis instance.
Mirrors tests/test_worker_control.py's own conventions exactly (same
_control_with_fake_client-style helper, same MagicMock-based fake client).
"""
import json
from unittest.mock import MagicMock, patch

import pytest
import redis

from campaign_control import CampaignControl, CAMPAIGN_KEY, persona_key


def _control_with_fake_client():
    fake_client = MagicMock()
    with patch("campaign_control.redis.Redis.from_url", return_value=fake_client):
        control = CampaignControl("redis://fake:6379")
    return control, fake_client


# ── persona_key ──────────────────────────────────────────────────────────────

def test_persona_key_format():
    assert persona_key("worker-1") == "worker:worker-1:persona"


def test_campaign_key_constant():
    assert CAMPAIGN_KEY == "campaign:active"


# ── get_active: fails open ───────────────────────────────────────────────────

def test_get_active_returns_none_when_key_missing():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    assert control.get_active() is None


def test_get_active_returns_parsed_json():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = json.dumps(
        {"campaign": "coder", "cast": {"coder": "worker-1"}, "started_at": 123.0})
    active = control.get_active()
    assert active == {"campaign": "coder", "cast": {"coder": "worker-1"}, "started_at": 123.0}


def test_get_active_fails_open_on_redis_error():
    control, fake_client = _control_with_fake_client()
    fake_client.get.side_effect = redis.RedisError("connection refused")
    assert control.get_active() is None


def test_get_active_fails_open_on_malformed_json():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = "{not valid json"
    assert control.get_active() is None


# ── get_persona: fails open ──────────────────────────────────────────────────

def test_get_persona_returns_none_when_key_missing():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    assert control.get_persona("worker-1") is None


def test_get_persona_returns_parsed_json():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = json.dumps({"campaign": "coder", "speaker": "coder"})
    assert control.get_persona("worker-1") == {"campaign": "coder", "speaker": "coder"}


def test_get_persona_uses_worker_scoped_key():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    control.get_persona("worker-7")
    fake_client.get.assert_called_once_with("worker:worker-7:persona")


def test_get_persona_fails_open_on_redis_error():
    control, fake_client = _control_with_fake_client()
    fake_client.get.side_effect = redis.RedisError("connection refused")
    assert control.get_persona("worker-1") is None


def test_get_persona_fails_open_on_malformed_json():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = "{not valid json"
    assert control.get_persona("worker-1") is None


# ── start: propagates on write, writes both key kinds ───────────────────────

def test_start_writes_campaign_active_and_every_persona_key():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None  # no previously active campaign

    cast = {"gm": "worker-1", "thorin": "worker-2"}
    active = control.start("dnd", cast)

    assert active["campaign"] == "dnd"
    assert active["cast"] == cast
    assert "started_at" in active

    set_calls = {call.args[0]: call.args[1] for call in fake_client.set.call_args_list}
    assert json.loads(set_calls["campaign:active"]) == active
    assert json.loads(set_calls["worker:worker-1:persona"]) == {"campaign": "dnd", "speaker": "gm"}
    assert json.loads(set_calls["worker:worker-2:persona"]) == {"campaign": "dnd", "speaker": "thorin"}


def test_start_returns_dict_with_expected_shape():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    active = control.start("coder", {"coder": "worker-1"})
    assert set(active) == {"campaign", "cast", "started_at"}


def test_start_raises_on_redis_error_and_does_not_fail_open():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    fake_client.set.side_effect = redis.RedisError("connection refused")
    with pytest.raises(redis.RedisError):
        control.start("coder", {"coder": "worker-1"})


def test_start_clears_persona_for_worker_dropped_from_a_previous_cast():
    """A worker cast in the PREVIOUSLY active campaign but not named in the
    new cast's worker ids must go back to blank -- otherwise it would keep
    performing its old persona forever, since nothing else ever tells it to
    stop. This is the one behavior CONTRACT.md §8 doesn't spell out
    literally as "writes both key kinds"; see campaign_control.py's own
    docstring for the reasoning."""
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = json.dumps({
        "campaign": "coder", "cast": {"coder": "worker-1", "manager": "worker-5"},
        "started_at": 1.0,
    })

    control.start("dnd", {"gm": "worker-1"})

    delete_calls = [call.args[0] for call in fake_client.delete.call_args_list]
    assert "worker:worker-5:persona" in delete_calls
    assert "worker:worker-1:persona" not in delete_calls  # still cast, just to a new speaker


def test_start_does_not_clear_persona_for_worker_still_in_new_cast():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = json.dumps({
        "campaign": "coder", "cast": {"coder": "worker-1"}, "started_at": 1.0,
    })

    control.start("coder", {"manager": "worker-1"})  # same worker, different speaker

    assert fake_client.delete.call_count == 0


def test_start_force_flag_does_not_change_the_write_itself():
    """force is accepted to match the frozen CONTRACT.md §8 signature and
    the POST /campaigns/{campaign}/start request body, but the mid-airing
    guard it exists for is enforced one layer up, in
    services/message-api/api.py (reads worker:{id}:airing) -- start() writes
    unconditionally either way."""
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None
    active_no_force = control.start("coder", {"coder": "worker-1"}, force=False)
    active_force = control.start("coder", {"coder": "worker-1"}, force=True)
    assert active_no_force["campaign"] == active_force["campaign"] == "coder"


# ── stop: clears campaign:active + every assigned persona, propagates ──────

def test_stop_clears_campaign_active_and_every_persona_key():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = json.dumps({
        "campaign": "dnd", "cast": {"gm": "worker-1", "thorin": "worker-2"}, "started_at": 1.0,
    })

    control.stop()

    delete_calls = {call.args[0] for call in fake_client.delete.call_args_list}
    assert delete_calls == {"worker:worker-1:persona", "worker:worker-2:persona", "campaign:active"}


def test_stop_is_a_noop_beyond_clearing_the_key_when_nothing_was_active():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = None

    control.stop()

    delete_calls = {call.args[0] for call in fake_client.delete.call_args_list}
    assert delete_calls == {"campaign:active"}


def test_stop_raises_on_redis_error_reading_active_campaign():
    """stop() reads campaign:active directly (not via get_active(), which
    fails open) -- a Redis outage must propagate here too, or an operator
    could believe a campaign stopped when its persona keys are still live."""
    control, fake_client = _control_with_fake_client()
    fake_client.get.side_effect = redis.RedisError("connection refused")
    with pytest.raises(redis.RedisError):
        control.stop()


def test_stop_raises_on_redis_error_deleting_keys():
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = json.dumps({
        "campaign": "coder", "cast": {"coder": "worker-1"}, "started_at": 1.0,
    })
    fake_client.delete.side_effect = redis.RedisError("connection refused")
    with pytest.raises(redis.RedisError):
        control.stop()


def test_stop_tolerates_malformed_active_campaign_value():
    """A corrupt campaign:active value must not crash stop() -- it degrades
    to 'nothing to clear beyond the key itself', same posture as
    get_active()'s own malformed-JSON handling."""
    control, fake_client = _control_with_fake_client()
    fake_client.get.return_value = "{not valid json"

    control.stop()  # must not raise

    delete_calls = {call.args[0] for call in fake_client.delete.call_args_list}
    assert delete_calls == {"campaign:active"}


# ── from_config ───────────────────────────────────────────────────────────

def test_from_config_uses_resolve_redis_url(monkeypatch):
    seen = {}

    def fake_from_url(url, **kwargs):
        seen["url"] = url
        return MagicMock()

    monkeypatch.setattr("campaign_control.redis.Redis.from_url", fake_from_url)
    CampaignControl.from_config({"world_state": {"redis_url": "redis://config:6379"}})
    assert seen["url"] == "redis://config:6379"
