"""WP-17 tests: the `messages` table's M1 and M2 changes and the v4 logger.

Frozen test list (playbook §4 WP-17, items 1-8), integration on pgserver (or
CHARACTER_TEST_DSN). Plan §3.4 and docs/charcterProfileGenerationNotes/v4_reference_messages.sql:

- M1 (additive, safe live): `character TEXT` plus indexes on (timestamp) and
  (character, timestamp), in the logger's startup DDL. The logger fills
  `character` with bus_attribution.character_for_message(msg) (D-04).
- M2 (operator-run, logger stopped): services/message-logger/migrate_partitioned.py
  builds `messages` PARTITION BY RANGE (timestamp), PK (id, timestamp), daily
  UTC partitions messages_pYYYYMMDD plus a DEFAULT partition, copies every
  row and keeps `messages_legacy`.
- The logger detects the shape (pg_class.relkind): ON CONFLICT (id, timestamp)
  when partitioned, and it creates partitions today..today+14 at startup.

Pending guard: logger.py already exists, so this file keys on the NEW target
services/message-logger/migrate_partitioned.py (both are targets of the same
harness spec). The schema-copy checks on docs/sql/02_create_tables.sql and the
Dockerfile skip where docs/ is absent (the harness sandbox copies no docs/,
tools/qwen_worker/sandbox.py SANDBOX_DIRS); they run in the real tree and in
the second WP-17 spec, which stages that file.

M2 column set (tracker question P3a-6, recommendation (a)): the reference M2
DDL omits the logger's correlation_id / causation_id columns
(services/message-logger/logger.py:29-30, 36-38); the migration keeps them,
so no column is lost.
"""
import importlib.util
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
LOGGER_DIR = ROOT / "services" / "message-logger"
for path in (ROOT / "app", LOGGER_DIR, TESTS / "character"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pending import skip_if_pending  # noqa: E402

skip_if_pending("services/message-logger/migrate_partitioned.py", wp="WP-17")

import fakes_runtime as fr  # noqa: E402
import psycopg2  # noqa: E402

with patch("psycopg2.connect"), patch("log_filter_control.redis.Redis.from_url"):
    import logger  # noqa: E402
import migrate_partitioned  # noqa: E402

# the `pg` fixture of tests/character/conftest.py (fresh empty database per test)
_spec = importlib.util.spec_from_file_location("character_v4_pg_fixtures",
                                               TESTS / "character" / "conftest.py")
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
_pg_admin, pg = _fixtures._pg_admin, _fixtures.pg

pytestmark = pytest.mark.integration

DOCS_SQL = ROOT / "docs" / "sql" / "02_create_tables.sql"
NO_DOCS = pytest.mark.skipif(not DOCS_SQL.is_file(),
                             reason="docs/ is not in the harness sandbox; checked in the real tree")
TODAY = datetime.now(timezone.utc).date()


@pytest.fixture
def conn(pg):
    connection = psycopg2.connect(pg)
    try:
        yield connection
    finally:
        connection.close()


def _msg(from_, type_, payload, ts=None, msg_id=None):
    return {"id": msg_id or str(uuid.uuid4()), "from": from_, "to": "broadcast", "type": type_,
            "payload": payload, "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
            "correlation_id": None, "causation_id": None}


def _run_logger(pg, messages, monkeypatch):
    """logger.main() against the real test database, fed `messages`."""
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka.test:9092")
    monkeypatch.setenv("KAFKA_TOPIC", "vtuber.messages")
    live = psycopg2.connect(pg)
    live.autocommit = True
    keep_all = MagicMock()
    keep_all.is_excluded.return_value = False
    try:
        with patch("logger.connect_db", return_value=live), \
             patch("logger.MessageConsumer", return_value=iter(messages)), \
             patch("logger.LogFilterControl.from_config", return_value=keep_all):
            logger.main()
    finally:
        live.close()


def _columns(conn, table):
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s", (table,))
        return {row[0] for row in cur.fetchall()}


# T17.1
def test_startup_ddl_on_legacy_table_adds_character_and_indexes_idempotently(conn):
    fr.create_legacy_messages(conn, with_m1=False)
    with conn.cursor() as cur:
        cur.execute('INSERT INTO messages (id, "from", "to", type, payload, timestamp) '
                    "VALUES (%s, 'tuber_3', 'broadcast', 'agent_thinking', '{}', now())",
                    (str(uuid.uuid4()),))
    conn.commit()
    assert "character" not in _columns(conn, "messages")
    for _ in range(2):
        with conn.cursor() as cur:
            cur.execute(logger.CREATE_TABLE_SQL)
        conn.commit()
    assert "character" in _columns(conn, "messages")
    assert {"idx_messages_timestamp", "idx_messages_character_ts"} <= fr.index_names(conn, "messages")
    assert fr.count(conn, "SELECT count(*) FROM messages") == 1


# T17.2
def test_insert_fills_character_with_character_for_message(pg, conn, monkeypatch):
    say = _msg("tuber_3", "character_say", {"character": "engineer", "text": "Pushed."})
    thought = _msg("char:tester", "agent_thinking", {"text": "Red again."})
    seat = _msg("tuber_3", "agent_thinking", {"text": "a seat thought (P3a-1: NULL)"})
    narration = _msg("char-live:ashiorid_office", "scene_event",
                     {"kind": "narration", "character": None, "text": "Lights."})
    for msg, expected in ((say, "engineer"), (thought, "tester"), (seat, None), (narration, None)):
        assert logger.message_row(msg)["character"] == expected
    _run_logger(pg, [say, thought, seat, narration], monkeypatch)
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, character FROM messages")
        stored = dict(cur.fetchall())
    assert stored == {say["id"]: "engineer", thought["id"]: "tester", seat["id"]: None,
                      narration["id"]: None}


# T17.3
def test_shape_is_plain_before_m2_and_partitioned_after(conn):
    fr.create_legacy_messages(conn)
    with conn.cursor() as cur:
        assert logger.detect_shape(cur) == "plain"
    migrate_partitioned.migrate(conn, today=TODAY)
    with conn.cursor() as cur:
        assert logger.detect_shape(cur) == "partitioned"
    assert fr.relkind(conn, "messages") == "p"


# T17.4
def test_partitioned_insert_dedupes_on_id_and_timestamp(pg, conn, monkeypatch):
    fr.create_legacy_messages(conn)
    migrate_partitioned.migrate(conn, today=TODAY)
    assert "ON CONFLICT (id, timestamp)" in logger.INSERT_PARTITIONED_SQL
    assert "%(character)s" in logger.INSERT_PARTITIONED_SQL
    msg = _msg("char:engineer", "agent_thinking", {"text": "once"})
    _run_logger(pg, [msg, dict(msg)], monkeypatch)
    _run_logger(pg, [dict(msg)], monkeypatch)            # redelivery after a restart
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), max(character) FROM messages WHERE id = %s", (msg["id"],))
        assert cur.fetchone() == (1, "engineer")


# T17.5
def test_ensure_partitions_creates_today_through_today_plus_14_idempotently(conn):
    fr.create_legacy_messages(conn)
    migrate_partitioned.migrate(conn, today=TODAY)
    later = TODAY + timedelta(days=30)
    expected = [f"messages_p{later + timedelta(days=i):%Y%m%d}" for i in range(15)]
    with conn.cursor() as cur:
        created = logger.ensure_partitions(cur, today=later)
    conn.commit()
    assert sorted(created) == expected
    assert set(expected) <= set(fr.partitions_of(conn, "messages"))
    with conn.cursor() as cur:
        assert logger.ensure_partitions(cur, today=later) == []
    conn.commit()
    # each partition is exactly its UTC day
    with conn.cursor() as cur:
        cur.execute("SELECT pg_get_expr(c.relpartbound, c.oid) FROM pg_class c WHERE relname = %s",
                    (expected[0],))
        bound = cur.fetchone()[0]
    assert f"{later:%Y-%m-%d} 00:00:00+00" in bound and \
        f"{later + timedelta(days=1):%Y-%m-%d} 00:00:00+00" in bound
    # plain shape: nothing to do, no error
    other = TODAY + timedelta(days=90)
    with conn.cursor() as cur:
        cur.execute("DROP TABLE messages CASCADE")
    conn.commit()
    fr.create_legacy_messages(conn)
    with conn.cursor() as cur:
        assert logger.ensure_partitions(cur, today=other) == []


def _legacy_with_rows(conn):
    fr.create_legacy_messages(conn)
    now = datetime.now(timezone.utc)
    corr = str(uuid.uuid4())
    ids = [fr.insert_message(conn, now - timedelta(days=age, hours=1), character=character,
                             correlation_id=corr if age == 3 else None)
           for age, character in ((6, "ceo"), (3, "engineer"), (1, None), (0, "tester"))]
    conn.commit()
    return ids, corr


def _snapshot(conn, table):
    with conn.cursor() as cur:
        cur.execute(f'SELECT id::text, "from", "to", type, payload::text, timestamp, ingested_at, '
                    f"correlation_id::text, causation_id::text, character FROM {table} ORDER BY id")
        return cur.fetchall()


# T17.6
def test_migrate_dry_run_changes_nothing(pg, conn, monkeypatch, capsys):
    _legacy_with_rows(conn)
    before = _snapshot(conn, "messages")
    result = migrate_partitioned.migrate(conn, today=TODAY, dry_run=True)
    assert result["dry_run"] is True and result["rows"] == 4
    assert fr.relkind(conn, "messages") == "r"
    assert fr.relkind(conn, "messages_legacy") is None and fr.relkind(conn, "messages_new") is None
    assert _snapshot(conn, "messages") == before
    monkeypatch.setattr(migrate_partitioned, "connect_db", lambda: psycopg2.connect(pg))
    assert migrate_partitioned.main(["--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out.lower()
    assert fr.relkind(conn, "messages") == "r"


# T17.7
def test_migration_copies_every_row_keeps_legacy_and_refuses_a_second_run(pg, conn, monkeypatch):
    ids, corr = _legacy_with_rows(conn)
    before = _snapshot(conn, "messages")
    result = migrate_partitioned.migrate(conn, today=TODAY)
    assert result["dry_run"] is False and result["rows"] == 4
    assert fr.relkind(conn, "messages") == "p"
    assert fr.relkind(conn, "messages_legacy") == "r"
    assert _snapshot(conn, "messages") == before == _snapshot(conn, "messages_legacy")
    assert {"correlation_id", "causation_id", "character"} <= _columns(conn, "messages")
    parts = fr.partitions_of(conn, "messages")
    first = (datetime.now(timezone.utc) - timedelta(days=6, hours=1)).date()
    assert "messages_default" in parts
    assert f"messages_p{first:%Y%m%d}" in parts and f"messages_p{TODAY + timedelta(days=14):%Y%m%d}" in parts
    assert fr.count(conn, "SELECT count(*) FROM messages_default") == 0
    assert {"idx_messages_p_to", "idx_messages_p_type", "idx_messages_p_character_ts",
            "idx_messages_p_correlation"} <= fr.index_names(conn, "messages")

    with pytest.raises(migrate_partitioned.MigrationRefused):
        migrate_partitioned.migrate(conn, today=TODAY)
    monkeypatch.setattr(migrate_partitioned, "connect_db", lambda: psycopg2.connect(pg))
    assert migrate_partitioned.main([]) == 1
    assert _snapshot(conn, "messages") == before


# T17.8
def test_m1_statements_appear_verbatim_in_logger_py():
    source = (LOGGER_DIR / "logger.py").read_text(encoding="utf-8")
    for statement in fr.M1_STATEMENTS:
        assert statement in source, statement
        assert statement in logger.CREATE_TABLE_SQL, statement


# T17.8
@NO_DOCS
def test_m1_statements_appear_verbatim_in_docs_sql():
    text = DOCS_SQL.read_text(encoding="utf-8")
    for statement in fr.M1_STATEMENTS:
        assert statement in text, statement


# plan §3.4 "The logger Dockerfile gains one COPY line" (checked with the schema copies)
@NO_DOCS
def test_logger_dockerfile_copies_bus_attribution():
    dockerfile = (LOGGER_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY app/bus_attribution.py /app/bus_attribution.py" in dockerfile
    assert dockerfile.index("bus_attribution.py") < dockerfile.index("ENTRYPOINT")


# the frozen M1 copy in fakes_runtime matches the validated reference SQL
def test_reference_m1_matches_the_frozen_statements():
    reference = (ROOT / "docs" / "charcterProfileGenerationNotes" / "v4_reference_messages.sql")
    if not reference.is_file():
        pytest.skip("docs/ is not in the harness sandbox")
    text = reference.read_text(encoding="utf-8")
    for statement in fr.M1_STATEMENTS:
        assert statement in text
