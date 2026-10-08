"""Connections, transactions, advisory locks and migrations for the character
v4 `character_profile` database (plan §6, D-05, D-25).

Migrations are numbered SQL files in app/character/sql/ applied in order, one
transaction per file, and recorded in schema_migrations(version, applied_at,
sha256). `migrate()` refuses to run when an applied file's sha256 changed:
later schema changes are new files, never edits.

Connections are autocommit OFF; `transaction(conn)` commits on success and
rolls back on any exception. Passwords and DSNs are never logged.
"""
from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from pathlib import Path

import psycopg2

from character.config import ConfigError

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parent / "sql"

_ROLES = {"main": ("db", "db.password"), "ingest": ("ingest_db", "ingest_db.password")}

_CREATE_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sha256     TEXT NOT NULL
)
"""


class MigrationError(Exception):
    """A migration could not run; the message always names the version."""


def connect(cfg, role="main"):
    """Open a psycopg2 connection (autocommit off) for `role` ("main" | "ingest")."""
    if role not in _ROLES:
        log.error("character db: unknown role=%r", role)
        raise ValueError(f"unknown db role {role!r} (expected 'main' or 'ingest')")
    attr, password_key = _ROLES[role]
    dbc = getattr(cfg, attr)
    if dbc.password is None:
        log.error("character db: no password for role=%s key=%s", role, password_key)
        raise ConfigError(password_key, "CHARACTER_DB_PASSWORD is not set")
    log.debug("character db connecting role=%s host=%s port=%s dbname=%s",
              role, dbc.host, dbc.port, dbc.dbname)
    try:
        conn = psycopg2.connect(host=dbc.host, port=dbc.port, dbname=dbc.dbname,
                                user=dbc.user, password=dbc.password, connect_timeout=5,
                                application_name=f"character-{role}")
    except psycopg2.Error as exc:
        log.error("character db connect failed role=%s host=%s port=%s dbname=%s: %s",
                  role, dbc.host, dbc.port, dbc.dbname, type(exc).__name__)
        raise
    conn.autocommit = False
    log.debug("character db connected role=%s host=%s port=%s dbname=%s",
              role, dbc.host, dbc.port, dbc.dbname)
    return conn


@contextmanager
def transaction(conn):
    """Commit on normal exit; roll back and re-raise on any exception."""
    try:
        yield conn
    except BaseException as exc:
        log.error("character db transaction rolled back: %s", type(exc).__name__)
        conn.rollback()
        raise
    conn.commit()


def file_sha256(path) -> str:
    """Hex sha256 of the file bytes with CRLF normalised to LF."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def migrate(conn, sql_dir=None) -> list[str]:
    """Apply unapplied numbered migrations in order; return the versions applied."""
    sql_dir = Path(sql_dir) if sql_dir is not None else SQL_DIR
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_MIGRATIONS)
            cur.execute("SELECT version, sha256 FROM schema_migrations")
            recorded = dict(cur.fetchall())
        conn.commit()
    except psycopg2.Error as exc:
        log.error("character db: cannot prepare schema_migrations: %s", exc)
        conn.rollback()
        raise MigrationError(f"schema_migrations: {exc}") from exc

    files = sorted(sql_dir.glob("[0-9][0-9][0-9]_*.sql"))
    for path in files:
        version = path.stem
        log.debug("character db migration considered version=%s recorded=%s",
                  version, version in recorded)
        if version in recorded:
            actual = file_sha256(path)
            if recorded[version] != actual:
                log.error("character db migration checksum mismatch version=%s", version)
                raise MigrationError(
                    f"{version}: sha256 changed since it was applied "
                    f"(recorded {recorded[version]}, file {actual}); "
                    "add a new numbered migration instead of editing an applied one")

    applied = []
    for path in files:
        version = path.stem
        if version in recorded:
            continue
        sha = file_sha256(path)
        try:
            with conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations (version, sha256) VALUES (%s, %s)",
                            (version, sha))
            conn.commit()
        except psycopg2.Error as exc:
            log.error("character db migration failed version=%s: %s", version, exc)
            conn.rollback()
            raise MigrationError(f"{version}: {exc}") from exc
        log.info("character db migration applied version=%s", version)
        applied.append(version)
    if not applied:
        log.info("character db schema up to date (%d migrations)", len(files))
    return applied


def advisory_lock(conn, key: str) -> bool:
    """Try a session-level advisory lock on hashtext(key). Does not commit."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (key,))
        got = bool(cur.fetchone()[0])
    log.debug("character db advisory_lock key=%s acquired=%s", key, got)
    return got


def advisory_unlock(conn, key: str) -> bool:
    """Release a session-level advisory lock on hashtext(key)."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
        released = bool(cur.fetchone()[0])
    log.debug("character db advisory_unlock key=%s released=%s", key, released)
    return released
