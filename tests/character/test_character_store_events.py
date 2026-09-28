"""WP-05 integration tests for app/character/store/events.py.

Frozen test list (playbook §4 WP-05, items 7-9): experience_events (written
only by ingest, deduped on (message_id, character_id)) and ingest_status (the
ingest heartbeat, plan §3.3).
"""
from datetime import date, datetime, timedelta, timezone

import psycopg2
import pytest

from fakes import seed_character
from pending import require

events = require("character.store.events", "app/character/store/events.py")

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)  # Thu, week 1 of the office epoch
DAY = date(2026, 10, 1)


def _row(message_id, character_id, ts=T0, day=DAY, text="hello", msg_type="character_say"):
    return {
        "message_id": message_id, "character_id": character_id, "msg_type": msg_type,
        "from_agent": "char:ceo", "scene_id": "standup", "visibility": "present",
        "text": text, "payload": {"text": text}, "ts": ts, "loop_week": 1, "loop_day": day,
    }


# T05.7
def test_insert_many_ignores_duplicates_and_returns_inserted_count(pg_conn):
    ceo = seed_character(pg_conn, "ceo")
    tech_lead = seed_character(pg_conn, "tech_lead")
    batch = [_row("m1", ceo), _row("m1", tech_lead), _row("m2", ceo)]
    assert events.insert_many(pg_conn, batch) == 3
    pg_conn.commit()
    redelivered = [_row("m1", ceo), _row("m2", ceo), _row("m3", ceo), _row("m3", ceo)]
    assert events.insert_many(pg_conn, redelivered) == 1
    assert events.insert_many(pg_conn, []) == 0
    pg_conn.commit()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT message_id, character_id FROM experience_events "
                    "ORDER BY message_id, character_id")
        assert len(cur.fetchall()) == 4


# T05.8
def test_events_for_day_is_ordered_by_ts(pg_conn):
    ceo = seed_character(pg_conn, "ceo")
    tester = seed_character(pg_conn, "tester")
    rows = [
        _row("late", ceo, ts=T0 + timedelta(hours=3), text="third"),
        _row("early", ceo, ts=T0, text="first"),
        _row("middle", ceo, ts=T0 + timedelta(minutes=5), text="second"),
        _row("other-char", tester, ts=T0 + timedelta(minutes=1)),
        _row("other-day", ceo, ts=T0 + timedelta(days=1), day=DAY + timedelta(days=1)),
    ]
    events.insert_many(pg_conn, rows)
    pg_conn.commit()
    got = events.events_for_day(pg_conn, ceo, DAY)
    assert [event["message_id"] for event in got] == ["early", "middle", "late"]
    assert [event["text"] for event in got] == ["first", "second", "third"]
    assert got[0]["payload"] == {"text": "first"}
    assert got[0]["ts"] == T0
    assert events.events_for_day(pg_conn, ceo, date(2026, 9, 1)) == []


# T05.9
def test_update_ingest_status_touches_only_its_own_columns(pg, pg_conn):
    ts1 = T0
    events.update_ingest_status(pg_conn, "character-ingest", last_message_ts=ts1,
                                messages_seen=10, rows_written=7, unmapped=3)
    events.update_ingest_status(pg_conn, "other-group", last_message_ts=ts1,
                                messages_seen=1, rows_written=1)
    pg_conn.commit()

    # counts are deltas, added in SQL; a None timestamp keeps the stored one
    events.update_ingest_status(pg_conn, "character-ingest", last_message_ts=None,
                                messages_seen=5, rows_written=2)
    pg_conn.commit()
    status = events.ingest_status(pg_conn, "character-ingest")
    assert status["messages_seen"] == 15
    assert status["rows_written"] == 9
    assert status["unmapped"] == 3
    assert status["last_message_ts"] == ts1
    assert status["last_committed_at"] is not None

    # a concurrent writer's increment is not lost (no read-modify-write)
    other = psycopg2.connect(pg)
    try:
        events.update_ingest_status(other, "character-ingest", messages_seen=100)
        other.commit()
    finally:
        other.close()
    events.update_ingest_status(pg_conn, "character-ingest", last_message_ts=ts1 + timedelta(hours=1),
                                messages_seen=1)
    pg_conn.commit()
    status = events.ingest_status(pg_conn, "character-ingest")
    assert status["messages_seen"] == 116
    assert status["last_message_ts"] == ts1 + timedelta(hours=1)

    untouched = events.ingest_status(pg_conn, "other-group")
    assert (untouched["messages_seen"], untouched["rows_written"], untouched["unmapped"]) == (1, 1, 0)
    assert events.ingest_status(pg_conn, "nobody") is None
