"""WP-16 tests for app/character/jobs_ingest.py: the `ingest` job's backfill path.

Frozen test list (playbook §4 WP-16, item 12): `ingest --backfill-from-messages
--since <ts>` reads the virtualtubers `messages` table (read-only; D-11, plan
§3.3 "the recovery path is a backfill from messages") through a cursor and
routes every row exactly like the live consumer (the same `route()`), so a
backfill after a Kafka retention gap gives the same experience rows.
The live Kafka loop itself is covered by test_character_ingest.py; here the
source database is a fake cursor and character_profile is a migrated pgserver
database. The job runs through character.jobs.run_job (WP-06).

OB-41 adaptations (test corrections, cited: .claude/prompts/ashiorid_office_build_plan.md
OB-41): office slugs and `tuber_N` seat senders; campaign ashiorid_office.
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fakes import seed_character
from fakes_runtime import (FakeSourceConn, connect_to, count, message_row, migrated_dsn,  # noqa: F401
                           say, scene, seed_agents, thinking)
from pending import require

jobs_ingest = require("character.jobs_ingest", "app/character/jobs_ingest.py", wp="WP-16")
import psycopg2  # noqa: E402
from character import config, ingest, jobs  # noqa: E402
from character.clock import LoopClock  # noqa: E402

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SINCE = "2026-09-28T00:00:00+00:00"


@pytest.fixture
def cfg():
    return config.load(env={})


@pytest.fixture
def office(migrated_dsn):
    """engineer + tester, with their char: and seat agent ids."""
    conn = psycopg2.connect(migrated_dsn)
    try:
        engineer = seed_character(conn, "engineer")
        tester = seed_character(conn, "tester")
        seed_agents(conn, {"char:engineer": engineer, "tuber_3": engineer,
                           "char:tester": tester, "tuber_4": tester})
        conn.commit()
    finally:
        conn.close()
    return {"engineer": engineer, "tester": tester}


def _messages():
    return [
        thinking("tuber_3", "The fixture leaks state between tests.", "2026-09-29T13:00:00Z"),
        say("tester", "Suite is red on main.", "2026-09-29T13:05:00Z",
            present=["tester", "engineer", "office_clock"], from_="tuber_4"),
        scene("The Glass Box goes quiet.", "2026-09-29T13:10:00Z", present=["engineer"]),
        thinking("coder", "an unmapped dev-team worker", "2026-09-29T13:11:00Z"),
    ]


def _rows(dsn):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT message_id, character_id, visibility, loop_week, loop_day "
                        "FROM experience_events ORDER BY message_id, character_id")
            return cur.fetchall()
    finally:
        conn.close()


# T16.12
def test_backfill_reads_a_cursor_and_routes_the_same_way(cfg, migrated_dsn, office):
    messages = _messages()
    source = FakeSourceConn([message_row(m) for m in messages])
    job = jobs_ingest.IngestJob(source_connect=lambda cfg: source)
    assert job.name == "ingest"
    code = jobs.run_job(job, ["--backfill-from-messages", "--since", SINCE,
                              "--at", "2026-09-30T12:00:00Z"],
                        cfg=cfg, connect=connect_to(migrated_dsn))
    assert code == jobs.EXIT_OK

    # the same rows route() gives for the same messages
    conn = psycopg2.connect(migrated_dsn)
    try:
        agents, slugs = ingest.load_maps(conn)
    finally:
        conn.close()
    clock = LoopClock(cfg.loop.epoch, cfg.timezone)
    expected = sorted((row["message_id"], row["character_id"], row["visibility"],
                       row["loop_week"], row["loop_day"])
                      for msg in messages for row in ingest.route(msg, agents, slugs, clock))
    assert _rows(migrated_dsn) == expected
    assert len(expected) == 4        # thought, say x2 (tester, engineer), scene; coder unmapped

    # read-only source query, parameterised on --since and the ingest allowlist
    queries = [(q, p) for q, p in source.cur.executed if "to_regclass" not in str(q)]
    assert queries, "backfill never queried messages"
    since = datetime(2026, 9, 28, tzinfo=timezone.utc)
    assert all(p == (since, list(cfg.ingest.types)) for _, p in queries)
    assert source.closed is True

    # a second backfill of the same window inserts nothing new
    again = FakeSourceConn([message_row(m) for m in messages])
    assert jobs.run_job(jobs_ingest.IngestJob(source_connect=lambda cfg: again),
                        ["--backfill-from-messages", "--since", SINCE],
                        cfg=cfg, connect=connect_to(migrated_dsn)) == jobs.EXIT_OK
    assert len(_rows(migrated_dsn)) == 4


# T16.12 (--dry-run reads and routes but writes nothing, not even the jobs row: plan §5)
def test_backfill_dry_run_writes_nothing(cfg, migrated_dsn, office, capsys):
    source = FakeSourceConn([message_row(m) for m in _messages()])
    code = jobs.run_job(jobs_ingest.IngestJob(source_connect=lambda cfg: source),
                        ["--backfill-from-messages", "--since", SINCE, "--dry-run"],
                        cfg=cfg, connect=connect_to(migrated_dsn))
    assert code == jobs.EXIT_OK
    assert _rows(migrated_dsn) == []
    assert count(migrated_dsn, "SELECT count(*) FROM character_jobs") == 0
    out = capsys.readouterr().out
    assert "4 messages, 4 rows" in out and "dry run" in out   # what it would write


# T16.12 (--backfill-from-messages without --since is a precondition failure, exit 3)
def test_backfill_without_since_exits_3(cfg, migrated_dsn, office):
    source = FakeSourceConn([])
    code = jobs.run_job(jobs_ingest.IngestJob(source_connect=lambda cfg: source),
                        ["--backfill-from-messages"], cfg=cfg, connect=connect_to(migrated_dsn))
    assert code == jobs.EXIT_PRECONDITION
    assert source.cur.executed == []


# registration (playbook WP-06: "main.py registers every job")
def test_main_registers_the_ingest_job():
    main = REPO_ROOT / "services" / "character-updater" / "main.py"
    result = subprocess.run([sys.executable, str(main), "ingest", "--help"], capture_output=True,
                            text=True, timeout=120, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert "--backfill-from-messages" in result.stdout and "--since" in result.stdout
