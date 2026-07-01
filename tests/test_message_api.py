"""
Tests for services/message-api/api.py.
Requires services/message-api/requirements.txt installed (fastapi, uvicorn)
in addition to the root requirements.txt (kafka-python).
KafkaProducer is mocked at import time so these tests never touch a real broker.
"""
import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "message-api"))

os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("KAFKA_TOPIC", "test-topic")

with patch("message_bus.KafkaProducer"):
    import api


@pytest.fixture
def client():
    api.producer.send = MagicMock()
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
