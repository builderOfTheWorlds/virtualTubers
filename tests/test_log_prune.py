"""
Tests for app/log_prune.py.
psycopg2.connect is mocked — these tests never touch a real Postgres instance.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import log_prune


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "virtualtubers")
    monkeypatch.setenv("POSTGRES_USER", "virtualtubers")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)


def _fake_conn():
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.rowcount = 3
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor
    return fake_conn, fake_cursor


def test_prune_logs_raises_when_no_bounds_given():
    with pytest.raises(ValueError):
        log_prune.prune_logs()


def test_prune_logs_with_only_after():
    fake_conn, fake_cursor = _fake_conn()
    after = datetime(2026, 7, 1)

    with patch("log_prune.connect_db", return_value=fake_conn):
        deleted = log_prune.prune_logs(after=after)

    fake_cursor.execute.assert_called_once_with(
        "DELETE FROM container_logs WHERE log_timestamp >= %(after)s;",
        {"after": after},
    )
    fake_conn.commit.assert_called_once()
    fake_conn.close.assert_called_once()
    assert deleted == 3


def test_prune_logs_with_only_before():
    fake_conn, fake_cursor = _fake_conn()
    before = datetime(2026, 7, 2)

    with patch("log_prune.connect_db", return_value=fake_conn):
        deleted = log_prune.prune_logs(before=before)

    fake_cursor.execute.assert_called_once_with(
        "DELETE FROM container_logs WHERE log_timestamp < %(before)s;",
        {"before": before},
    )
    assert deleted == 3


def test_prune_logs_with_both_bounds():
    fake_conn, fake_cursor = _fake_conn()
    after = datetime(2026, 7, 1)
    before = datetime(2026, 7, 2)

    with patch("log_prune.connect_db", return_value=fake_conn):
        log_prune.prune_logs(after=after, before=before)

    fake_cursor.execute.assert_called_once_with(
        "DELETE FROM container_logs WHERE log_timestamp >= %(after)s AND log_timestamp < %(before)s;",
        {"after": after, "before": before},
    )


def test_prune_logs_closes_connection_even_on_error():
    fake_conn, fake_cursor = _fake_conn()
    fake_cursor.execute.side_effect = RuntimeError("boom")

    with patch("log_prune.connect_db", return_value=fake_conn):
        with pytest.raises(RuntimeError):
            log_prune.prune_logs(after=datetime(2026, 7, 1))

    fake_conn.close.assert_called_once()
