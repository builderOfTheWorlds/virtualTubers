"""
episode_store.py
Postgres-backed storage for the "Rerun Theater" episode library
(docs/episode_store.md). Episodes used to be JSON files on a host directory
bind-mounted read-only into every worker at /data/replays; they now live in
the replay_episodes table, uploaded through message-api's POST /replays
(docs/message_api.md) after passing episode_validator.

Two very different callers share this module:

  * message-api — the ONLY writer. Validates an uploaded episode, then
    save_episode()s it.
  * replay_pane.py / agent.py — readers. list_episodes() for the idle
    screen and the random viewer_joined pick, load_episode() to resolve a
    requested episode name into its script.

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
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_replay_episodes_uploaded_at
    ON replay_episodes (uploaded_at DESC);
"""

_INSERT_COLUMNS = """
INSERT INTO replay_episodes (
    name, script, project, session_id, episode_date,
    event_count, byte_size, uploaded_by
) VALUES (
    %(name)s, %(script)s, %(project)s, %(session_id)s, %(episode_date)s,
    %(event_count)s, %(byte_size)s, %(uploaded_by)s
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
        uploaded_at = now();
"""

LOAD_SQL = "SELECT script FROM replay_episodes WHERE name = %(name)s;"

LIST_SQL = "SELECT name FROM replay_episodes ORDER BY name;"

LIST_DETAILED_SQL = """
SELECT name, project, session_id, episode_date, event_count, byte_size,
       uploaded_by, uploaded_at
FROM replay_episodes
ORDER BY name;
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


def ensure_schema():
    """Create the replay_episodes table if it doesn't exist. Raises on DB
    failure.

    Unlike messages/container_logs, no long-lived consumer owns this table,
    so there is no service whose startup would naturally create it —
    message-api calls this best-effort instead (docs/episode_store.md)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
    finally:
        conn.close()


def save_episode(name, script, uploaded_by="operator", overwrite=False):
    """Store one validated episode script. Returns True when the row was
    written, False when an episode of that name already existed and
    `overwrite` is False. Raises on DB failure.

    The caller is expected to have run episode_validator.validate_episode
    first — this module stores what it is given and does no redaction or
    schema checking of its own."""
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
    }
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(SAVE_OVERWRITE_SQL if overwrite else SAVE_SQL, params)
            # rowcount is 0 only when ON CONFLICT DO NOTHING suppressed it.
            return cur.rowcount > 0
    finally:
        conn.close()


def load_episode(name):
    """The episode script dict for `name`, or None when the library has no
    such episode. Raises on DB failure."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(LOAD_SQL, {"name": name})
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


def list_episodes():
    """Sorted episode names — the pane's idle listing and the agent's random
    viewer_joined pick. Raises on DB failure."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(LIST_SQL)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def list_episodes_detailed():
    """Every episode's metadata (no scripts) for message-api's GET /replays.
    Raises on DB failure."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(LIST_DETAILED_SQL)
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
    } for row in rows]


def delete_episode(name):
    """Remove one episode. Returns True when a row was deleted, False when
    the name was already absent. Raises on DB failure."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(DELETE_SQL, {"name": name})
            return cur.rowcount > 0
    finally:
        conn.close()
