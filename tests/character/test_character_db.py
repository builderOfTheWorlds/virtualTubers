"""WP-04 integration tests for app/character/db.py (plan §6, D-05, D-25).

Frozen test list (playbook §4 WP-04, items 1-7). Every test gets a fresh,
empty database from the `pg` fixture (tests/character/conftest.py):
CHARACTER_TEST_DSN, else pgserver, else skip.
"""
import shutil

import psycopg2
import psycopg2.errors
import pytest

from pending import require

db = require("character.db", "app/character/db.py")

pytestmark = pytest.mark.integration

#: The 23 tables of app/character/sql/001_init.sql (plan §6).
V4_TABLES = {
    "source_works", "source_chapters", "source_utterances", "source_scenes",
    "timeline_events", "characters", "character_agents", "character_backstories",
    "character_baselines", "loop_weeks", "experience_events", "daily_summaries",
    "memory_fragments", "week_knowledge_nodes", "week_knowledge_edges",
    "fragment_lead_up", "fragment_links", "fragment_unlocks", "fragment_recalls",
    "character_jobs", "character_artifacts", "ingest_status", "backup_runs",
}


@pytest.fixture
def conn(pg):
    connection = psycopg2.connect(pg)
    yield connection
    connection.close()


def _tables(connection):
    with connection.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")
        rows = {row[0] for row in cur.fetchall()}
    connection.rollback()
    return rows


# T04.1
def test_migrate_empty_db_applies_001_and_creates_all_tables(conn):
    assert _tables(conn) == set()
    applied = db.migrate(conn)
    assert applied == ["001_init"]
    assert _tables(conn) == V4_TABLES | {"schema_migrations"}
    assert len(V4_TABLES) == 23
    with conn.cursor() as cur:
        cur.execute("SELECT version, sha256, applied_at FROM schema_migrations")
        rows = cur.fetchall()
    assert [row[0] for row in rows] == ["001_init"]
    assert len(rows[0][1]) == 64 and rows[0][2] is not None


# T04.2
def test_second_migrate_applies_nothing(conn):
    assert db.migrate(conn) == ["001_init"]
    assert db.migrate(conn) == []
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM schema_migrations")
        assert cur.fetchone()[0] == 1


# T04.3
def test_changed_checksum_raises_migration_error(conn, tmp_path):
    sql_dir = tmp_path / "sql"
    shutil.copytree(db.SQL_DIR, sql_dir)
    assert db.migrate(conn, sql_dir=sql_dir) == ["001_init"]
    migration = sql_dir / "001_init.sql"
    migration.write_text(migration.read_text(encoding="utf-8") + "\n-- edited after apply\n",
                         encoding="utf-8")
    with pytest.raises(db.MigrationError) as caught:
        db.migrate(conn, sql_dir=sql_dir)
    assert "001_init" in str(caught.value)


# T04.4
def test_advisory_lock_first_session_wins_second_fails(pg):
    first, second = psycopg2.connect(pg), psycopg2.connect(pg)
    try:
        assert db.advisory_lock(first, "weekly-reset:ashiorid_office") is True
        assert db.advisory_lock(second, "weekly-reset:ashiorid_office") is False
        assert db.advisory_lock(second, "daily-maintenance:ashiorid_office") is True
    finally:
        first.close()
        second.close()
    third = psycopg2.connect(pg)
    try:
        assert db.advisory_lock(third, "weekly-reset:ashiorid_office") is True
    finally:
        third.close()


# T04.5
def test_transaction_rolls_back_when_block_raises(conn, pg):
    db.migrate(conn)
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            with conn.cursor() as cur:
                cur.execute("INSERT INTO source_works (id, title, config_sha256) "
                            "VALUES ('rolled-back', 'x', 'x')")
            raise RuntimeError("boom")
    with db.transaction(conn):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO source_works (id, title, config_sha256) "
                        "VALUES ('committed', 'x', 'x')")
    other = psycopg2.connect(pg)
    try:
        with other.cursor() as cur:
            cur.execute("SELECT id FROM source_works ORDER BY id")
            assert [row[0] for row in cur.fetchall()] == ["committed"]
    finally:
        other.close()


# T04.6
def test_vector_extension_is_present(conn):
    db.migrate(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert cur.fetchone() == ("vector",)
        cur.execute("SELECT '[1,0,0]'::vector <=> '[1,0,0]'::vector")
        assert cur.fetchone()[0] == pytest.approx(0.0)


# T04.7
def test_reader_role_can_select_but_not_insert(conn):
    with conn.cursor() as cur:
        cur.execute("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = "
                    "'character_reader') THEN CREATE ROLE character_reader NOLOGIN; "
                    "END IF; END $$;")
    conn.commit()
    db.migrate(conn)
    with conn.cursor() as cur:
        cur.execute("SET ROLE character_reader")
        cur.execute("SELECT count(*) FROM characters")
        assert cur.fetchone()[0] == 0
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("INSERT INTO source_works (id, title, config_sha256) "
                        "VALUES ('x', 'x', 'x')")
    conn.rollback()
