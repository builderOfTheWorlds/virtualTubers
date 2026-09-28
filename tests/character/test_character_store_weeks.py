"""WP-05 integration tests for app/character/store/weeks.py.

Frozen test list (playbook §4 WP-05, items 4-6): loop_weeks and the
exactly-once reset_steps ledger (plan §5, the weekly-reset row). Uses the
real WP-03 LoopClock on the office epoch.
"""
from datetime import date

import psycopg2
import psycopg2.errors
import pytest

from pending import require

clock_mod = require("character.clock", "app/character/clock.py")
weeks = require("character.store.weeks", "app/character/store/weeks.py")

pytestmark = pytest.mark.integration

CAMPAIGN = "ashiorid_office"


@pytest.fixture
def loop_clock():
    return clock_mod.LoopClock(date(2026, 9, 27), "America/New_York")


# T05.4
def test_ensure_week_is_idempotent(pg_conn, loop_clock):
    assert weeks.ensure_week(pg_conn, CAMPAIGN, 3, loop_clock) is True
    pg_conn.commit()
    assert weeks.ensure_week(pg_conn, CAMPAIGN, 3, loop_clock) is False
    pg_conn.commit()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM loop_weeks")
        assert cur.fetchone()[0] == 1
    row = weeks.get_week(pg_conn, CAMPAIGN, 3)
    start, end = loop_clock.week_bounds(3)
    assert row["starts_at"] == start and row["ends_at"] == end
    assert row["status"] == "open"
    assert row["reset_steps"] == {}
    assert weeks.get_week(pg_conn, CAMPAIGN, 4) is None
    weeks.ensure_week(pg_conn, CAMPAIGN, 4, loop_clock)
    assert weeks.latest_week(pg_conn, CAMPAIGN)["week"] == 4
    assert weeks.latest_week(pg_conn, "other_campaign") is None


# T05.5
def test_run_step_locks_row_skips_completed_and_records_completed_at(pg, pg_conn, loop_clock):
    weeks.ensure_week(pg_conn, CAMPAIGN, 1, loop_clock)
    pg_conn.commit()
    calls = []

    def step_fn(conn):
        calls.append("ran")
        other = psycopg2.connect(pg)
        try:
            with other.cursor() as cur:
                with pytest.raises(psycopg2.errors.LockNotAvailable):
                    cur.execute("SELECT week FROM loop_weeks WHERE campaign = %s AND week = 1 "
                                "FOR UPDATE NOWAIT", (CAMPAIGN,))
        finally:
            other.close()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ingest_status (consumer_group) VALUES ('from-step')")

    assert weeks.run_step(pg_conn, CAMPAIGN, 1, "archive", step_fn) is True
    assert weeks.run_step(pg_conn, CAMPAIGN, 1, "archive", step_fn) is False
    assert calls == ["ran"]

    steps = weeks.completed_steps(pg_conn, CAMPAIGN, 1)
    assert set(steps) == {"archive"}
    assert steps["archive"]["completed_at"]
    verify = psycopg2.connect(pg)
    try:
        with verify.cursor() as cur:
            cur.execute("SELECT reset_steps FROM loop_weeks WHERE campaign = %s AND week = 1",
                        (CAMPAIGN,))
            assert "archive" in cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM ingest_status WHERE consumer_group = 'from-step'")
            assert cur.fetchone()[0] == 1
    finally:
        verify.close()

    with pytest.raises(LookupError):
        weeks.run_step(pg_conn, CAMPAIGN, 99, "archive", step_fn)


# T05.6
def test_raising_step_fn_leaves_step_unrecorded(pg, pg_conn, loop_clock):
    weeks.ensure_week(pg_conn, CAMPAIGN, 2, loop_clock)
    pg_conn.commit()

    def crashing(conn):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ingest_status (consumer_group) VALUES ('half-done')")
        raise RuntimeError("step crashed")

    with pytest.raises(RuntimeError, match="step crashed"):
        weeks.run_step(pg_conn, CAMPAIGN, 2, "select_fragments", crashing)

    assert weeks.completed_steps(pg_conn, CAMPAIGN, 2) == {}
    verify = psycopg2.connect(pg)
    try:
        with verify.cursor() as cur:
            cur.execute("SELECT count(*) FROM ingest_status")
            assert cur.fetchone()[0] == 0
    finally:
        verify.close()
    assert weeks.run_step(pg_conn, CAMPAIGN, 2, "select_fragments", lambda conn: None) is True
