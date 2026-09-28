"""WP-06 tests for app/character/jobs.py: the job framework and the `status` job.

Frozen test list (playbook §4 WP-06, items 2-6; items 1 and 7 are about
services/character-updater/main.py and live in test_character_updater_main.py).
Plan §5: every job runs under an advisory lock keyed on (job, campaign),
writes a character_jobs row (except under --dry-run), and exits 0 done,
1 failed, 2 nothing to do / locked, 3 precondition not met. `--at` replaces
"now". Integration tests use a migrated fresh database from the `pg` fixture;
`run_job` is given the config and a connect function directly.
"""
import logging
from datetime import date, datetime, timedelta, timezone

import psycopg2
import pytest

from pending import require

jobs = require("character.jobs", "app/character/jobs.py")
from character import config, db  # noqa: E402  (present once jobs.py is)

pytestmark = pytest.mark.integration


class ProbeJob:
    """Records every context it is run with; optionally raises."""

    name = "probe"

    def __init__(self, exc=None):
        self.contexts = []
        self.exc = exc

    def run(self, ctx):
        self.contexts.append(ctx)
        if self.exc is not None:
            raise self.exc
        return jobs.JobResult("done", {"probed": True})


@pytest.fixture
def cfg():
    return config.load(env={})


@pytest.fixture
def dsn(pg):
    conn = psycopg2.connect(pg)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    return pg


@pytest.fixture
def connect(dsn):
    def _connect(cfg, role="main"):
        conn = psycopg2.connect(dsn)
        conn.autocommit = False
        return conn
    return _connect


def _job_rows(dsn):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT job, status, dry_run, error FROM character_jobs ORDER BY created_at")
            return cur.fetchall()
    finally:
        conn.close()


# T06.2
def test_at_sets_ctx_now_and_naive_at_is_rejected(cfg, connect):
    job = ProbeJob()
    assert jobs.run_job(job, ["--at", "2026-10-05T12:00:00-04:00"], cfg=cfg, connect=connect) == 0
    ctx = job.contexts[0]
    assert ctx.now == datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)
    assert ctx.now.utcoffset() is not None
    assert ctx.cfg is cfg
    assert ctx.dry_run is False
    assert ctx.clock.position(ctx.now) == (2, date(2026, 10, 5))  # office epoch 2026-09-27
    assert ctx.conn is not None and ctx.log is not None and ctx.args is not None

    assert jobs.run_job(job, [], cfg=cfg, connect=connect) == 0
    assert abs(job.contexts[1].now - datetime.now(timezone.utc)) < timedelta(minutes=5)

    with pytest.raises(SystemExit) as caught:
        jobs.run_job(job, ["--at", "2026-10-05T12:00:00"], cfg=cfg, connect=connect)
    assert caught.value.code == 2
    assert len(job.contexts) == 2


# T06.3
def test_dry_run_writes_no_jobs_row(cfg, connect, dsn):
    job = ProbeJob()
    assert jobs.run_job(job, ["--dry-run", "--at", "2026-10-05T12:00:00Z"],
                        cfg=cfg, connect=connect) == 0
    assert job.contexts[0].dry_run is True
    assert _job_rows(dsn) == []
    assert jobs.run_job(job, [], cfg=cfg, connect=connect) == 0
    assert _job_rows(dsn) == [("probe", "completed", False, None)]


# T06.4
def test_lock_contention_exits_2(cfg, connect, dsn):
    holder = psycopg2.connect(dsn)
    try:
        assert db.advisory_lock(holder, jobs.lock_key("probe", cfg.campaign)) is True
        job = ProbeJob()
        assert jobs.run_job(job, [], cfg=cfg, connect=connect) == jobs.EXIT_NOTHING == 2
        assert job.contexts == []
        assert [row[:2] for row in _job_rows(dsn)] == [("probe", "skipped")]
        # another campaign is another lock
        assert jobs.run_job(job, ["--campaign", "other_campaign"], cfg=cfg, connect=connect) == 0
    finally:
        holder.close()
    job = ProbeJob()
    assert jobs.run_job(job, [], cfg=cfg, connect=connect) == 0
    assert len(job.contexts) == 1


# T06.5
def test_raising_job_exits_1_writes_failed_row_and_logs_error(cfg, connect, dsn, caplog):
    caplog.set_level(logging.DEBUG)
    job = ProbeJob(exc=RuntimeError("summary exploded: ceo day 2026-10-04"))
    assert jobs.run_job(job, [], cfg=cfg, connect=connect) == jobs.EXIT_FAILED == 1
    rows = _job_rows(dsn)
    assert [row[:2] for row in rows] == [("probe", "failed")]
    assert "summary exploded: ceo day 2026-10-04" in rows[0][3]
    assert any(record.levelno >= logging.ERROR and record.name.startswith("character")
               for record in caplog.records)


# T06.6
def test_status_on_migrated_empty_db_prints_no_weeks_and_exits_0(cfg, connect, dsn, capsys):
    status = jobs.StatusJob()
    assert status.name == "status"
    assert jobs.run_job(status, ["--at", "2026-10-05T12:00:00-04:00"],
                        cfg=cfg, connect=connect) == jobs.EXIT_OK == 0
    out = capsys.readouterr().out
    assert "no weeks" in out.lower() or "week 0" in out.lower()
    assert _job_rows(dsn) == [("status", "completed", False, None)]
