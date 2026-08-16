"""Tests for app/episode_store.py — Postgres-backed storage for the "Rerun
Theater" episode library (docs/episode_store.md). No real DB: _connect() is
always monkeypatched to a fake connection/cursor recording what would be
sent."""
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import episode_store  # noqa: E402


def _ensure_psycopg2_importable(monkeypatch):
    """Most dev boxes here have the real psycopg2 installed, but keep the
    test suite honest for an environment where it isn't: inject a minimal
    fake module so `import psycopg2` still works."""
    try:
        import psycopg2  # noqa: F401
        return
    except ImportError:
        pass

    fake = types.ModuleType("psycopg2")
    fake.connect = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "psycopg2", fake)


class FakeCursor:
    def __init__(self, fetch_rows=None, fetchone_row=None, rowcount=1):
        self.calls = []
        self._fetch_rows = fetch_rows if fetch_rows is not None else []
        self._fetchone_row = fetchone_row
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self._fetch_rows

    def fetchone(self):
        return self._fetchone_row

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeConn:
    def __init__(self, fetch_rows=None, fetchone_row=None, rowcount=1):
        self.closed = False
        self.cur = FakeCursor(fetch_rows, fetchone_row, rowcount)

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


# ── available() ──────────────────────────────────────────────────────────────

def test_available_false_when_env_vars_missing(monkeypatch):
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    assert episode_store.available() is False


@pytest.mark.parametrize("missing", ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"])
def test_available_false_when_one_env_var_missing(monkeypatch, missing):
    monkeypatch.setenv("POSTGRES_DB", "vtuber")
    monkeypatch.setenv("POSTGRES_USER", "vtuber")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.delenv(missing, raising=False)
    _ensure_psycopg2_importable(monkeypatch)
    assert episode_store.available() is False


def test_available_false_when_psycopg2_not_importable(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "vtuber")
    monkeypatch.setenv("POSTGRES_USER", "vtuber")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    assert episode_store.available() is False


def test_available_true_when_env_set_and_psycopg2_importable(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "vtuber")
    monkeypatch.setenv("POSTGRES_USER", "vtuber")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    _ensure_psycopg2_importable(monkeypatch)
    assert episode_store.available() is True


# ── save_episode ─────────────────────────────────────────────────────────────

def test_save_episode_new_insert_returns_true_and_correct_sql(monkeypatch):
    fake_conn = FakeConn(rowcount=1)
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)
    script = {
        "project": "ProjectA",
        "session_id": "sess123",
        "date": "2023-01-01",
        "events": [{"type": "assistant_text", "text": "hi"},
                    {"type": "assistant_text", "text": "there"}],
    }

    result = episode_store.save_episode("test_episode", script)

    assert result is True
    assert len(fake_conn.cur.calls) == 1
    sql, params = fake_conn.cur.calls[0]
    assert sql == episode_store.SAVE_SQL
    assert params["name"] == "test_episode"
    assert json.loads(params["script"]) == script
    assert params["project"] == "ProjectA"
    assert params["session_id"] == "sess123"
    assert params["episode_date"] == "2023-01-01"
    assert params["event_count"] == 2
    assert params["byte_size"] == len(params["script"].encode("utf-8"))
    assert params["uploaded_by"] == "operator"
    assert fake_conn.closed is True


def test_save_episode_missing_optional_fields_default_sensibly(monkeypatch):
    fake_conn = FakeConn(rowcount=1)
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    episode_store.save_episode("bare", {})

    _, params = fake_conn.cur.calls[0]
    assert params["project"] == ""
    assert params["session_id"] == ""
    assert params["episode_date"] == ""
    assert params["event_count"] == 0


def test_save_episode_conflicting_insert_returns_false(monkeypatch):
    fake_conn = FakeConn(rowcount=0)
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    result = episode_store.save_episode("test_episode", {"project": "ProjectA"})

    assert result is False


def test_save_episode_overwrite_sends_overwrite_sql(monkeypatch):
    fake_conn = FakeConn(rowcount=1)
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    episode_store.save_episode("test_episode", {"project": "ProjectA"}, overwrite=True)

    sql, _ = fake_conn.cur.calls[0]
    assert sql == episode_store.SAVE_OVERWRITE_SQL


def test_save_episode_default_not_overwrite_sends_save_sql(monkeypatch):
    fake_conn = FakeConn(rowcount=1)
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    episode_store.save_episode("test_episode", {"project": "ProjectA"})

    sql, _ = fake_conn.cur.calls[0]
    assert sql == episode_store.SAVE_SQL


def test_save_episode_closes_connection_even_when_execute_raises(monkeypatch):
    fake_conn = FakeConn()

    def explode(sql, params=None):
        raise RuntimeError("db exploded")
    fake_conn.cur.execute = explode
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    with pytest.raises(RuntimeError):
        episode_store.save_episode("test_episode", {"project": "ProjectA"})

    assert fake_conn.closed is True


# ── load_episode ─────────────────────────────────────────────────────────────

def test_load_episode_existing_dict_column_returned_as_is(monkeypatch):
    # psycopg2 decodes jsonb to a dict already — row[0] is that dict.
    fake_conn = FakeConn(fetchone_row=({"name": "test"},))
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    result = episode_store.load_episode("test_episode")

    assert result == {"name": "test"}
    sql, params = fake_conn.cur.calls[0]
    assert sql == episode_store.LOAD_SQL
    assert params == {"name": "test_episode"}
    assert fake_conn.closed is True


def test_load_episode_json_string_column_is_decoded(monkeypatch):
    # A driver/test double that hands back the raw column as a string must
    # still be tolerated (json.loads applied).
    fake_conn = FakeConn(fetchone_row=(json.dumps({"name": "test"}),))
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    result = episode_store.load_episode("test_episode")

    assert result == {"name": "test"}


def test_load_episode_missing_returns_none(monkeypatch):
    fake_conn = FakeConn(fetchone_row=None)
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    result = episode_store.load_episode("nonexistent")

    assert result is None
    assert fake_conn.closed is True


# ── list_episodes ────────────────────────────────────────────────────────────

def test_list_episodes_returns_sorted_names(monkeypatch):
    fake_conn = FakeConn(fetch_rows=[("ep1",), ("ep2",), ("ep0",)])
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    result = episode_store.list_episodes()

    # SQL itself sorts; this just checks the row->name unpacking is right.
    assert result == ["ep1", "ep2", "ep0"]
    sql, _ = fake_conn.cur.calls[0]
    assert sql == episode_store.LIST_SQL
    assert fake_conn.closed is True


def test_list_episodes_empty_table_returns_empty_list(monkeypatch):
    fake_conn = FakeConn(fetch_rows=[])
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    assert episode_store.list_episodes() == []


# ── list_episodes_detailed ───────────────────────────────────────────────────

def test_list_episodes_detailed_returns_metadata_dicts(monkeypatch):
    uploaded_at = datetime(2023, 1, 1, 10, 0)
    fake_conn = FakeConn(fetch_rows=[
        ("ep1", "proj1", "sess1", "2023-01-01", 5, 100, "user1", uploaded_at),
        ("ep2", "proj2", "sess2", "2023-01-02", 3, 150, "user2", None),
    ])
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    result = episode_store.list_episodes_detailed()

    assert result == [
        {"name": "ep1", "project": "proj1", "session_id": "sess1",
         "date": "2023-01-01", "event_count": 5, "byte_size": 100,
         "uploaded_by": "user1", "uploaded_at": "2023-01-01T10:00:00"},
        {"name": "ep2", "project": "proj2", "session_id": "sess2",
         "date": "2023-01-02", "event_count": 3, "byte_size": 150,
         "uploaded_by": "user2", "uploaded_at": None},
    ]
    sql, _ = fake_conn.cur.calls[0]
    assert sql == episode_store.LIST_DETAILED_SQL


def test_list_episodes_detailed_empty_table_returns_empty_list(monkeypatch):
    fake_conn = FakeConn(fetch_rows=[])
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    assert episode_store.list_episodes_detailed() == []


# ── delete_episode ───────────────────────────────────────────────────────────

def test_delete_episode_existing_returns_true(monkeypatch):
    fake_conn = FakeConn(rowcount=1)
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    result = episode_store.delete_episode("test_episode")

    assert result is True
    sql, params = fake_conn.cur.calls[0]
    assert sql == episode_store.DELETE_SQL
    assert params == {"name": "test_episode"}
    assert fake_conn.closed is True


def test_delete_episode_missing_returns_false(monkeypatch):
    fake_conn = FakeConn(rowcount=0)
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    assert episode_store.delete_episode("test_episode") is False


def test_delete_episode_closes_connection_even_when_execute_raises(monkeypatch):
    fake_conn = FakeConn()

    def explode(sql, params=None):
        raise RuntimeError("db exploded")
    fake_conn.cur.execute = explode
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    with pytest.raises(RuntimeError):
        episode_store.delete_episode("test_episode")

    assert fake_conn.closed is True


# ── ensure_schema ────────────────────────────────────────────────────────────

def test_ensure_schema_sends_create_table_sql_and_closes_connection(monkeypatch):
    fake_conn = FakeConn()
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    episode_store.ensure_schema()

    assert len(fake_conn.cur.calls) == 1
    sql, _ = fake_conn.cur.calls[0]
    assert sql == episode_store.CREATE_TABLE_SQL
    assert fake_conn.closed is True


def test_ensure_schema_closes_connection_even_when_execute_raises(monkeypatch):
    fake_conn = FakeConn()

    def explode(sql, params=None):
        raise RuntimeError("db exploded")
    fake_conn.cur.execute = explode
    monkeypatch.setattr(episode_store, "_connect", lambda: fake_conn)

    with pytest.raises(RuntimeError):
        episode_store.ensure_schema()

    assert fake_conn.closed is True
