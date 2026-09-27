"""
Tests for app/replay_logs.py.
psycopg2.connect is mocked — these tests never touch a real Postgres.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import replay_logs


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "virtualtubers")
    monkeypatch.setenv("POSTGRES_USER", "virtualtubers")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)


def _fake_conn(rows):
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.fetchall.return_value = rows
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor
    return fake_conn, fake_cursor


def test_fetch_container_logs_returns_empty_list_for_no_services():
    assert replay_logs.fetch_container_logs([]) == []


def test_fetch_container_logs_builds_suffix_anchored_regex_per_service():
    fake_conn, fake_cursor = _fake_conn([])
    with patch("replay_logs.connect_db", return_value=fake_conn):
        replay_logs.fetch_container_logs(["worker-coder", "worker-roundtable"])

    sql, params = fake_cursor.execute.call_args[0]
    assert "container_name ~ %(svc0)s" in sql
    assert "container_name ~ %(svc1)s" in sql
    assert params["svc0"] == r"-worker\-coder-[0-9]+$"
    assert params["svc1"] == r"-worker\-roundtable-[0-9]+$"
    fake_conn.close.assert_called_once()


def test_fetch_container_logs_pattern_does_not_match_a_longer_service_name():
    """Regression guard: "worker-coder" must not match the container for
    "worker-coder-native" — a plain substring/LIKE would."""
    import re
    pattern = replay_logs._service_suffix_pattern("worker-coder")
    assert re.search(pattern, "virtualtubers-worker-coder-1")
    assert not re.search(pattern, "virtualtubers-worker-coder-native-1")


def test_fetch_container_logs_applies_since_and_limit_and_reverses_to_oldest_first():
    rows = [
        {"container_name": "virtualtubers-worker-coder-1", "stream": "stdout",
         "message": "newer", "log_timestamp": datetime(2026, 1, 1, 0, 0, 2)},
        {"container_name": "virtualtubers-worker-coder-1", "stream": "stdout",
         "message": "older", "log_timestamp": datetime(2026, 1, 1, 0, 0, 1)},
    ]
    fake_conn, fake_cursor = _fake_conn(rows)
    since = datetime(2026, 1, 1)
    with patch("replay_logs.connect_db", return_value=fake_conn):
        result = replay_logs.fetch_container_logs(["worker-coder"], since=since, limit=50)

    _, params = fake_cursor.execute.call_args[0]
    assert params["since"] == since
    assert params["limit"] == 50
    assert [r["message"] for r in result] == ["older", "newer"]


def test_fetch_messages_returns_empty_list_for_no_worker_ids():
    assert replay_logs.fetch_messages([]) == []


def test_fetch_messages_matches_from_or_to_any_worker_id():
    fake_conn, fake_cursor = _fake_conn([])
    with patch("replay_logs.connect_db", return_value=fake_conn):
        replay_logs.fetch_messages(["coder", "roundtable"], limit=100)

    sql, params = fake_cursor.execute.call_args[0]
    assert '"to" = ANY(%(ids)s)' in sql
    assert '"from" = ANY(%(ids)s)' in sql
    assert params["ids"] == ["coder", "roundtable"]
    assert params["limit"] == 100
    assert "since" not in params


def test_fetch_messages_applies_since_when_given():
    fake_conn, fake_cursor = _fake_conn([])
    since = datetime(2026, 1, 1)
    with patch("replay_logs.connect_db", return_value=fake_conn):
        replay_logs.fetch_messages(["coder"], since=since)

    sql, params = fake_cursor.execute.call_args[0]
    assert "timestamp > %(since)s" in sql
    assert params["since"] == since
