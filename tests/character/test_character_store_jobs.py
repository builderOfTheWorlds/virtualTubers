"""WP-05 integration tests for app/character/store/jobs.py.

Frozen test list (playbook §4 WP-05, items 18-19): character_jobs (every CLI
job run writes one, plan §5 / D-14) and character_artifacts.
"""
import psycopg2
import pytest

from pending import require

jobs = require("character.store.jobs", "app/character/store/jobs.py")

pytestmark = pytest.mark.integration


def _row(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT job, status, dry_run, params, result, error, started_at, "
                    "finished_at FROM character_jobs WHERE id = %s", (job_id,))
        names = [column[0] for column in cur.description]
        row = cur.fetchone()
    return dict(zip(names, row)) if row else None


# T05.18
def test_start_finish_and_fail_job(pg_conn):
    done = jobs.start_job(pg_conn, "status", params={"campaign": "ashiorid_office"})
    pg_conn.commit()
    row = _row(pg_conn, done)
    assert row["job"] == "status" and row["status"] == "running"
    assert row["params"] == {"campaign": "ashiorid_office"}
    assert row["dry_run"] is False
    assert row["started_at"] is not None and row["finished_at"] is None

    assert jobs.finish_job(pg_conn, done, result={"week": 1}) is True
    pg_conn.commit()
    row = _row(pg_conn, done)
    assert row["status"] == "completed" and row["result"] == {"week": 1}
    assert row["finished_at"] is not None

    skipped = jobs.start_job(pg_conn, "weekly-reset")
    assert jobs.finish_job(pg_conn, skipped, status="skipped", result={"reason": "locked"}) is True
    assert _row(pg_conn, skipped)["status"] == "skipped"

    broken = jobs.start_job(pg_conn, "daily-maintenance", dry_run=False)
    assert jobs.fail_job(pg_conn, broken, "RuntimeError: summary LLM timed out") is True
    pg_conn.commit()
    row = _row(pg_conn, broken)
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError: summary LLM timed out"
    assert row["finished_at"] is not None

    # terminal rows are never rewritten: a late finish cannot hide the failure
    assert jobs.finish_job(pg_conn, broken, result={"ok": True}) is False
    assert jobs.fail_job(pg_conn, done, "late error") is False
    pg_conn.commit()
    assert _row(pg_conn, broken)["status"] == "failed"
    assert _row(pg_conn, done)["error"] is None

    with pytest.raises(ValueError):
        jobs.finish_job(pg_conn, jobs.start_job(pg_conn, "status"), status="running")
    pg_conn.rollback()

    artifact = jobs.add_artifact(pg_conn, kind="daily_summary_raw", content={"raw": "..."},
                                 job_id=done, character_id="ceo-id")
    pg_conn.commit()
    assert isinstance(artifact, int)
    with pytest.raises(psycopg2.Error):
        jobs.add_artifact(pg_conn, kind="orphan", content={}, job_id="no-such-job")
    pg_conn.rollback()


# T05.19
def test_list_recent_returns_newest_first(pg_conn):
    ids = []
    for name in ("status", "daily-maintenance", "weekly-reset"):
        ids.append(jobs.start_job(pg_conn, name))
        pg_conn.commit()  # separate transactions, so created_at differs
    recent = jobs.list_recent(pg_conn, 2)
    assert [row["id"] for row in recent] == [ids[2], ids[1]]
    assert recent[0]["job"] == "weekly-reset" and recent[0]["status"] == "running"
    assert [row["id"] for row in jobs.list_recent(pg_conn, 10)] == list(reversed(ids))
    assert [row["id"] for row in jobs.list_recent(pg_conn, 10, job="status")] == [ids[0]]
    assert jobs.list_recent(pg_conn, 0) == []
