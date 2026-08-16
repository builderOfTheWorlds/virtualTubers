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
sys.path.insert(0, str(ROOT / "app"))

os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("KAFKA_TOPIC", "test-topic")

os.environ.setdefault("POSTGRES_DB", "virtualtubers")
os.environ.setdefault("POSTGRES_USER", "virtualtubers")
os.environ.setdefault("POSTGRES_PASSWORD", "secret")

with patch("message_bus.KafkaProducer"), \
     patch("worker_control.redis.Redis.from_url"), \
     patch("log_filter_control.redis.Redis.from_url"):
    import api


@pytest.fixture
def client():
    api.producer.send = MagicMock()
    api.control._client = MagicMock()
    api.log_filter._client = MagicMock()
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


# ── /replays (Rerun Theater episode library) ────────────────────────────────
# episode_store and validate_episode/resolve_name are monkeypatched per test
# (never a real Postgres). _schema_ready is forced True so _ensure_schema()
# doesn't attempt a real DB connection on every request that reaches it.
import episode_validator  # noqa: E402

VALID_SCRIPT = {
    "source": "demo-ep",
    "project": "virtualTubers",
    "session_id": "sess-1",
    "date": "2026-08-01",
    "events": [{"type": "assistant_text", "text": "hi"}] * 5,
}


@pytest.fixture
def no_store(client, monkeypatch):
    """episode_store reachable == False for every /replays endpoint."""
    monkeypatch.setattr(api.episode_store, "available", lambda: False)
    return client


@pytest.mark.parametrize("method,path,kwargs", [
    ("post", "/replays", {"content": b"{}"}),
    ("get", "/replays", {}),
    ("get", "/replays/ep1", {}),
    ("delete", "/replays/ep1", {}),
])
def test_replays_endpoints_503_when_store_unavailable(no_store, method, path, kwargs):
    resp = getattr(no_store, method)(path, **kwargs)
    assert resp.status_code == 503


def test_upload_replay_success_stores_and_returns_info(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})
    save_calls = []
    monkeypatch.setattr(
        api.episode_store, "save_episode",
        lambda name, script, overwrite=False: save_calls.append((name, script, overwrite)) or True)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"name": "demo-ep", "event_count": 5, "byte_size": 123, "created": True}
    assert len(save_calls) == 1
    assert save_calls[0][0] == "demo-ep"
    assert save_calls[0][2] is False


def test_upload_replay_success_with_explicit_json_content_type(client, monkeypatch):
    """Regression test: the README and scripts/build_replay_library.py both
    document `curl -H 'Content-Type: application/json' --data-binary @file`.
    A `bytes = Body(..., media_type=...)` param can get JSON-decoded before
    validation on some fastapi/starlette versions, turning that exact,
    documented call into a 422. Every other test in this file posts with no
    Content-Type header and would not have caught that."""
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})
    monkeypatch.setattr(
        api.episode_store, "save_episode", lambda name, script, overwrite=False: True)

    resp = client.post(
        "/replays",
        content=json.dumps(VALID_SCRIPT).encode(),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    assert resp.json()["name"] == "demo-ep"


def test_upload_replay_overwrite_query_param_passed_through(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})
    save_calls = []
    monkeypatch.setattr(
        api.episode_store, "save_episode",
        lambda name, script, overwrite=False: save_calls.append(overwrite) or True)

    resp = client.post(
        "/replays?overwrite=true", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 200
    assert save_calls == [True]


def test_upload_replay_413_when_over_max_upload_bytes(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 10)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 413


def test_upload_replay_400_when_body_not_valid_json(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    resp = client.post("/replays", content=b"not json{")

    assert resp.status_code == 400
    assert "not valid json" in resp.json()["detail"]


def test_upload_replay_400_when_episode_invalid(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    def raise_invalid(script, name=None):
        raise episode_validator.EpisodeInvalid("missing required key(s): source")
    monkeypatch.setattr(api, "validate_episode", raise_invalid)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "missing required key(s): source"


def test_upload_replay_409_when_episode_already_exists(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})
    monkeypatch.setattr(api.episode_store, "save_episode", lambda *a, **kw: False)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 409
    assert "demo-ep" in resp.json()["detail"]


def test_upload_replay_503_when_save_raises_operational_error(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})

    def raise_op_error(*a, **kw):
        raise psycopg2.OperationalError("connection refused")
    monkeypatch.setattr(api.episode_store, "save_episode", raise_op_error)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 503


def test_list_replays_returns_detailed_episode_metadata(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    episodes = [{"name": "ep1", "project": "virtualTubers", "session_id": "s1",
                 "date": "2026-08-01", "event_count": 5, "byte_size": 123,
                 "uploaded_by": "operator", "uploaded_at": "2026-08-01T00:00:00+00:00"}]
    monkeypatch.setattr(api.episode_store, "list_episodes_detailed", lambda: episodes)

    resp = client.get("/replays")

    assert resp.status_code == 200
    assert resp.json() == {"episodes": episodes}


def test_list_replays_503_when_listing_raises_operational_error(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)

    def raise_op_error():
        raise psycopg2.OperationalError("connection refused")
    monkeypatch.setattr(api.episode_store, "list_episodes_detailed", raise_op_error)

    resp = client.get("/replays")

    assert resp.status_code == 503


def test_get_replay_returns_the_stored_script(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api.episode_store, "load_episode", lambda name: VALID_SCRIPT)

    resp = client.get("/replays/demo-ep")

    assert resp.status_code == 200
    assert resp.json() == VALID_SCRIPT


def test_get_replay_404_when_episode_missing(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api.episode_store, "load_episode", lambda name: None)

    resp = client.get("/replays/nope")

    assert resp.status_code == 404


def test_get_replay_400_when_name_has_disallowed_characters(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    # A "/" would split into extra path segments and 404 on routing before
    # ever reaching the handler; a space is invalid per NAME_RE but stays a
    # single path segment, so it actually exercises _safe_name's rejection.
    resp = client.get("/replays/bad name")

    assert resp.status_code == 400


def test_get_replay_503_when_load_raises_operational_error(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    def raise_op_error(name):
        raise psycopg2.OperationalError("connection refused")
    monkeypatch.setattr(api.episode_store, "load_episode", raise_op_error)

    resp = client.get("/replays/demo-ep")

    assert resp.status_code == 503


def test_delete_replay_true_when_episode_removed(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api.episode_store, "delete_episode", lambda name: True)

    resp = client.delete("/replays/demo-ep")

    assert resp.status_code == 200
    assert resp.json() == {"name": "demo-ep", "deleted": True}


def test_delete_replay_false_when_episode_absent(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api.episode_store, "delete_episode", lambda name: False)

    resp = client.delete("/replays/demo-ep")

    assert resp.status_code == 200
    assert resp.json() == {"name": "demo-ep", "deleted": False}


def test_delete_replay_400_when_name_has_disallowed_characters(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    resp = client.delete("/replays/bad name")

    assert resp.status_code == 400


def test_delete_replay_503_when_delete_raises_operational_error(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    def raise_op_error(name):
        raise psycopg2.OperationalError("connection refused")
    monkeypatch.setattr(api.episode_store, "delete_episode", raise_op_error)

    resp = client.delete("/replays/demo-ep")

    assert resp.status_code == 503
