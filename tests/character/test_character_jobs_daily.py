"""WP-19 tests for app/character/jobs_daily.py: the `daily-maintenance` job.

Frozen test list (playbook §4 WP-19, items 4, 5 and 7; items 1-3 and 6 are in
test_character_summaries.py). Plan §5, daily-maintenance at 00:00 NY:
  1. Summaries for yesterday (the NY date) for every active character. Wait up
     to ingest.catchup_wait_s (600) for ingest to pass midnight, then proceed
     and mark the summary `partial`.
  2. Compaction of `messages`, only if ingest lag is under
     ingest.max_lag_messages, else skipped with exit 3; steps already done stay
     done.
The job runs through character.jobs.run_job (WP-06) against a migrated
pgserver database; the LLM, the wait clock and compaction are injected fakes
(compaction itself is tested in test_character_compaction.py).

OB-41 adaptations (test corrections, cited: .claude/prompts/ashiorid_office_build_plan.md
OB-41): office characters (engineer, tester) and the office epoch; item 7's
`--character harry` becomes `--character engineer`.
"""
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest

from fakes import FakeClock, seed_character
from fakes_runtime import (FakeConn, FakeLLM, connect_to, count, migrated_dsn,  # noqa: F401
                           seed_event, seed_ingest_status)
from pending import require

jobs_daily = require("character.jobs_daily", "app/character/jobs_daily.py", wp="WP-19")
from character import config, jobs  # noqa: E402

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = "2026-09-30T00:00:30-04:00"                  # Wednesday 00:00:30 NY -> summarise Tuesday
DAY = date(2026, 9, 29)
MIDNIGHT = datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc)
REPLY = {"summary": "I watched the build go red and then green.",
         "nodes": [{"name": "knows-build-went-green", "kind": "fact",
                    "statement": "I know the build went green."}]}


@pytest.fixture
def cfg():
    return config.load(env={})


@pytest.fixture
def office(migrated_dsn):
    conn = psycopg2.connect(migrated_dsn)
    try:
        ids = {slug: seed_character(conn, slug) for slug in ("engineer", "tester")}
        for slug, character_id in ids.items():
            seed_event(conn, character_id, text=f"{slug} saw the build go red",
                       ts=datetime(2026, 9, 29, 15, tzinfo=timezone.utc), loop_week=1, loop_day=DAY)
        conn.commit()
    finally:
        conn.close()
    return ids


class Compaction:
    """Stands in for character.compaction.run / ingest_lag and journals the order."""

    def __init__(self, journal, status="done", lag=0):
        self.journal, self.status, self.lag, self.calls = journal, status, lag, []

    def run(self, conn, ccfg, *, now, ingest_lag, max_lag, dry_run=False):
        self.journal.append("compaction")
        self.calls.append({"now": now, "ingest_lag": ingest_lag, "max_lag": max_lag,
                           "dry_run": dry_run})
        reason = "ingest lag 5000 > 1000" if self.status == "precondition" else ""
        return {"status": self.status, "reason": reason, "dry_run": dry_run, "noisy_deleted": 0,
                "rows_moved": 0, "partitions_moved": [], "default_rows_moved": 0}

    def ingest_lag(self, conn, last_message_ts, types):
        return self.lag


@pytest.fixture
def journal():
    return []


@pytest.fixture
def fake_compaction(monkeypatch, journal):
    fake = Compaction(journal)
    monkeypatch.setattr(jobs_daily.compaction, "run", fake.run)
    monkeypatch.setattr(jobs_daily.compaction, "ingest_lag", fake.ingest_lag)
    return fake


def _job(journal, llm=None, clock=None):
    llm = llm or FakeLLM(REPLY)

    def complete(system, user, shape):
        journal.append("summary")
        return llm(system, user, shape)

    clock = clock or FakeClock()
    job = jobs_daily.DailyMaintenanceJob(complete=complete, messages_connect=lambda cfg: FakeConn(),
                                         check_name=lambda name: [], monotonic=clock,
                                         sleep=clock.sleep, poll_s=15)
    return job, llm, clock


def _summaries(dsn):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT c.slug, s.loop_day, s.summary->>'partial' FROM daily_summaries s "
                        "JOIN characters c ON c.id = s.character_id ORDER BY c.slug")
            return cur.fetchall()
    finally:
        conn.close()


# T19.4
def test_waits_up_to_catchup_wait_then_marks_summaries_partial(cfg, migrated_dsn, office,
                                                               fake_compaction, journal):
    job, llm, clock = _job(journal)
    assert job.name == "daily-maintenance"
    code = jobs.run_job(job, ["--at", AT], cfg=cfg, connect=connect_to(migrated_dsn))
    assert code == jobs.EXIT_OK
    assert cfg.ingest.catchup_wait_s == 600
    assert sum(clock.sleeps) == 600 and all(s <= 15 for s in clock.sleeps)
    assert _summaries(migrated_dsn) == [("engineer", DAY, "true"), ("tester", DAY, "true")]


# T19.4 (ingest already past midnight: no wait, not partial)
def test_no_wait_when_ingest_has_passed_midnight(cfg, migrated_dsn, office, fake_compaction, journal):
    conn = psycopg2.connect(migrated_dsn)
    try:
        seed_ingest_status(conn, MIDNIGHT + timedelta(seconds=5))
        conn.commit()
    finally:
        conn.close()
    job, llm, clock = _job(journal)
    assert jobs.run_job(job, ["--at", AT], cfg=cfg, connect=connect_to(migrated_dsn)) == 0
    assert clock.sleeps == []
    assert _summaries(migrated_dsn) == [("engineer", DAY, "false"), ("tester", DAY, "false")]


# T19.5
def test_summaries_then_compaction_and_precondition_keeps_summaries_exit_3(
        cfg, migrated_dsn, office, fake_compaction, journal):
    fake_compaction.status = "precondition"
    fake_compaction.lag = 5000
    seed = psycopg2.connect(migrated_dsn)
    try:
        seed_ingest_status(seed, MIDNIGHT + timedelta(seconds=5))
        seed.commit()
    finally:
        seed.close()
    job, llm, clock = _job(journal)
    code = jobs.run_job(job, ["--at", AT], cfg=cfg, connect=connect_to(migrated_dsn))
    assert code == jobs.EXIT_PRECONDITION == 3
    assert journal == ["summary", "summary", "compaction"]
    assert fake_compaction.calls[0]["ingest_lag"] == 5000
    assert fake_compaction.calls[0]["max_lag"] == cfg.ingest.max_lag_messages
    assert fake_compaction.calls[0]["now"] == datetime.fromisoformat(AT)
    assert [row[0] for row in _summaries(migrated_dsn)] == ["engineer", "tester"]  # committed
    # the re-run: summaries are done (no LLM call), compaction is tried again
    journal.clear()
    fake_compaction.status = "done"
    job2, llm2, _ = _job(journal, llm=FakeLLM())
    assert jobs.run_job(job2, ["--at", AT], cfg=cfg, connect=connect_to(migrated_dsn)) == 0
    assert journal == ["compaction"] and llm2.calls == []


# T19.7
def test_character_flag_limits_the_run_to_that_character(cfg, migrated_dsn, office,
                                                         fake_compaction, journal):
    job, llm, clock = _job(journal)
    assert jobs.run_job(job, ["--at", AT, "--character", "engineer"],
                        cfg=cfg, connect=connect_to(migrated_dsn)) == 0
    assert [row[0] for row in _summaries(migrated_dsn)] == ["engineer"]
    assert len(llm.calls) == 1 and "engineer saw the build" in llm.calls[0]["user"]
    assert "tester saw" not in llm.calls[0]["user"]
    job, llm, clock = _job(journal)
    assert jobs.run_job(job, ["--at", AT, "--character", "nobody"],
                        cfg=cfg, connect=connect_to(migrated_dsn)) == jobs.EXIT_PRECONDITION


# playbook §2.3: every job WP has a --dry-run test
def test_dry_run_writes_nothing_and_calls_no_llm(cfg, migrated_dsn, office, fake_compaction, journal):
    job, llm, clock = _job(journal, llm=FakeLLM())
    assert jobs.run_job(job, ["--at", AT, "--dry-run"], cfg=cfg,
                        connect=connect_to(migrated_dsn)) == 0
    assert _summaries(migrated_dsn) == []
    assert llm.calls == [] and clock.sleeps == []
    assert fake_compaction.calls and fake_compaction.calls[0]["dry_run"] is True
    assert count(migrated_dsn, "SELECT count(*) FROM week_knowledge_nodes") == 0
    assert count(migrated_dsn, "SELECT count(*) FROM character_jobs") == 0


# registration (playbook WP-06: "main.py registers every job")
def test_main_registers_daily_maintenance():
    main = REPO_ROOT / "services" / "character-updater" / "main.py"
    result = subprocess.run([sys.executable, str(main), "daily-maintenance", "--help"],
                            capture_output=True, text=True, timeout=120, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert "--character" in result.stdout and "--dry-run" in result.stdout
