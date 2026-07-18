"""
narration_store.py
Direct Postgres persistence for Rerun Theater narrations: after a voiced
airing, replay_pane.py saves each scene's spoken line AND its synthesized
WAV bytes into the voiced_narration table; a later replay_request with
payload.narration: "reuse" reads the latest cached airing back instead of
calling the LLM + TTS again (docs/narration_store.md).

Why direct DB and not the Kafka -> message-logger path the transcript
already rides: WAV audio for a show runs to megabytes, far past bus message
sizes. The pane therefore writes the full rows itself, reusing the SAME
message_id it published in the replay_narration bus message — the logger's
text-only insert (ON CONFLICT DO NOTHING) and this module's upsert
(ON CONFLICT DO UPDATE on the audio columns) converge on one row set per
airing no matter which lands first.

Everything here is best-effort, matching the show-must-air rule
(docs/revoice.md): missing psycopg2, missing POSTGRES_* env, or a down
database disable the store (save skipped, reuse falls back to a fresh
generation) — never an exception into the show.
"""
import os


_REQUIRED_ENV = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")

SAVE_SQL = """
INSERT INTO voiced_narration (
    message_id, worker_id, episode, aired_at, scene_index, scene_kind,
    speaker, text, audio, audio_duration_s
) VALUES (
    %(message_id)s, %(worker_id)s, %(episode)s, %(aired_at)s,
    %(scene_index)s, %(scene_kind)s, %(speaker)s, %(text)s,
    %(audio)s, %(audio_duration_s)s
)
ON CONFLICT (message_id, scene_index) DO UPDATE
    SET audio = EXCLUDED.audio,
        audio_duration_s = EXCLUDED.audio_duration_s;
"""

LOAD_SQL = """
SELECT message_id, scene_index, scene_kind, speaker, text, audio, audio_duration_s
FROM voiced_narration
WHERE message_id = (
    SELECT message_id FROM voiced_narration
    WHERE episode = %(episode)s AND audio IS NOT NULL
    ORDER BY aired_at DESC, ingested_at DESC
    LIMIT 1
)
ORDER BY scene_index;
"""

LOAD_BY_ID_SQL = """
SELECT message_id, scene_index, scene_kind, speaker, text, audio, audio_duration_s
FROM voiced_narration
WHERE message_id = %(message_id)s
ORDER BY scene_index;
"""


def available():
    """True when this process can reach the store: psycopg2 importable and
    the POSTGRES_* env present (workers only get these when docker-compose
    grants them — local dev without a DB simply runs uncached)."""
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
        # A down DB must stall the show for seconds, not minutes.
        connect_timeout=5,
    )
    conn.autocommit = True
    return conn


def save_airing(message_id, worker_id, episode, aired_at, show):
    """Persist one voiced airing: a row per scene with narration text plus
    the WAV bytes and measured duration (silent scenes save text-only).
    Returns the number of scenes saved. Raises on DB failure — callers
    (replay_pane.persist_narration) wrap this best-effort."""
    import psycopg2

    conn = _connect()
    try:
        with conn.cursor() as cur:
            for index, scene in enumerate(show):
                audio_bytes, duration = None, None
                narration = scene.get("audio")
                if narration is not None:
                    audio_bytes = psycopg2.Binary(
                        narration.audio_path.read_bytes())
                    duration = narration.duration
                cur.execute(SAVE_SQL, {
                    "message_id": message_id,
                    "worker_id": worker_id,
                    "episode": episode,
                    "aired_at": aired_at,
                    "scene_index": index,
                    "scene_kind": scene.get("kind", ""),
                    "speaker": scene.get("speaker", ""),
                    "text": scene.get("narration", ""),
                    "audio": audio_bytes,
                    "audio_duration_s": duration,
                })
        return len(show)
    finally:
        conn.close()


def _row_to_dict(row):
    return {
        "message_id": row[0],
        "scene_index": row[1],
        "scene_kind": row[2],
        "speaker": row[3],
        "text": row[4],
        # psycopg2 hands bytea back as memoryview
        "audio": bytes(row[5]) if row[5] is not None else None,
        "audio_duration_s": row[6],
    }


def load_latest_airing(episode):
    """Scenes of the most recent cached airing of `episode` that has audio,
    ordered by scene_index — a list of dicts with message_id, scene_index,
    scene_kind, speaker, text, audio (bytes or None), audio_duration_s — or
    None when the episode has never been cached. Raises on DB failure.

    message_id is included so a duet director reusing a cached airing
    (replay_request payload.narration: "reuse") can tell its followers
    exactly which airing_id to load via load_airing — "latest" can drift
    out from under a show that takes a while to invite/ready its cast."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(LOAD_SQL, {"episode": episode})
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    return [_row_to_dict(row) for row in rows]


def load_airing(message_id):
    """Scenes of the exact airing identified by `message_id`, ordered by
    scene_index — same row-dict shape as load_latest_airing (message_id,
    scene_index, scene_kind, speaker, text, audio, audio_duration_s), or
    None when the id is unknown. Raises on DB failure (callers wrap).

    This is the duet follower's read path: it loads the SAME airing the
    director persisted and published as airing_id, rather than "whatever's
    newest" — two workers airing different episodes at once would otherwise
    make load_latest_airing ambiguous."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(LOAD_BY_ID_SQL, {"message_id": message_id})
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    return [_row_to_dict(row) for row in rows]
