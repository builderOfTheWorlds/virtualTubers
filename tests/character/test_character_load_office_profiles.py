"""WP-10o tests for scripts/load_office_profiles.py, the thin loader CLI.

OB-41 test list (see test_character_office_profiles.py): the office build plan
OB-41 names `scripts/load_office_profiles.py` as the replacement for the book
source stages. It parses flags, loads config/character.yaml (DB settings from
CHARACTER_DB_*), validates every profile BEFORE connecting, then calls
character.generator.office_profiles.load_profiles and commits. Exit codes follow
plan §5: 0 loaded something, 1 failed (invalid profile, DB error), 2 nothing
to do (every profile unchanged), 3 precondition (the database is not migrated).
`--dry-run` writes nothing (playbook §2.3). The script inserts app/ into
sys.path itself (D-23), so it runs from any cwd. These tests run it as a
subprocess against the `pg` fixture's database.
"""
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest

from fakes_generator import (OFFICE_SLUGS, copy_office_pack, count_rows, db_env,
                             edit_profile)
from pending import skip_if_pending

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "load_office_profiles.py"

skip_if_pending("scripts/load_office_profiles.py", wp="WP-10o")
if not SCRIPT.is_file():  # only reachable with CHARACTER_V4_STRICT=1
    raise ImportError(f"scripts/load_office_profiles.py not found: {SCRIPT}")

pytestmark = pytest.mark.integration

TABLES = ("characters", "character_baselines", "character_backstories", "character_agents")


def _run(args, env, cwd):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=180)


def _counts(dsn):
    conn = psycopg2.connect(dsn)
    try:
        return {table: count_rows(conn, table) for table in TABLES}
    finally:
        conn.close()


@pytest.fixture
def migrated(pg, pg_conn):
    """The `pg` DSN, with app/character/sql/*.sql applied by the pg_conn fixture."""
    return pg


@pytest.fixture
def office(tmp_path):
    _, profiles, cast = copy_office_pack(tmp_path)
    return ["--profiles-dir", str(profiles), "--cast-dir", str(cast)], profiles


# T10o.1 + T10o.2 (CLI): first run loads 8 and exits 0; the second exits 2
def test_cli_loads_eight_then_second_run_is_nothing_to_do(migrated, office, tmp_path):
    flags, _ = office
    env = db_env(migrated)
    first = _run(flags, env, tmp_path)
    assert first.returncode == 0, first.stderr
    for slug in OFFICE_SLUGS:
        assert slug in first.stdout
    assert _counts(migrated) == {"characters": 8, "character_baselines": 8,
                                 "character_backstories": 16, "character_agents": 16}
    second = _run(flags, env, REPO_ROOT)
    assert second.returncode == 2, second.stderr
    assert _counts(migrated)["character_baselines"] == 8


# T10o.3 (CLI): a changed profile loads only that character, exit 0
def test_cli_changed_profile_exits_0_and_adds_one_version(migrated, office, tmp_path):
    flags, profiles = office
    env = db_env(migrated)
    assert _run(flags, env, tmp_path).returncode == 0
    edit_profile(profiles / "marketing.yaml", lambda d: d["identity"].update(age=30))
    result = _run(flags, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "marketing" in result.stdout
    assert _counts(migrated)["character_baselines"] == 9


# T10o.8 (CLI): --dry-run writes nothing and exits 0
def test_cli_dry_run_writes_nothing(migrated, office, tmp_path):
    flags, _ = office
    result = _run([*flags, "--dry-run"], db_env(migrated), tmp_path)
    assert result.returncode == 0, result.stderr
    assert "dry run" in result.stdout.lower()
    for slug in OFFICE_SLUGS:
        assert slug in result.stdout
    assert all(count == 0 for count in _counts(migrated).values())


# T10o.5 (CLI): an invalid profile exits 1, names the file, and never connects
def test_cli_invalid_profile_exits_1_before_connecting(office, tmp_path):
    flags, profiles = office
    edit_profile(profiles / "ceo.yaml", lambda d: d["backstory_nodes"][0].update(name="Bad Name"))
    env = db_env("host=/nonexistent-socket-dir port=1 dbname=none user=nobody")
    result = _run(flags, env, tmp_path)
    assert result.returncode == 1
    assert "ceo.yaml" in result.stderr and "backstory_nodes[0].name" in result.stderr
    assert "connect" not in result.stderr.lower()             # validation came first


# T10o.10 (CLI): an unmigrated database is a precondition failure (exit 3)
def test_cli_unmigrated_database_exits_3(pg, office, tmp_path):
    flags, _ = office
    result = _run(flags, db_env(pg), tmp_path)
    assert result.returncode == 3, result.stderr
    assert "migrat" in (result.stdout + result.stderr).lower()


# D-23 (CLI): --help works from any cwd without a database
def test_cli_help_works_from_any_cwd(tmp_path):
    for cwd in (tmp_path, REPO_ROOT / "scripts"):
        result = _run(["--help"], db_env("host=/nowhere dbname=x user=y"), cwd)
        assert result.returncode == 0, result.stderr
        for flag in ("--profiles-dir", "--cast-dir", "--dry-run", "--no-activate", "--config"):
            assert flag in result.stdout
