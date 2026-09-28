"""Database fixtures for the character v4 integration tests (playbook WP-03, D-06).

`pg` yields a psycopg2 DSN string for a FRESH, EMPTY database per test:

1. `CHARACTER_TEST_DSN` if set (e.g. the gx10 test instance, plan §6). The
   role behind it needs CREATEDB; per-test databases are created on that
   server and dropped afterwards.
2. else the `pgserver` pip package (Postgres 16 + pgvector) started once per
   session in a pytest tmp dir. It has no aarch64 wheel (D-06).
3. else every test that asks for `pg` is skipped. A skip is not a pass: the
   phase gates need these tests to run somewhere (playbook §2.1). With
   `CHARACTER_TEST_REQUIRE_DB=1` the fixture FAILS instead of skipping; set it
   for harness runs (`runner.py run`), because the harness counts pytest exit
   0 as a pass and an all-skipped run exits 0.

Each test's database is `CREATE DATABASE ... TEMPLATE <session template>`,
where the template is an empty database created once per session from
template0. So a test sees no tables and no extensions until it migrates.

`pg_conn` builds on `pg`: an open connection (autocommit off) with every
`app/character/sql/*.sql` file applied in order, raw, without `character.db`,
so the store tests don't depend on db.migrate(). Skipped while the SQL
directory has no files.

Never point CHARACTER_TEST_DSN at a production server (playbook §1.6).
"""
import logging
import os
import uuid
from pathlib import Path

import pytest

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = REPO_ROOT / "app" / "character" / "sql"
TEST_DSN_ENV = "CHARACTER_TEST_DSN"
REQUIRE_DB_ENV = "CHARACTER_TEST_REQUIRE_DB"


def _no_database(reason):
    """Skip, or fail when CHARACTER_TEST_REQUIRE_DB=1 (harness runs)."""
    if os.environ.get(REQUIRE_DB_ENV) == "1":
        pytest.fail(f"{reason} ({REQUIRE_DB_ENV}=1)", pytrace=False)
    pytest.skip(reason)


def _dsn_with_db(dsn, dbname):
    """The same server and credentials as `dsn` (URI or key=value), another database."""
    from psycopg2.extensions import make_dsn, parse_dsn

    parts = parse_dsn(dsn)
    parts["dbname"] = dbname
    return make_dsn(**parts)


def _admin_exec(admin_dsn, sql):
    """Run one statement outside a transaction (CREATE/DROP DATABASE need that)."""
    import psycopg2

    conn = psycopg2.connect(admin_dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()


@pytest.fixture(scope="session")
def _pg_admin(tmp_path_factory):
    """(admin_dsn, template_name) for the session's Postgres, or skip."""
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        _no_database("psycopg2 not installed")

    server = None
    admin_dsn = os.environ.get(TEST_DSN_ENV)
    if admin_dsn:
        log.info("character tests use %s (value not logged)", TEST_DSN_ENV)
    else:
        try:
            import pgserver
        except ImportError:
            _no_database(f"no {TEST_DSN_ENV} and pgserver not installed "
                         "(pip install -r requirements-dev.txt)")
        datadir = tmp_path_factory.mktemp("chpg")
        try:
            server = pgserver.get_server(str(datadir), cleanup_mode="delete")
        except Exception as exc:  # noqa: BLE001 - any start failure means "no DB here"
            log.error("pgserver failed to start in %s: %s", datadir, exc)
            _no_database(f"pgserver failed to start: {exc}")
        admin_dsn = server.get_uri("postgres")
        log.info("character tests started pgserver in %s", datadir)

    template = f"chv4_tmpl_{uuid.uuid4().hex[:10]}"
    _admin_exec(admin_dsn, f'CREATE DATABASE "{template}" TEMPLATE template0')
    try:
        yield admin_dsn, template
    finally:
        try:
            _admin_exec(admin_dsn, f'DROP DATABASE IF EXISTS "{template}" WITH (FORCE)')
        except Exception as exc:  # noqa: BLE001 - teardown must not mask test results
            log.error("could not drop template database %s: %s", template, exc)
        if server is not None:
            server.cleanup()


@pytest.fixture
def pg(_pg_admin):
    """DSN of a fresh, empty database, dropped after the test."""
    admin_dsn, template = _pg_admin
    name = f"chv4_t_{uuid.uuid4().hex[:12]}"
    _admin_exec(admin_dsn, f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    log.debug("created test database %s", name)
    try:
        yield _dsn_with_db(admin_dsn, name)
    finally:
        _admin_exec(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        log.debug("dropped test database %s", name)


def sql_files():
    """The numbered migration files, in order (001_init.sql, 002_..., ...)."""
    return sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")) if SQL_DIR.is_dir() else []


@pytest.fixture
def pg_conn(pg):
    """An open connection to `pg` with every app/character/sql/*.sql applied."""
    import psycopg2

    files = sql_files()
    if not files:
        pytest.skip("app/character/sql/ has no migration files yet")
    conn = psycopg2.connect(pg)
    try:
        with conn.cursor() as cur:
            for path in files:
                cur.execute(path.read_text(encoding="utf-8"))
        conn.commit()
        yield conn
    finally:
        conn.close()
