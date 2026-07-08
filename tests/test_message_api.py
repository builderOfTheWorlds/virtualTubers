"""
Tests for services/message-api/api.py.
Requires services/message-api/requirements.txt installed (fastapi, uvicorn,
redis) in addition to the root requirements.txt (kafka-python).
KafkaProducer and redis.Redis are mocked at import time so these tests never
touch a real broker or Redis instance.
"""
import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest
import redis
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "message-api"))

os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("KAFKA_TOPIC", "test-topic")

with patch("message_bus.KafkaProducer"), patch("worker_control.redis.Redis.from_url"):
    import api


@pytest.fixture
def client():
    api.producer.send = MagicMock()
    api.control._client = MagicMock()
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
