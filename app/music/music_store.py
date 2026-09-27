"""
music_store.py — Postgres record of every piece of music the engine plays.

Three tables (docs/database_schema.md, docs/sql/02_create_tables.sql):

  music_themes    — one row per campaign theme (the score's DNA), upserted
                    whenever a session starts so the DB always holds the
                    exact theme that was played.
  music_sessions  — one row per engine run (a roundtable airing, or an
                    offline `music.cli render --record`).
  music_segments  — contiguous runs of bars in one mood: the MOOD/INTENSITY
                    and why (scene cue / GM override), the resolved musical
                    parameters, every NOTE played (JSONB), and the rendered
                    AUDIO (compressed, BYTEA).

The notes + params + theme + engine_version are enough to re-render a
segment bit-for-bit (the engine is deterministic); the audio column is what
actually aired, kept so it can be listened to/exported without the engine.

Connection handling mirrors episode_store.py / narration_store.py: lazy
psycopg2 import, per-call connection, 5 s connect timeout, autocommit, and
every function RAISES on DB failure — degrading is the caller's job
(recorder.py does it on a background thread so a slow/down DB never stalls
the music).
"""
import json
import logging
import os
from typing import Any

log = logging.getLogger("music.store")

_REQUIRED_ENV = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")

# Mirrored in docs/sql/02_create_tables.sql and documented in
# docs/database_schema.md — keep all three in sync (no migration framework).
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS music_themes (
    name            TEXT PRIMARY KEY,
    campaign        TEXT NOT NULL DEFAULT '',
    theme           JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS music_sessions (
    id              BIGSERIAL PRIMARY KEY,
    worker_id       TEXT NOT NULL DEFAULT '',
    campaign        TEXT NOT NULL DEFAULT '',
    episode         TEXT NOT NULL DEFAULT '',
    theme_name      TEXT NOT NULL,
    theme           JSONB NOT NULL,
    engine_version  TEXT NOT NULL,
    sample_rate     INTEGER NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_music_sessions_started_at
    ON music_sessions (started_at DESC);

CREATE TABLE IF NOT EXISTS music_segments (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL REFERENCES music_sessions(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    mood            TEXT NOT NULL,
    intensity       REAL NOT NULL,
    source          TEXT NOT NULL DEFAULT '',
    scene_id        TEXT NOT NULL DEFAULT '',
    bar_start       INTEGER NOT NULL,
    bar_count       INTEGER NOT NULL,
    start_s         DOUBLE PRECISION NOT NULL,
    duration_s      DOUBLE PRECISION NOT NULL,
    params          JSONB NOT NULL,
    notes           JSONB NOT NULL,
    audio           BYTEA,
    audio_format    TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_music_segments_mood ON music_segments (mood);
"""

UPSERT_THEME_SQL = """
INSERT INTO music_themes (name, campaign, theme)
VALUES (%(name)s, %(campaign)s, %(theme)s)
ON CONFLICT (name) DO UPDATE
    SET campaign = EXCLUDED.campaign, theme = EXCLUDED.theme, updated_at = now();
"""

START_SESSION_SQL = """
INSERT INTO music_sessions (worker_id, campaign, episode, theme_name, theme,
                            engine_version, sample_rate)
VALUES (%(worker_id)s, %(campaign)s, %(episode)s, %(theme_name)s, %(theme)s,
        %(engine_version)s, %(sample_rate)s)
RETURNING id;
"""

END_SESSION_SQL = "UPDATE music_sessions SET ended_at = now() WHERE id = %(id)s;"

SAVE_SEGMENT_SQL = """
INSERT INTO music_segments (session_id, seq, mood, intensity, source, scene_id,
                            bar_start, bar_count, start_s, duration_s, params,
                            notes, audio, audio_format)
VALUES (%(session_id)s, %(seq)s, %(mood)s, %(intensity)s, %(source)s,
        %(scene_id)s, %(bar_start)s, %(bar_count)s, %(start_s)s,
        %(duration_s)s, %(params)s, %(notes)s, %(audio)s, %(audio_format)s)
ON CONFLICT (session_id, seq) DO NOTHING;
"""

LIST_SEGMENTS_SQL = """
SELECT id, seq, mood, intensity, source, scene_id, bar_start, bar_count,
       start_s, duration_s, audio_format, octet_length(audio)
FROM music_segments WHERE session_id = %(session_id)s ORDER BY seq;
"""

LOAD_SEGMENT_AUDIO_SQL = "SELECT audio, audio_format FROM music_segments WHERE id = %(id)s;"


def available():
    """True when psycopg2 imports and POSTGRES_* env is present."""
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


def _execute(sql, params=None, fetch=None) -> Any:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return cur.rowcount
    finally:
        conn.close()


def ensure_schema():
    log.debug("music_store.ensure_schema")
    _execute(CREATE_TABLE_SQL)


def upsert_theme(theme_dict, campaign=""):
    log.debug("music_store.upsert_theme name=%s", theme_dict.get("name"))
    return _execute(UPSERT_THEME_SQL, {
        "name": theme_dict["name"], "campaign": campaign,
        "theme": json.dumps(theme_dict, ensure_ascii=False),
    })


def start_session(theme_dict, engine_version, sample_rate, worker_id="",
                  campaign="", episode=""):
    """Insert a session row; returns its id."""
    row = _execute(START_SESSION_SQL, {
        "worker_id": worker_id, "campaign": campaign, "episode": episode,
        "theme_name": theme_dict["name"],
        "theme": json.dumps(theme_dict, ensure_ascii=False),
        "engine_version": engine_version, "sample_rate": int(sample_rate),
    }, fetch="one")
    session_id = row[0]
    log.info("music session started id=%s theme=%s worker=%s", session_id,
             theme_dict["name"], worker_id)
    return session_id


def end_session(session_id):
    log.debug("music_store.end_session id=%s", session_id)
    return _execute(END_SESSION_SQL, {"id": session_id})


def save_segment(segment):
    """Insert one segment dict (see recorder.Segment.to_row). Returns True
    when written, False on a duplicate (session_id, seq)."""
    import psycopg2
    row = dict(segment)
    row["params"] = json.dumps(row["params"], ensure_ascii=False)
    row["notes"] = json.dumps(row["notes"], ensure_ascii=False)
    row["audio"] = psycopg2.Binary(row["audio"]) if row.get("audio") else None
    written = _execute(SAVE_SEGMENT_SQL, row) > 0
    log.debug("music_store.save_segment session=%s seq=%s mood=%s written=%s bytes=%s",
              row["session_id"], row["seq"], row["mood"], written,
              len(segment.get("audio") or b""))
    return written


def list_segments(session_id):
    rows = _execute(LIST_SEGMENTS_SQL, {"session_id": session_id}, fetch="all")
    keys = ("id", "seq", "mood", "intensity", "source", "scene_id", "bar_start",
            "bar_count", "start_s", "duration_s", "audio_format", "audio_bytes")
    return [dict(zip(keys, r)) for r in rows]


def load_segment_audio(segment_id):
    """(bytes, format) for one segment, or None if it doesn't exist."""
    row = _execute(LOAD_SEGMENT_AUDIO_SQL, {"id": segment_id}, fetch="one")
    if not row:
        return None
    return (bytes(row[0]) if row[0] is not None else None), row[1]
