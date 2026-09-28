"""WP-21 tests: the `revert` job (item 9) and the registration of `testctl` and
`revert` in services/character-updater/main.py (the WP's second target).

Plan §5 job table: `revert --character X --to-version N` "moves
active_baseline_version, then publishes character_refresh". It is a separate
job (not a testctl command), so its jobs row is 'revert' and its lock is
('revert', campaign). The refresh payload follows plan §3.2:
{"campaign", "week", "characters": [X], "reason": "revert"}, sent by
"character-updater" to "broadcast".
"""
import os
import subprocess
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

from fakes import seed_character
from fakes_e2e import FakeProducer, count
from pending import require, skip_if_pending

testctl = require("character.testctl", "app/character/testctl.py", wp="WP-21")
skip_if_pending("services/character-updater/main.py", wp="WP-06")
from character import config, db, jobs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN = REPO_ROOT / "services" / "character-updater" / "main.py"


@pytest.fixture
def cfg():
    return config.load(env={})


@pytest.fixture
def dsn(pg):
    conn = psycopg2.connect(pg)
    try:
        db.migrate(conn)
        cid = seed_character(conn, "engineer")
        seed_character(conn, "tester")
        with conn.cursor() as cur:
            for version in (1, 2):
                cur.execute("INSERT INTO character_baselines (character_id, version, profile, "
                            "baseline_book, baseline_chapter) VALUES (%s, %s, %s, 0, 0)",
                            (cid, version, psycopg2.extras.Json({"v": version})))
            cur.execute("UPDATE characters SET active_baseline_version = 2 WHERE id = %s", (cid,))
        conn.commit()
    finally:
        conn.close()
    return pg


@pytest.fixture
def connect(dsn):
    return lambda cfg, role="main": psycopg2.connect(dsn)


def _pointer(dsn):
    return count(dsn, "SELECT active_baseline_version FROM characters WHERE slug = 'engineer'")


def _revert(cfg, connect, producer, *argv):
    return jobs.run_job(testctl.RevertJob(producer=producer), list(argv), cfg=cfg, connect=connect)


# T21.9
@pytest.mark.integration
def test_revert_moves_the_pointer_and_publishes_a_refresh(cfg, connect, dsn):
    producer = FakeProducer()
    at = "2026-10-07T12:00:00-04:00"
    assert _revert(cfg, connect, producer, "--character", "engineer", "--to-version", "1",
                   "--dry-run", "--at", at) == 0
    assert _pointer(dsn) == 2 and producer.sent == []
    assert count(dsn, "SELECT count(*) FROM character_jobs") == 0

    assert _revert(cfg, connect, producer, "--character", "engineer", "--to-version", "1",
                   "--at", at) == 0
    assert _pointer(dsn) == 1
    assert len(producer.sent) == 1
    message = producer.sent[0]
    assert message["type"] == "character_refresh"
    assert message["from"] == "character-updater" and message["to"] == "broadcast"
    assert message["payload"]["campaign"] == "ashiorid_office"
    assert message["payload"]["characters"] == ["engineer"]
    assert message["payload"]["reason"] == "revert"
    assert message["payload"]["week"] == 2
    assert count(dsn, "SELECT job || ':' || status FROM character_jobs") == "revert:completed"

    assert _revert(cfg, connect, producer, "--character", "engineer", "--to-version", "7",
                   "--at", at) == 1                                  # no such version
    assert _revert(cfg, connect, producer, "--to-version", "1", "--at", at) == 1   # no --character
    assert _pointer(dsn) == 1 and len(producer.sent) == 1


def _main(*args):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(MAIN), *args], cwd=REPO_ROOT.parent, env=env,
                          capture_output=True, text=True, timeout=120)


def test_main_registers_testctl_and_revert():
    top = _main("--help")
    assert top.returncode == 0, top.stderr
    assert "testctl" in top.stdout and "revert" in top.stdout and "status" in top.stdout
    group = _main("testctl", "--help")
    assert group.returncode == 0, group.stderr
    for word in ("reset-undo", "fragment", "week", "seed", "clock", "refresh"):
        assert word in group.stdout
    assert _main("testctl").returncode == 2
    bad = _main("testctl", "explode")
    assert bad.returncode == 2 and "explode" in bad.stderr
    revert = _main("revert", "--help")
    assert revert.returncode == 0, revert.stderr
    assert "--to-version" in revert.stdout and "--dry-run" in revert.stdout
