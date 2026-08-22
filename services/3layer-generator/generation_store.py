"""
generation_store.py
Postgres-backed persistence for the 3-layer generator's API service.

Owns two tables:

  * generation_jobs — one row per submitted generation run. Tracks status
    (queued → running → terminal), progress, result, error, and the
    cancel_requested flag that the dispatcher polls between work units.
  * generation_artifacts — one row per (pack, kind, segment_id) natural key.
    Stores the JSONB document produced by a generation run so the GUI can
    browse and the next run can read prior output.

Design rule: every UPDATE touches ONLY the columns that function is
responsible for. update_progress writes progress + heartbeat_at and nothing
else; request_cancel writes cancel_requested and nothing else. Two such
statements against the same row cannot destroy each other, which is the
entire point of moving off the file-based read-modify-write draft.

Connection handling mirrors episode_store.py: lazy psycopg2 import, per-call
connection, 5 s connect timeout, autocommit. A machine without psycopg2 or
without POSTGRES_* env still imports this module cleanly and gets
available() == False.

Every function here RAISES on database failure. The dispatcher's own
exception handler turns that into a failed job with a readable error;
swallowing it here would report success for a write that never landed.
"""
import datetime
import logging
import os
import uuid

log = logging.getLogger(__name__)

_REQUIRED_ENV = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")

# Mirrored in docs/sql/02_create_tables.sql — keep both in sync (there is no
# migration framework in this project).
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS generation_jobs (
    id               TEXT PRIMARY KEY,
    pack             TEXT NOT NULL,
    stage            TEXT NOT NULL,
    profile          TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL,
    params           JSONB NOT NULL DEFAULT '{}'::jsonb,
    progress         JSONB,
    result           JSONB,
    error            TEXT,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    submitted_by     TEXT NOT NULL DEFAULT 'api',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    heartbeat_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_status_created
    ON generation_jobs (status, created_at);

CREATE TABLE IF NOT EXISTS generation_artifacts (
    id          BIGSERIAL PRIMARY KEY,
    pack        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    segment_id  TEXT NOT NULL DEFAULT '',
    content     JSONB NOT NULL,
    job_id      TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pack, kind, segment_id)
);
"""

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

_JOB_COLUMNS = (
    "id, pack, stage, profile, status, params, progress, result, error, "
    "cancel_requested, submitted_by, created_at, started_at, finished_at, "
    "heartbeat_at"
)


def available() -> bool:
    """True when this process can reach the store: psycopg2 importable and
    the POSTGRES_* env present. Callers treat False as "no store
    configured", never as an error."""
    if not all(os.environ.get(name) for name in _REQUIRED_ENV):
        return False
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        return False
    return True


def _connect():
    import psycopg2
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
    )
    conn.autocommit = True
    return conn


def ensure_schema() -> None:
    """Create both tables and the index if they do not exist. Safe to call
    repeatedly — every statement is IF NOT EXISTS. Raises on DB failure."""
    log.debug("ensure_schema: executing CREATE TABLE statements")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
    finally:
        conn.close()


def new_job_id() -> str:
    """A unique, sortable, human-readable id. The random suffix prevents
    collisions when a GUI submits a stage-per-segment batch in the same
    second."""
    # datetime.utcnow() is deprecated in 3.12 and the service image is
    # python:3.12-slim, so use an explicit UTC-aware clock.
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    return "job_" + stamp + "_" + uuid.uuid4().hex[:6]


def submit(record: dict) -> str:
    """Insert one queued job. `record` must contain `pack` and `stage`;
    optionally `profile`, `params`, `submitted_by`. Returns the new id.

    Only the whitelisted keys are read — the API hands this function a raw
    request body and trusting it to name columns would let a caller set
    `status` or `finished_at`."""
    pack = record["pack"]
    stage = record["stage"]
    profile = record.get("profile", "")
    params = record.get("params", {})
    submitted_by = record.get("submitted_by", "api")

    job_id = new_job_id()
    log.info("submit: job_id=%s pack=%s stage=%s profile=%s", job_id, pack, stage, profile)

    from psycopg2.extras import Json
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO generation_jobs "
                "(id, pack, stage, profile, status, params, submitted_by) "
                "VALUES (%s, %s, %s, %s, 'queued', %s, %s)",
                (job_id, pack, stage, profile, Json(params), submitted_by),
            )
    finally:
        conn.close()
    return job_id


def get(job_id: str) -> dict | None:
    """The full job row as a dict, or None when no such id exists."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_JOB_COLUMNS} FROM generation_jobs WHERE id = %s",
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_dict(cur, row)
    finally:
        conn.close()


def list_jobs(pack: str | None = None, stage: str | None = None,
              status: str | None = None) -> list:
    """Rows matching every filter that is not None, newest first. All three
    None returns every job."""
    clauses: list = []
    params: list = []
    if pack is not None:
        clauses.append("pack = %s")
        params.append(pack)
    if stage is not None:
        clauses.append("stage = %s")
        params.append(stage)
    if status is not None:
        clauses.append("status = %s")
        params.append(status)

    sql = f"SELECT {_JOB_COLUMNS} FROM generation_jobs"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"

    log.debug("list_jobs: pack=%s stage=%s status=%s", pack, stage, status)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            return [_row_to_dict(cur, row) for row in rows]
    finally:
        conn.close()


def mark_running(job_id: str) -> bool:
    """Atomically claim a queued job. Returns True when exactly one row
    changed, False when somebody else already took it."""
    log.debug("mark_running: job_id=%s", job_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generation_jobs SET status = 'running', started_at = now() "
                "WHERE id = %s AND status = 'queued'",
                (job_id,),
            )
            claimed = cur.rowcount == 1
    finally:
        conn.close()
    if claimed:
        log.info("mark_running: job_id=%s claimed", job_id)
    else:
        log.debug("mark_running: job_id=%s not claimed (not queued)", job_id)
    return claimed


def update_progress(job_id: str, progress: dict) -> None:
    """Write the progress document and bump the heartbeat. TWO COLUMNS ONLY.
    An unknown id updates zero rows — a no-op, not an error."""
    from psycopg2.extras import Json
    log.debug("update_progress: job_id=%s", job_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generation_jobs SET progress = %s, heartbeat_at = now() "
                "WHERE id = %s",
                (Json(progress), job_id),
            )
    finally:
        conn.close()


def finish(job_id: str, status: str, result: dict | None = None,
           error: str | None = None) -> bool:
    """Move a job to a terminal state. Raises ValueError when `status` is
    not in TERMINAL_STATUSES. Returns True when exactly one row changed,
    False when the job was already terminal (the caller lost the race)."""
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"finish: status {status!r} is not terminal")

    from psycopg2.extras import Json
    log.info("finish: job_id=%s status=%s", job_id, status)

    result_param = Json(result) if result is not None else None
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generation_jobs SET status = %s, result = %s, error = %s, "
                "finished_at = now() "
                "WHERE id = %s AND status NOT IN ('completed', 'failed', 'cancelled')",
                (status, result_param, error, job_id),
            )
            succeeded = cur.rowcount == 1
    finally:
        conn.close()
    if not succeeded:
        log.debug("finish: job_id=%s already terminal, no-op", job_id)
    return succeeded


def request_cancel(job_id: str) -> bool:
    """Set cancel_requested=TRUE on a queued or running job. ONE COLUMN ONLY.
    Returns True when exactly one row changed, False for terminal or unknown
    jobs."""
    log.debug("request_cancel: job_id=%s", job_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generation_jobs SET cancel_requested = TRUE "
                "WHERE id = %s AND status IN ('queued', 'running')",
                (job_id,),
            )
            cancelled = cur.rowcount == 1
    finally:
        conn.close()
    if cancelled:
        log.info("request_cancel: job_id=%s flag set", job_id)
    return cancelled


def is_cancelled(job_id: str) -> bool:
    """Poll the cancel flag. One column only — this is called in a hot loop
    between work units and must not pull every JSONB document across the
    wire. Returns False when there is no such row."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cancel_requested FROM generation_jobs WHERE id = %s",
                (job_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return False
    return bool(row[0])


def reconcile_orphans() -> int:
    """Fail every running job at service boot. A container that restarts
    mid-job leaves its row running forever; the dispatcher only claims
    queued rows so nothing would clear it. Must NOT touch queued rows —
    those are work the operator submitted that simply has not started.
    Returns the number of rows updated."""
    log.info("reconcile_orphans: checking for running jobs")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generation_jobs SET status = 'failed', "
                "finished_at = now(), error = 'interrupted by service restart' "
                "WHERE status = 'running'",
            )
            count = cur.rowcount
    finally:
        conn.close()
    log.info("reconcile_orphans: %d orphan(s) marked failed", count)
    return count


def upsert_artifact(pack: str, kind: str, segment_id: str, content: dict,
                    job_id: str | None = None) -> None:
    """Insert or replace an artifact on its (pack, kind, segment_id) natural
    key. Regenerating a segment replaces its artifact rather than
    accumulating a second copy."""
    from psycopg2.extras import Json
    log.debug("upsert_artifact: pack=%s kind=%s segment_id=%s", pack, kind, segment_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO generation_artifacts (pack, kind, segment_id, content, job_id) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (pack, kind, segment_id) DO UPDATE "
                "SET content = EXCLUDED.content, "
                "    job_id = EXCLUDED.job_id, "
                "    updated_at = now()",
                (pack, kind, segment_id, Json(content), job_id),
            )
    finally:
        conn.close()


def load_artifact(pack: str, kind: str, segment_id: str) -> dict | None:
    """The artifact document, or None when absent. psycopg2 decodes JSONB
    to a Python dict already."""
    log.debug("load_artifact: pack=%s kind=%s segment_id=%s", pack, kind, segment_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM generation_artifacts "
                "WHERE pack = %s AND kind = %s AND segment_id = %s",
                (pack, kind, segment_id),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return row[0]


def list_artifacts(pack: str) -> list:
    """Metadata-only listing for the GUI's artifact browser. The `content`
    column is deliberately NOT selected — a full segment tree is tens of
    kilobytes and a pack has hundreds, so including bodies would turn a
    listing into a multi-megabyte response."""
    log.debug("list_artifacts: pack=%s", pack)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, pack, kind, segment_id, job_id, updated_at "
                "FROM generation_artifacts WHERE pack = %s "
                "ORDER BY kind, segment_id",
                (pack,),
            )
            rows = cur.fetchall()
            return [_row_to_dict(cur, row) for row in rows]
    finally:
        conn.close()


def _row_to_dict(cursor, row: tuple) -> dict:
    """Zip cursor.description column names with the row tuple, converting
    every datetime to an ISO-8601 string. These dicts are returned straight
    out of FastAPI endpoints as JSON — a datetime is not JSON-serialisable
    and would raise at response-encoding time."""
    columns = [d[0] for d in cursor.description]
    result = {}
    for name, value in zip(columns, row):
        if isinstance(value, datetime.datetime):
            result[name] = value.isoformat()
        else:
            result[name] = value
    return result
