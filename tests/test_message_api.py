"""
Tests for services/message-api/api.py.
Requires services/message-api/requirements.txt installed (fastapi, uvicorn,
redis) in addition to the root requirements.txt (kafka-python).
KafkaProducer and redis.Redis are mocked at import time so these tests never
touch a real broker or Redis instance.
"""
import json
import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
import redis
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "message-api"))

os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("KAFKA_TOPIC", "test-topic")

os.environ.setdefault("POSTGRES_DB", "virtualtubers")
os.environ.setdefault("POSTGRES_USER", "virtualtubers")
os.environ.setdefault("POSTGRES_PASSWORD", "secret")

with patch("message_bus.KafkaProducer"), \
     patch("worker_control.redis.Redis.from_url"), \
     patch("log_filter_control.redis.Redis.from_url"), \
     patch("campaign_control.redis.Redis.from_url"):
    import api


@pytest.fixture
def client():
    api.producer.send = MagicMock()
    api.control._client = MagicMock()
    api.log_filter._client = MagicMock()
    api.campaign_control._client = MagicMock()
    return TestClient(api.app)


def test_post_message_valid_input(client):
    resp = client.post("/messages", json={"to": "coder", "payload": {"task": "hi"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["from"] == "operator"
    assert body["to"] == "coder"
    assert body["type"] == "operator_message"
    assert body["payload"] == {"task": "hi"}
    api.producer.send.assert_called_once()


def test_post_message_custom_type(client):
    resp = client.post("/messages", json={"to": "coder", "type": "task_assignment", "payload": {}})
    assert resp.status_code == 200
    assert resp.json()["type"] == "task_assignment"


def test_post_message_missing_required_field(client):
    resp = client.post("/messages", json={"payload": {}})
    assert resp.status_code == 422


def test_get_worker_status_defaults_enabled(client):
    api.control._client.get.return_value = None
    resp = client.get("/workers/coder")
    assert resp.status_code == 200
    assert resp.json() == {"worker_id": "coder", "enabled": True}


def test_disable_then_enable_worker_round_trip(client):
    resp = client.post("/workers/coder/disable")
    assert resp.status_code == 200
    assert resp.json() == {"worker_id": "coder", "enabled": False}
    api.control._client.set.assert_called_with("worker:coder:enabled", "0")

    resp = client.post("/workers/coder/enable")
    assert resp.status_code == 200
    assert resp.json() == {"worker_id": "coder", "enabled": True}
    api.control._client.set.assert_called_with("worker:coder:enabled", "1")


def test_disable_worker_returns_503_when_redis_unavailable(client):
    api.control._client.set.side_effect = redis.RedisError("connection refused")
    resp = client.post("/workers/coder/disable")
    assert resp.status_code == 503


def test_get_log_filter_defaults_excluded_for_status_update(client):
    api.log_filter._client.get.return_value = None
    resp = client.get("/log-filter/status_update")
    assert resp.status_code == 200
    assert resp.json() == {"type": "status_update", "excluded": True}


def test_get_log_filter_defaults_not_excluded_for_other_types(client):
    api.log_filter._client.get.return_value = None
    resp = client.get("/log-filter/task_complete")
    assert resp.status_code == 200
    assert resp.json() == {"type": "task_complete", "excluded": False}


def test_include_then_exclude_log_type_round_trip(client):
    resp = client.post("/log-filter/status_update/include")
    assert resp.status_code == 200
    assert resp.json() == {"type": "status_update", "excluded": False}
    api.log_filter._client.set.assert_called_with("logfilter:status_update:excluded", "0")

    resp = client.post("/log-filter/status_update/exclude")
    assert resp.status_code == 200
    assert resp.json() == {"type": "status_update", "excluded": True}
    api.log_filter._client.set.assert_called_with("logfilter:status_update:excluded", "1")


def test_exclude_log_type_returns_503_when_redis_unavailable(client):
    api.log_filter._client.set.side_effect = redis.RedisError("connection refused")
    resp = client.post("/log-filter/status_update/exclude")
    assert resp.status_code == 503


def test_prune_logs_requires_at_least_one_bound(client):
    resp = client.post("/logs/prune", json={})
    assert resp.status_code == 400


def test_prune_logs_deletes_range(client):
    with patch("api.prune_logs", return_value=5) as fake_prune:
        resp = client.post("/logs/prune", json={
            "after": "2026-07-01T00:00:00Z", "before": "2026-07-02T00:00:00Z",
        })

    assert resp.status_code == 200
    assert resp.json()["deleted"] == 5
    fake_prune.assert_called_once()
    _, kwargs = fake_prune.call_args
    assert kwargs["after"].isoformat() == "2026-07-01T00:00:00+00:00"
    assert kwargs["before"].isoformat() == "2026-07-02T00:00:00+00:00"


def test_prune_logs_returns_503_when_postgres_unavailable(client):
    with patch("api.prune_logs", side_effect=psycopg2.OperationalError("connection refused")):
        resp = client.post("/logs/prune", json={"after": "2026-07-01T00:00:00Z"})
    assert resp.status_code == 503


# ── /campaigns/* (CONTRACT.md §8, docs/campaign_control.md) ────────────────
# This service NEVER reads a personas.yaml file itself -- it only ever
# stores {campaign, speaker} pairs in Redis via campaign_control.py, which
# is why these tests only ever touch api.campaign_control._client /
# api.control._client (the airing-guard read), never any config/campaigns
# fixture.

def test_start_campaign_writes_cast_and_returns_campaign_and_cast(client):
    api.campaign_control._client.get.return_value = None  # no previously active campaign
    api.control._client.get.return_value = None  # nobody is airing

    resp = client.post("/campaigns/coder/start", json={"cast": {"coder": "worker-1"}})

    assert resp.status_code == 200
    assert resp.json() == {"campaign": "coder", "cast": {"coder": "worker-1"}}
    set_calls = {call.args[0]: call.args[1] for call in api.campaign_control._client.set.call_args_list}
    assert json.loads(set_calls["campaign:active"])["campaign"] == "coder"
    assert json.loads(set_calls["worker:worker-1:persona"]) == {"campaign": "coder", "speaker": "coder"}


def test_start_campaign_missing_cast_field_returns_422(client):
    resp = client.post("/campaigns/coder/start", json={})
    assert resp.status_code == 422


def test_start_campaign_returns_409_when_a_cast_worker_is_airing_and_not_forced(client):
    api.control._client.get.side_effect = lambda key: "1" if key == "worker:worker-1:airing" else None

    resp = client.post("/campaigns/coder/start", json={"cast": {"coder": "worker-1"}})

    assert resp.status_code == 409
    # Refused BEFORE ever writing anything -- campaign_control.start() must
    # not have been reached.
    api.campaign_control._client.set.assert_not_called()


def test_start_campaign_force_true_overrides_the_airing_guard(client):
    api.control._client.get.side_effect = lambda key: "1" if key == "worker:worker-1:airing" else None
    api.campaign_control._client.get.return_value = None

    resp = client.post("/campaigns/coder/start",
                       json={"cast": {"coder": "worker-1"}, "force": True})

    assert resp.status_code == 200
    assert resp.json() == {"campaign": "coder", "cast": {"coder": "worker-1"}}


def test_start_campaign_airing_check_fails_open_when_redis_unreachable(client):
    """_is_airing fails OPEN (absent/unreachable == not airing) -- a
    control-plane hiccup checking airing state must never block a
    legitimate campaign reassignment (same posture as worker_control's
    is_enabled)."""
    api.control._client.get.side_effect = redis.RedisError("connection refused")
    api.campaign_control._client.get.return_value = None

    resp = client.post("/campaigns/coder/start", json={"cast": {"coder": "worker-1"}})

    assert resp.status_code == 200


def test_start_campaign_returns_503_when_redis_unavailable_on_write(client):
    api.control._client.get.return_value = None
    api.campaign_control._client.get.return_value = None
    api.campaign_control._client.set.side_effect = redis.RedisError("connection refused")

    resp = client.post("/campaigns/coder/start", json={"cast": {"coder": "worker-1"}})

    assert resp.status_code == 503


def test_stop_campaign_returns_previous_campaign_and_clears_it(client):
    api.campaign_control._client.get.return_value = json.dumps(
        {"campaign": "coder", "cast": {"coder": "worker-1"}, "started_at": 1.0})

    resp = client.post("/campaigns/stop")

    assert resp.status_code == 200
    assert resp.json() == {"stopped": True, "campaign": "coder"}
    delete_calls = {call.args[0] for call in api.campaign_control._client.delete.call_args_list}
    assert "worker:worker-1:persona" in delete_calls
    assert "campaign:active" in delete_calls


def test_stop_campaign_returns_null_campaign_when_nothing_was_active(client):
    api.campaign_control._client.get.return_value = None

    resp = client.post("/campaigns/stop")

    assert resp.status_code == 200
    assert resp.json() == {"stopped": True, "campaign": None}


def test_stop_campaign_returns_503_when_redis_unavailable(client):
    api.campaign_control._client.get.side_effect = redis.RedisError("connection refused")

    resp = client.post("/campaigns/stop")

    assert resp.status_code == 503


def test_get_active_campaign_returns_null_when_nothing_active(client):
    api.campaign_control._client.get.return_value = None

    resp = client.get("/campaigns/active")

    assert resp.status_code == 200
    assert resp.json() == {"campaign": None}


def test_get_active_campaign_returns_campaign_and_cast(client):
    api.campaign_control._client.get.return_value = json.dumps(
        {"campaign": "dnd", "cast": {"gm": "worker-1"}, "started_at": 1.0})

    resp = client.get("/campaigns/active")

    assert resp.status_code == 200
    assert resp.json() == {"campaign": "dnd", "cast": {"gm": "worker-1"}}


def test_get_active_campaign_never_503s_on_redis_error(client):
    """GET /campaigns/active reads via CampaignControl.get_active, which
    fails OPEN -- unlike the write endpoints, this must never 503."""
    api.campaign_control._client.get.side_effect = redis.RedisError("connection refused")

    resp = client.get("/campaigns/active")

    assert resp.status_code == 200
    assert resp.json() == {"campaign": None}
