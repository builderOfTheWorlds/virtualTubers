"""
Tests for services/log-shipper/shipper.py.
Requires services/log-shipper/requirements.txt installed (docker, psycopg2-binary).
psycopg2.connect and the docker client are mocked so these tests never touch a
real Postgres instance or Docker daemon.
"""
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "log-shipper"))

with patch("psycopg2.connect"), patch("docker.from_env"):
    import shipper


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "mafober")
    monkeypatch.setenv("POSTGRES_USER", "mafober")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("HOSTNAME", "abc123")
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)


def test_connect_db_reads_required_env_vars(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "5433")

    with patch("shipper.psycopg2.connect") as fake_connect:
        fake_connect.return_value = MagicMock()
        conn = shipper.connect_db()

    fake_connect.assert_called_once_with(
        host="db.internal", port="5433",
        dbname="mafober", user="mafober", password="secret",
    )
    assert conn.autocommit is True


def test_connect_db_defaults_host_and_port(monkeypatch):
    with patch("shipper.psycopg2.connect") as fake_connect:
        fake_connect.return_value = MagicMock()
        shipper.connect_db()

    fake_connect.assert_called_once_with(
        host="localhost", port="5432",
        dbname="mafober", user="mafober", password="secret",
    )


def test_connect_db_raises_when_required_var_missing(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(KeyError):
        shipper.connect_db()


def test_parse_log_line_splits_timestamp_and_message():
    raw = b"2026-07-02T00:00:00.123456789Z hello world\n"
    timestamp, message = shipper.parse_log_line(raw)
    assert timestamp == "2026-07-02T00:00:00.123456789Z"
    assert message == "hello world"


def test_parse_log_line_handles_empty_message():
    raw = b"2026-07-02T00:00:00.123456789Z \n"
    timestamp, message = shipper.parse_log_line(raw)
    assert timestamp == "2026-07-02T00:00:00.123456789Z"
    assert message == ""


def test_get_project_label_reads_own_container_label():
    fake_self = MagicMock()
    fake_self.labels = {"com.docker.compose.project": "virtualtubers"}
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_self

    label = shipper.get_project_label(fake_client)

    fake_client.containers.get.assert_called_once_with("abc123")
    assert label == "virtualtubers"


def _fake_container(container_id, name):
    container = MagicMock()
    container.id = container_id
    container.name = name
    return container


def test_discover_and_follow_starts_stdout_and_stderr_threads_for_new_container():
    new_container = _fake_container("id-1", "worker-coder")
    fake_client = MagicMock()
    fake_client.containers.list.return_value = [new_container]
    followed = set()

    with patch("shipper.threading.Thread") as fake_thread_cls:
        fake_thread = MagicMock()
        fake_thread_cls.return_value = fake_thread
        shipper.discover_and_follow(fake_client, "virtualtubers", followed)

    fake_client.containers.list.assert_called_once_with(
        filters={"label": "com.docker.compose.project=virtualtubers"}
    )
    assert followed == {"id-1"}
    assert fake_thread_cls.call_count == 2
    started_streams = {call.kwargs["args"][1] for call in fake_thread_cls.call_args_list}
    assert started_streams == {"stdout", "stderr"}
    assert fake_thread.start.call_count == 2


def test_discover_and_follow_skips_already_followed_container():
    existing = _fake_container("id-1", "worker-coder")
    fake_client = MagicMock()
    fake_client.containers.list.return_value = [existing]
    followed = {"id-1"}

    with patch("shipper.threading.Thread") as fake_thread_cls:
        shipper.discover_and_follow(fake_client, "virtualtubers", followed)

    fake_thread_cls.assert_not_called()


def test_main_creates_table_then_watches_project(monkeypatch):
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    fake_client = MagicMock()

    class StopLoop(Exception):
        pass

    with patch("shipper.connect_db", return_value=fake_conn), \
         patch("shipper.docker.from_env", return_value=fake_client), \
         patch("shipper.get_project_label", return_value="virtualtubers"), \
         patch("shipper.discover_and_follow") as fake_discover, \
         patch("shipper.time.sleep", side_effect=StopLoop):
        with pytest.raises(StopLoop):
            shipper.main()

    fake_cursor.execute.assert_called_once_with(shipper.CREATE_TABLE_SQL)
    fake_discover.assert_called_once_with(fake_client, "virtualtubers", set())
