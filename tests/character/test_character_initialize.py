"""WP-14 tests for the `initialize` job, app/character/jobs_initialize.py.

Frozen test list (playbook §4 WP-14, items 11-13), adapted for OB-41. Plan §5:
`initialize` (manual) loads the source, runs the generator stages for the
configured characters, seeds `character_agents` (`char:<slug>`), exports the
cast YAML and opens the current week. It runs under the WP-06 job framework
(`jobs.run_job`: advisory lock, jobs row, exit codes 0/1/2/3, --dry-run, --at).

Test corrections (OB-41, .claude/prompts/ashiorid_office_build_plan.md OB-41:
"The book source stages are replaced by scripts/load_office_profiles.py, which
loads profiles/*.yaml into characters / character_backstories /
character_baselines"):
- "Load source, then stages 4-6" becomes the office profile load
  (character.generator.office_profiles.load_profiles over <pack>/profiles and
  <pack>/cast). The pack is `cfg.pack` (campaigns/ashiorid_office) unless the
  job's `--pack DIR` is given; the tests always pass a tmp copy.
- T14.13: agents are `char:<slug>` AND the office seat worker id `tuber_N`.
The database is migrated first (as the WP-06 tests do); `run_job` gets the
config and a connect function directly.
"""
import os
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest

from fakes_generator import (OFFICE_SLUGS, copy_office_pack, count_rows, edit_profile,
                             snapshot_files)
from pending import require, skip_if_pending

jobs_initialize = require("character.jobs_initialize", "app/character/jobs_initialize.py",
                          wp="WP-14")
from character import config, db, jobs  # noqa: E402  (promoted in WP-03, WP-04, WP-06)
from character.store import characters  # noqa: E402

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN = REPO_ROOT / "services" / "character-updater" / "main.py"
AT = "2026-09-29T12:00:00-04:00"        # Tuesday of loop week 1 (epoch Sunday 2026-09-27)
TABLES = ("characters", "character_baselines", "character_backstories", "character_agents",
          "loop_weeks", "character_jobs")


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


@pytest.fixture
def pack(tmp_path):
    return copy_office_pack(tmp_path)[0]


def _counts(dsn):
    conn = psycopg2.connect(dsn)
    try:
        return {table: count_rows(conn, table) for table in TABLES}
    finally:
        conn.close()


def _run(cfg, connect, pack, *extra):
    job = jobs_initialize.InitializeJob()
    assert job.name == "initialize"
    return jobs.run_job(job, ["--pack", str(pack), "--at", AT, *extra], cfg=cfg, connect=connect)


# T14.11
def test_initialize_dry_run_writes_nothing(cfg, connect, dsn, pack, capsys):
    before = snapshot_files(pack / "cast")
    assert _run(cfg, connect, pack, "--dry-run") == 0
    assert all(count == 0 for count in _counts(dsn).values())      # not even the jobs row
    assert snapshot_files(pack / "cast") == before
    out = capsys.readouterr().out
    assert "dry run" in out.lower()
    assert all(slug in out for slug in OFFICE_SLUGS)


# T14.12
def test_initialize_twice_second_run_is_a_no_op_exit_2(cfg, connect, dsn, pack):
    cast_before = snapshot_files(pack / "cast")
    assert _run(cfg, connect, pack) == 0
    counts = _counts(dsn)
    assert counts == {"characters": 8, "character_baselines": 8, "character_backstories": 16,
                      "character_agents": 16, "loop_weeks": 1, "character_jobs": 1}
    assert snapshot_files(pack / "cast") == cast_before       # export round trip is a no-op
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT campaign, week, status FROM loop_weeks")
            assert cur.fetchall() == [(cfg.campaign, 1, "open")]    # the current week is open
            assert all(characters.active_baseline(conn, s)["version"] == 1 for s in OFFICE_SLUGS)
    finally:
        conn.close()

    assert _run(cfg, connect, pack) == 2
    after = _counts(dsn)
    assert {k: v for k, v in after.items() if k != "character_jobs"} == \
        {k: v for k, v in counts.items() if k != "character_jobs"}
    rows = _job_statuses(dsn)
    assert rows == [("initialize", "completed"), ("initialize", "skipped")]


def _job_statuses(dsn):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT job, status FROM character_jobs ORDER BY created_at")
            return cur.fetchall()
    finally:
        conn.close()


# T14.12 (a changed profile makes the next run do work again, for that character only)
def test_initialize_after_a_profile_change_exits_0(cfg, connect, dsn, pack):
    assert _run(cfg, connect, pack) == 0
    edit_profile(pack / "profiles" / "tester.yaml", lambda d: d["identity"].update(age=29))
    assert _run(cfg, connect, pack) == 0
    assert _counts(dsn)["character_baselines"] == 9


# T14.13 (adapted, OB-41)
def test_initialize_seeds_character_agents_char_slug_and_seat(cfg, connect, dsn, pack):
    assert _run(cfg, connect, pack) == 0
    conn = psycopg2.connect(dsn)
    try:
        agents = characters.agents_map(conn)
        for slug in OFFICE_SLUGS:
            character_id = characters.get_character(conn, slug)["id"]
            assert agents[f"char:{slug}"] == character_id
        assert sorted(a for a in agents if a.startswith("tuber_")) == \
            [f"tuber_{n}" for n in range(8)]
    finally:
        conn.close()


# plan §5 exit 3: no profiles directory in the pack is a precondition failure
def test_initialize_without_profiles_is_precondition_exit_3(cfg, connect, dsn, tmp_path):
    empty_pack = tmp_path / "empty_pack"
    (empty_pack / "cast").mkdir(parents=True)
    assert _run(cfg, connect, empty_pack) == 3
    assert _job_statuses(dsn) == [("initialize", "skipped")]
    assert _counts(dsn)["characters"] == 0


# plan §5: an invalid profile fails the job (exit 1) and writes no character rows
def test_initialize_with_an_invalid_profile_fails_exit_1(cfg, connect, dsn, pack):
    edit_profile(pack / "profiles" / "ceo.yaml",
                 lambda d: d["backstory_nodes"][0].update(name="Not A Node"))
    assert _run(cfg, connect, pack) == 1
    assert _job_statuses(dsn) == [("initialize", "failed")]
    assert _counts(dsn)["characters"] == 0


# plan §5 / D-13: initialize is a subcommand of main.py
def test_main_registers_initialize():
    skip_if_pending("services/character-updater/main.py", wp="WP-06")
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    listing = subprocess.run([sys.executable, str(MAIN), "--help"], env=env,
                             capture_output=True, text=True, timeout=120)
    assert listing.returncode == 0, listing.stderr
    assert "initialize" in listing.stdout and "status" in listing.stdout
    detail = subprocess.run([sys.executable, str(MAIN), "initialize", "--help"], env=env,
                            capture_output=True, text=True, timeout=120)
    assert detail.returncode == 0, detail.stderr
    assert "--pack" in detail.stdout and "--dry-run" in detail.stdout
