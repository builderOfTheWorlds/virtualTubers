"""
Tests for services/message-logger/logger.py.
Requires services/message-logger/requirements.txt installed (psycopg2-binary)
in addition to the root requirements.txt (kafka-python).
psycopg2.connect and MessageConsumer are mocked so these tests never touch a
real Postgres or Kafka broker.
"""
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "message-logger"))

with patch("psycopg2.connect"):
    import logger


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "mafober")
    monkeypatch.setenv("POSTGRES_USER", "mafober")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)


def test_connect_db_reads_required_env_vars(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "5433")

    with patch("logger.psycopg2.connect") as fake_connect:
        fake_connect.return_value = MagicMock()
        conn = logger.connect_db()

    fake_connect.assert_called_once_with(
        host="db.internal", port="5433",
        dbname="mafober", user="mafober", password="secret",
    )
    assert conn.autocommit is True


def test_connect_db_defaults_host_and_port(monkeypatch):
    with patch("logger.psycopg2.connect") as fake_connect:
        fake_connect.return_value = MagicMock()
        logger.connect_db()

    fake_connect.assert_called_once_with(
        host="localhost", port="5432",
        dbname="mafober", user="mafober", password="secret",
    )


def test_connect_db_raises_when_required_var_missing(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(KeyError):
        logger.connect_db()


def _fake_message(msg_id="abc-123", from_="coder", to="manager", type_="task_complete", payload=None):
    return {
        "id": msg_id, "from": from_, "to": to, "type": type_,
        "payload": payload or {"task": "fix the login bug"},
        "timestamp": "2026-07-01T00:00:00+00:00",
    }


def test_main_inserts_each_consumed_message(monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("KAFKA_TOPIC", "vtuber.messages")

    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    messages = [_fake_message()]

    with patch("logger.connect_db", return_value=fake_conn), \
         patch("logger.MessageConsumer", return_value=iter(messages)):
        logger.main()

    insert_calls = [c for c in fake_cursor.execute.call_args_list if c.args[0] == logger.INSERT_SQL]
    assert len(insert_calls) == 1
    params = insert_calls[0].args[1]
    assert params["id"] == "abc-123"
    assert params["from"] == "coder"
    assert params["to"] == "manager"
    assert params["type"] == "task_complete"
    assert params["payload"] == '{"task": "fix the login bug"}'


def test_main_creates_table_before_consuming(monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("KAFKA_TOPIC", "vtuber.messages")

    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch("logger.connect_db", return_value=fake_conn), \
         patch("logger.MessageConsumer", return_value=iter([])):
        logger.main()

    first_sql = fake_cursor.execute.call_args_list[0].args[0]
    assert first_sql == logger.CREATE_TABLE_SQL


def test_main_fails_fast_when_kafka_env_vars_missing(monkeypatch):
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("KAFKA_TOPIC", raising=False)

    with pytest.raises(KeyError):
        logger.main()
