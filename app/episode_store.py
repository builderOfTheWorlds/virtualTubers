"""
episode_store.py
Postgres-backed storage for the "Rerun Theater" episode library
(docs/episode_store.md). Episodes used to be JSON files on a host directory
bind-mounted read-only into every worker at /data/replays; they now live in
the replay_episodes table, uploaded through message-api's POST /replays
(docs/message_api.md) after passing episode_validator.

Two very different callers share this module:

  * message-api — the ONLY writer. Validates an uploaded episode, then
    save_episode()s it; approve_episode() promotes a draft.
  * replay_pane.py / agent.py — readers. list_episodes() for the idle
    screen and the random viewer_joined pick, load_episode() to resolve a
    requested episode name into its script.

Every episode has a review status: 'draft' or 'approved'. A draft has
passed the validator but has not been reviewed by a human yet (the
3layer-generator's opt-in auto-submit uploads drafts). Drafts NEVER air:
the read paths the workers use (load_episode, list_episodes) and the
default list_episodes_detailed() filter on status = 'approved' HERE, inside
this module, so no caller can forget the filter. Only an explicit
include_drafts=True / status= opt-in (message-api's review endpoints) ever
sees a draft.

Connection handling mirrors narration_store.py deliberately (lazy psycopg2
import, per-call connection, 5s connect timeout, autocommit): a worker
without psycopg2 or without POSTGRES_* env must still import this module
and get a clean available() == False rather than an ImportError at pane
startup.

Like narration_store, every function here RAISES on database failure.
Degrading a failure into "no episodes" is the caller's job — that keeps
this module's failure modes visible to its tests, and lets each caller
choose its own degradation (the pane says the store is unreachable; the
agent's viewer greeting just skips the rerun).
"""
import json
import os


_REQUIRED_ENV = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUSES = (STATUS_DRAFT, STATUS_APPROVED)

# Mirrored in docs/sql/02_create_tables.sql and documented in
# docs/database_schema.md — keep all three in sync (there is no migration
# framework in this project).
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS replay_episodes (
    name         TEXT PRIMARY KEY,
    script       JSONB NOT NULL,
    project      TEXT NOT NULL DEFAULT '',
    session_id   TEXT NOT NULL DEFAULT '',
    episode_date TEXT NOT NULL DEFAULT '',
    event_count  INTEGER NOT NULL,
    byte_size    INTEGER NOT NULL,
    uploaded_by  TEXT NOT NULL DEFAULT 'operator',
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       TEXT NOT NULL DEFAULT 'approved'
                 CHECK (status IN ('draft', 'approved'))
);
CREATE INDEX IF NOT EXISTS idx_replay_episodes_uploaded_at
    ON replay_episodes (uploaded_at DESC);
"""

# In-place upgrade for a replay_episodes table created before the review
# gate existed (v1.0.0 had no status column). ADD COLUMN ... DEFAULT fills
# every pre-existing row with 'approved', so everything already in the
# library keeps airing exactly as before. IF NOT EXISTS makes this a no-op
# on a fresh table (CREATE_TABLE_SQL above already has the column) and on
# every later run, so ensure_schema() stays safe to call repeatedly.
MIGRATE_SQL = """
ALTER TABLE replay_episodes
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'approved'
    CHECK (status IN ('draft', 'approved'));
CREATE INDEX IF NOT EXISTS idx_replay_episodes_status
    ON replay_episodes (status);
"""

_INSERT_COLUMNS = """
INSERT INTO replay_episodes (
    name, script, project, session_id, episode_date,
    event_count, byte_size, uploaded_by, status
) VALUES (
    %(name)s, %(script)s, %(project)s, %(session_id)s, %(episode_date)s,
    %(event_count)s, %(byte_size)s, %(uploaded_by)s, %(status)s
)
"""

SAVE_SQL = _INSERT_COLUMNS + "ON CONFLICT (name) DO NOTHING;"

SAVE_OVERWRITE_SQL = _INSERT_COLUMNS + """
ON CONFLICT (name) DO UPDATE
    SET script = EXCLUDED.script,
        project = EXCLUDED.project,
        session_id = EXCLUDED.session_id,
        episode_date = EXCLUDED.episode_date,
        event_count = EXCLUDED.event_count,
        byte_size = EXCLUDED.byte_size,
        uploaded_by = EXCLUDED.uploaded_by,
        uploaded_at = now(),
        status = EXCLUDED.status;
"""

# The default (airing) read paths: approved only. To a worker, a draft is
# indistinguishable from "no such episode".
LOAD_SQL = ("SELECT script FROM replay_episodes "
            "WHERE name = %(name)s AND status = 'approved';")
LOAD_ANY_SQL = "SELECT script FROM replay_episodes WHERE name = %(name)s;"

LIST_SQL = ("SELECT name FROM replay_episodes "
            "WHERE status = 'approved' ORDER BY name;")
LIST_ANY_SQL = "SELECT name FROM replay_episodes ORDER BY name;"

_LIST_DETAILED_SELECT = """
SELECT name, project, session_id, episode_date, event_count, byte_size,
       uploaded_by, uploaded_at, status
FROM replay_episodes
"""
LIST_DETAILED_SQL = _LIST_DETAILED_SELECT + """WHERE status = %(status)s
ORDER BY name;
"""
LIST_DETAILED_ANY_SQL = _LIST_DETAILED_SELECT + "ORDER BY name;\n"

# Promote a row to 'approved' and report what its status WAS, in one
# statement: the self-join captures the pre-update value, so the caller can
# tell "approved a draft" from "already approved" from "no such episode" (no
# row returned) without a separate read-then-write round trip. Two racing
# approvals can both report 'draft'; the end state is identical either way.
APPROVE_SQL = """
UPDATE replay_episodes AS r
SET status = 'approved'
FROM (SELECT name, status AS previous_status
      FROM replay_episodes
      WHERE name = %(name)s) AS old
WHERE r.name = old.name
RETURNING old.previous_status;
"""

DELETE_SQL = "DELETE FROM replay_episodes WHERE name = %(name)s;"


def available():
    """True when this process can reach the store: psycopg2 importable and
    the POSTGRES_* env present. Same contract as narration_store.available()
    — callers treat False as "no library", never as an error."""
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
        # A down DB must stall the pane for seconds, not minutes.
        connect_timeout=5,
    )
    conn.autocommit = True
    return conn


def _check_status(status):
    if status not in STATUSES:
        raise ValueError(
            f"invalid episode status {status!r}; expected one of {STATUSES}")


def ensure_schema():
    """Create the replay_episodes table if it doesn't exist, then run
    MIGRATE_SQL to add the review `status` column to a table created before
    it existed (existing rows become 'approved'). Both are idempotent.
    Raises on DB failure.

    Unlike messages/container_logs, no long-lived consumer owns this table,
    so there is no service whose startup would naturally create it —
    message-api calls this best-effort instead (docs/episode_store.md)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            cur.execute(MIGRATE_SQL)
    finally:
        conn.close()


def save_episode(name, script, uploaded_by="operator", overwrite=False,
                 status=STATUS_APPROVED):
    """Store one validated episode script. Returns True when the row was
    written, False when an episode of that name already existed and
    `overwrite` is False. Raises on DB failure, ValueError on an unknown
    `status`.

    `status` defaults to 'approved' — what every upload was before the
    review gate existed. Pass 'draft' to hold the episode for review; it
    will not air until approve_episode() promotes it. An overwrite replaces
    the status too, so re-uploading an aired episode as a draft pulls it
    off air until it is re-approved — never the other way round.

    The caller is expected to have run episode_validator.validate_episode
    first — this module stores what it is given and does no redaction or
    schema checking of its own."""
    _check_status(status)
    payload = json.dumps(script, ensure_ascii=False)
    params = {
        "name": name,
        "script": payload,
        "project": str(script.get("project") or ""),
        "session_id": str(script.get("session_id") or ""),
        "episode_date": str(script.get("date") or ""),
        "event_count": len(script.get("events") or []),
        "byte_size": len(payload.encode("utf-8")),
        "uploaded_by": uploaded_by,
        "status": status,
    }
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(SAVE_OVERWRITE_SQL if overwrite else SAVE_SQL, params)
            # rowcount is 0 only when ON CONFLICT DO NOTHING suppressed it.
            return cur.rowcount > 0
    finally:
        conn.close()


def load_episode(name, include_drafts=False):
    """The episode script dict for `name`, or None when the library has no
    such APPROVED episode. Raises on DB failure.

    Drafts are invisible by default — this is the path replay_pane uses to
    resolve what to air. Only a review surface (message-api's
    GET /replays/{name}) passes include_drafts=True."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(LOAD_ANY_SQL if include_drafts else LOAD_SQL,
                        {"name": name})
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    script = row[0]
    # psycopg2 decodes jsonb to dict already; tolerate a str for drivers or
    # test doubles that hand the raw column back.
    if isinstance(script, (str, bytes)):
        script = json.loads(script)
    return script


def list_episodes(include_drafts=False):
    """Sorted APPROVED episode names — the pane's idle listing and the
    agent's random viewer_joined pick. include_drafts=True lists every row
    regardless of status. Raises on DB failure."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(LIST_ANY_SQL if include_drafts else LIST_SQL)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def list_episodes_detailed(status=STATUS_APPROVED):
    """Episode metadata (no scripts) for message-api's GET /replays.

    `status` filters by review status: 'approved' (the default — the
    airable library, exactly what this returned before drafts existed),
    'draft' (the review queue), or None for every row. Raises on DB
    failure, ValueError on an unknown status."""
    if status is not None:
        _check_status(status)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if status is None:
                cur.execute(LIST_DETAILED_ANY_SQL)
            else:
                cur.execute(LIST_DETAILED_SQL, {"status": status})
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{
        "name": row[0],
        "project": row[1],
        "session_id": row[2],
        "date": row[3],
        "event_count": row[4],
        "byte_size": row[5],
        "uploaded_by": row[6],
        "uploaded_at": row[7].isoformat() if row[7] is not None else None,
        "status": row[8],
    } for row in rows]


def approve_episode(name):
    """Promote `name` to 'approved' so it can air. Returns the status the
    row had BEFORE ('draft', or 'approved' — approving twice is a harmless
    no-op), or None when there is no episode of that name. Raises on DB
    failure."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(APPROVE_SQL, {"name": name})
            row = cur.fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def delete_episode(name):
    """Remove one episode, whatever its status (deleting a draft is how a
    reviewer rejects it). Returns True when a row was deleted, False when
    the name was already absent. Raises on DB failure."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(DELETE_SQL, {"name": name})
            return cur.rowcount > 0
    finally:
        conn.close()
