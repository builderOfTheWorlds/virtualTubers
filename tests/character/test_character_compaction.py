"""WP-18 tests for app/character/compaction.py: nightly `messages` compaction.

Frozen test list (playbook §4 WP-18, items 1-8), integration on a fresh
pgserver database holding the virtualtubers `messages` table (built from the
frozen legacy DDL / the M2 shape in fakes_runtime.py). D-03: A drops noisy
types after N hours, B moves rows older than `hot_keep_days` into
`messages_archive`, C (daily partitions) turns B into DETACH/ATTACH; the code
handles both table shapes. Reference SQL: v4_reference_messages.sql section C
and .claude/prompts/character_v4_plan_validation.py:257-440.

Every cutoff is computed from the `now` argument (the job's `--at`), in UTC
(D-24): B's cutoff is UTC midnight of (now's UTC date - hot_keep_days), and a
partition messages_pYYYYMMDD moves when its whole day is before the cutoff.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from fakes_runtime import (count, create_legacy_messages, create_partitioned_messages,
                           insert_message, partitions_of, relkind, utc_midnight)
from pending import require

compaction = require("character.compaction", "app/character/compaction.py", wp="WP-18")
import psycopg2  # noqa: E402
from character import config  # noqa: E402

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
CUTOFF = utc_midnight(date(2026, 10, 8))            # hot_keep_days = 2


@pytest.fixture
def ccfg():
    base = config.load(env={}).compaction
    assert (base.noisy_types, base.noisy_keep_hours, base.hot_keep_days) == (("status_update",), 6, 2)
    return base


@pytest.fixture
def conn(pg):
    connection = psycopg2.connect(pg)
    try:
        yield connection
    finally:
        connection.close()


def _total(conn):
    archived = count(conn, "SELECT count(*) FROM messages_archive") \
        if relkind(conn, "messages_archive") else 0
    return count(conn, "SELECT count(*) FROM messages") + archived


@pytest.fixture
def plain(conn):
    create_legacy_messages(conn)
    for days in (9, 6, 5, 4, 3):                                  # 5 rows before the cutoff
        insert_message(conn, NOW - timedelta(days=days))
    insert_message(conn, CUTOFF + timedelta(minutes=1))           # just after it: stays
    insert_message(conn, NOW - timedelta(hours=1))
    conn.commit()
    return conn


@pytest.fixture
def partitioned(conn):
    create_partitioned_messages(conn, date(2026, 10, 1), date(2026, 10, 24))
    with conn.cursor() as cur:   # a partition whose name is not messages_pYYYYMMDD (item 5)
        cur.execute("CREATE TABLE messages_import_batch PARTITION OF messages "
                    "FOR VALUES FROM ('2026-09-20 00:00+00') TO ('2026-09-21 00:00+00')")
    conn.commit()
    for ts in (datetime(2026, 9, 20, 5, tzinfo=timezone.utc),     # the odd partition
               datetime(2026, 9, 25, 5, tzinfo=timezone.utc),     # DEFAULT, old
               datetime(2026, 9, 28, 5, tzinfo=timezone.utc),     # DEFAULT, old
               datetime(2026, 11, 30, 5, tzinfo=timezone.utc),    # DEFAULT, future: stays
               datetime(2026, 10, 3, 5, tzinfo=timezone.utc),
               datetime(2026, 10, 3, 6, tzinfo=timezone.utc),
               datetime(2026, 10, 7, 23, tzinfo=timezone.utc),    # last old day
               datetime(2026, 10, 8, 1, tzinfo=timezone.utc),     # first hot day: stays
               NOW - timedelta(hours=1)):
        insert_message(conn, ts)
    conn.commit()
    return conn


# T18.1
def test_noisy_types_older_than_n_hours_are_deleted(plain, ccfg):
    old_noise = insert_message(plain, NOW - timedelta(hours=7), type_="status_update")
    new_noise = insert_message(plain, NOW - timedelta(hours=5), type_="status_update")
    old_say = insert_message(plain, NOW - timedelta(hours=7), type_="character_say")
    plain.commit()
    deleted = compaction.compact_noisy(plain, ccfg.noisy_types, ccfg.noisy_keep_hours, now=NOW)
    plain.commit()
    assert deleted == 1
    ids = {row for (row,) in _ids(plain)}
    assert old_noise not in ids and new_noise in ids and old_say in ids


def _ids(conn, table="messages"):
    with conn.cursor() as cur:
        cur.execute(f"SELECT id::text FROM {table}")
        return cur.fetchall()


# T18.2
def test_plain_old_rows_move_in_batches_into_created_archive(plain):
    assert relkind(plain, "messages_archive") is None
    before = _total(plain)
    result = compaction.compact_archive(plain, 2, now=NOW, batch=2)
    assert result["shape"] == "plain"
    assert result["rows_moved"] == 5 and result["batches"] == 3
    assert relkind(plain, "messages_archive") == "r"
    assert count(plain, "SELECT count(*) FROM messages_archive") == 5
    assert count(plain, "SELECT count(*) FROM messages WHERE timestamp < %s", (CUTOFF,)) == 0
    assert count(plain, "SELECT count(*) FROM messages") == 2
    assert _total(plain) == before
    assert {"correlation_id", "character"} <= _archive_columns(plain)


def _archive_columns(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'messages_archive'")
        return {row[0] for row in cur.fetchall()}


# T18.3
def test_partitioned_old_partitions_are_detached_and_attached_to_archive(partitioned):
    before = _total(partitioned)
    result = compaction.compact_archive(partitioned, 2, now=NOW)
    assert result["shape"] == "partitioned"
    moved = [f"messages_p202610{d:02d}" for d in range(1, 8)]
    assert sorted(result["partitions_moved"]) == moved
    assert relkind(partitioned, "messages_archive") == "p"
    assert set(moved) <= set(partitions_of(partitioned, "messages_archive"))
    assert not set(moved) & set(partitions_of(partitioned, "messages"))
    assert "messages_p20261008" in partitions_of(partitioned, "messages")
    assert count(partitioned, "SELECT count(*) FROM messages_archive "
                              "WHERE timestamp >= '2026-10-01' AND timestamp < %s", (CUTOFF,)) == 3
    assert _total(partitioned) == before


# T18.4
def test_partitioned_old_default_rows_move_to_archive_default(partitioned):
    result = compaction.compact_archive(partitioned, 2, now=NOW)
    assert result["default_rows_moved"] == 2
    assert count(partitioned, "SELECT count(*) FROM messages_default") == 1      # Nov 30 stays
    assert "messages_archive_default" in partitions_of(partitioned, "messages_archive")
    assert count(partitioned, "SELECT count(*) FROM messages_archive_default") == 2


# T18.5
@pytest.mark.parametrize("name, expected", [
    ("messages_p20261001", date(2026, 10, 1)),
    ("messages_p20240229", date(2024, 2, 29)),
    ("messages_p2026101", None), ("messages_p202610011", None), ("messages_p20261301", None),
    ("messages_p20230229", None), ("messages_default", None), ("messages_pabcdefgh", None),
    ("xmessages_p20261001", None), ("messages_p20261001_old", None), ("messages_import_batch", None),
])
def test_partition_age_comes_from_the_name(name, expected):
    assert compaction.partition_day(name) == expected
    if expected is not None:
        assert compaction.partition_name(expected) == name


# T18.5
def test_partition_not_matching_the_pattern_is_ignored(partitioned):
    compaction.compact_archive(partitioned, 2, now=NOW)
    assert "messages_import_batch" in partitions_of(partitioned, "messages")
    assert count(partitioned, "SELECT count(*) FROM messages_import_batch") == 1


def _state(conn):
    tables = {"messages": partitions_of(conn, "messages"),
              "archive": partitions_of(conn, "messages_archive")}
    return tables, _total(conn), count(conn, "SELECT count(*) FROM messages")


# T18.6
@pytest.mark.parametrize("shape", ["plain", "partitioned"])
def test_a_second_run_changes_nothing(shape, request, ccfg):
    conn = request.getfixturevalue(shape)
    first = compaction.run(conn, ccfg, now=NOW, ingest_lag=0, max_lag=1000)
    assert first["status"] == "done"
    after_first = _state(conn)
    second = compaction.run(conn, ccfg, now=NOW, ingest_lag=0, max_lag=1000)
    assert second["status"] == "done"
    assert second["noisy_deleted"] == second["rows_moved"] == second["default_rows_moved"] == 0
    assert second["partitions_moved"] == []
    assert _state(conn) == after_first


# T18.7
@pytest.mark.parametrize("shape", ["plain", "partitioned"])
def test_dry_run_reports_counts_and_changes_nothing(shape, request, ccfg):
    conn = request.getfixturevalue(shape)
    insert_message(conn, NOW - timedelta(hours=8), type_="status_update")
    conn.commit()
    before = (_state(conn), relkind(conn, "messages_archive"))
    report = compaction.run(conn, ccfg, now=NOW, ingest_lag=0, max_lag=1000, dry_run=True)
    assert report["dry_run"] is True and report["status"] == "done"
    assert (_state(conn), relkind(conn, "messages_archive")) == before
    real = compaction.run(conn, ccfg, now=NOW, ingest_lag=0, max_lag=1000)
    for key in ("noisy_deleted", "rows_moved", "default_rows_moved"):
        assert report[key] == real[key], key
    assert sorted(report["partitions_moved"]) == sorted(real["partitions_moved"])
    assert report["noisy_deleted"] == 1


# T18.8
def test_skipped_with_precondition_when_ingest_lag_exceeds_max(plain, ccfg):
    insert_message(plain, NOW - timedelta(hours=8), type_="status_update")
    plain.commit()
    before = _state(plain)
    result = compaction.run(plain, ccfg, now=NOW, ingest_lag=1001, max_lag=1000)
    assert result["status"] == "precondition"
    assert "lag" in result["reason"]
    assert _state(plain) == before and relkind(plain, "messages_archive") is None
    assert compaction.run(plain, ccfg, now=NOW, ingest_lag=1000, max_lag=1000)["status"] == "done"


# T18.8 (how the lag is measured: ingest-type messages newer than the last ingested one)
def test_ingest_lag_counts_ingest_type_messages_after_the_last_ingested(plain):
    last = NOW - timedelta(hours=2)
    insert_message(plain, NOW - timedelta(hours=1), type_="agent_thinking")
    insert_message(plain, NOW - timedelta(minutes=5), type_="scene_event")
    insert_message(plain, NOW - timedelta(minutes=5), type_="status_update")
    plain.commit()
    types = ("agent_thinking", "character_say", "scene_event")
    # the fixture's newest character_say (now - 1 h) is after `last` too
    assert compaction.ingest_lag(plain, last, types) == 3
    assert compaction.ingest_lag(plain, None, types) == count(
        plain, "SELECT count(*) FROM messages WHERE type = ANY(%s)", (list(types),))
    assert compaction.detect_shape(plain) == "plain"
