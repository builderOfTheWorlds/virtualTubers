#!/usr/bin/env python3
"""
logger.py
Consumes every message on the Kafka bus and durably logs it to Postgres.
Runs as its own consumer group, fully decoupled from agents and the
message-api injection service.
Messages whose type is excluded via LogFilterControl (e.g. the per-tick
heartbeat status_update flood) are dropped before the INSERT — see
docs/log_filter_control.md.
"""
import json
import os

import psycopg2

from log_filter_control import LogFilterControl
from message_bus import MessageConsumer

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY,
    "from"      TEXT NOT NULL,
    "to"        TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     JSONB NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_to ON messages ("to");
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages (type);

-- Typed unpacking of coding_run_report messages: one row per coding-backend
-- run, for A/B comparison queries (see docs/coding_backend.md). The raw
-- message also lands in messages like everything else.
CREATE TABLE IF NOT EXISTS coding_backend_runs (
    message_id    UUID PRIMARY KEY,
    worker_id     TEXT NOT NULL,
    backend       TEXT NOT NULL,
    task          TEXT NOT NULL,
    retry_count   INTEGER NOT NULL DEFAULT 0,
    success       BOOLEAN NOT NULL,
    commit_sha    TEXT,
    files_changed INTEGER NOT NULL DEFAULT 0,
    insertions    INTEGER NOT NULL DEFAULT 0,
    deletions     INTEGER NOT NULL DEFAULT 0,
    duration_s    DOUBLE PRECISION NOT NULL DEFAULT 0,
    output        TEXT,
    error         TEXT,
    reported_at   TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_runs_backend ON coding_backend_runs (backend);
CREATE INDEX IF NOT EXISTS idx_runs_worker ON coding_backend_runs (worker_id);

-- Typed unpacking of replay_narration messages: one row per spoken scene
-- from a Rerun Theater airing (see docs/revoice.md). Text only — the
-- synthesized audio itself is never persisted, only regenerated per airing.
CREATE TABLE IF NOT EXISTS voiced_narration (
    message_id  UUID NOT NULL,
    worker_id   TEXT NOT NULL,
    episode     TEXT NOT NULL,
    aired_at    TIMESTAMPTZ NOT NULL,
    scene_index INTEGER NOT NULL,
    scene_kind  TEXT NOT NULL,
    speaker     TEXT NOT NULL,
    text        TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, scene_index)
);
CREATE INDEX IF NOT EXISTS idx_voiced_narration_episode ON voiced_narration (episode);
"""

INSERT_SQL = """
INSERT INTO messages (id, "from", "to", type, payload, timestamp)
VALUES (%(id)s, %(from)s, %(to)s, %(type)s, %(payload)s, %(timestamp)s)
ON CONFLICT (id) DO NOTHING;
"""

INSERT_RUN_SQL = """
INSERT INTO coding_backend_runs (
    message_id, worker_id, backend, task, retry_count, success, commit_sha,
    files_changed, insertions, deletions, duration_s, output, error, reported_at
) VALUES (
    %(message_id)s, %(worker_id)s, %(backend)s, %(task)s, %(retry_count)s,
    %(success)s, %(commit_sha)s, %(files_changed)s, %(insertions)s,
    %(deletions)s, %(duration_s)s, %(output)s, %(error)s, %(reported_at)s
)
ON CONFLICT (message_id) DO NOTHING;
"""

INSERT_NARRATION_SQL = """
INSERT INTO voiced_narration (
    message_id, worker_id, episode, aired_at, scene_index, scene_kind, speaker, text
) VALUES (
    %(message_id)s, %(worker_id)s, %(episode)s, %(aired_at)s, %(scene_index)s,
    %(scene_kind)s, %(speaker)s, %(text)s
)
ON CONFLICT (message_id, scene_index) DO NOTHING;
"""


def insert_coding_run(cur, msg):
    """Unpack a coding_run_report payload into coding_backend_runs. Defensive
    .get()s throughout: a malformed report still lands in messages; this
    typed row is best-effort and must never crash the logger loop."""
    p = msg.get("payload", {}) or {}
    cur.execute(INSERT_RUN_SQL, {
        "message_id": msg["id"],
        "worker_id": msg.get("from", "unknown"),
        "backend": p.get("backend", "unknown"),
        "task": p.get("task", ""),
        "retry_count": p.get("retry_count", 0),
        "success": bool(p.get("success")),
        "commit_sha": p.get("commit"),
        "files_changed": p.get("files_changed", 0),
        "insertions": p.get("insertions", 0),
        "deletions": p.get("deletions", 0),
        "duration_s": p.get("duration_s", 0.0),
        "output": p.get("output"),
        "error": p.get("error"),
        "reported_at": msg["timestamp"],
    })


def insert_voiced_narration(cur, msg):
    """Unpack a replay_narration payload into voiced_narration: one row per
    spoken scene. Defensive .get()s throughout: a malformed report still
    lands in messages; this typed row is best-effort and must never crash
    the logger loop."""
    p = msg.get("payload", {}) or {}
    episode = p.get("episode", "")
    aired_at = p.get("aired_at") or msg["timestamp"]
    for scene in p.get("scenes", []) or []:
        cur.execute(INSERT_NARRATION_SQL, {
            "message_id": msg["id"],
            "worker_id": msg.get("from", "unknown"),
            "episode": episode,
            "aired_at": aired_at,
            "scene_index": scene.get("index", 0),
            "scene_kind": scene.get("kind", ""),
            "speaker": scene.get("speaker", ""),
            "text": scene.get("text", ""),
        })


def connect_db():
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    conn.autocommit = True
    return conn


def main():
    bootstrap_servers = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
    topic = os.environ["KAFKA_TOPIC"]
    group_id = os.environ.get("KAFKA_GROUP_ID", "vtuber-logger")

    print(f"[logger] connecting to Postgres at {os.environ.get('POSTGRES_HOST')}")
    conn = connect_db()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    print("[logger] messages table ready")

    print(f"[logger] consuming topic={topic} group={group_id} bootstrap={bootstrap_servers}")
    consumer = MessageConsumer(bootstrap_servers, topic, group_id=group_id)
    log_filter = LogFilterControl.from_config()

    for msg in consumer:
        if log_filter.is_excluded(msg["type"]):
            continue
        with conn.cursor() as cur:
            cur.execute(INSERT_SQL, {
                "id": msg["id"],
                "from": msg["from"],
                "to": msg["to"],
                "type": msg["type"],
                "payload": json.dumps(msg["payload"]),
                "timestamp": msg["timestamp"],
            })
            if msg["type"] == "coding_run_report":
                try:
                    insert_coding_run(cur, msg)
                except Exception as exc:
                    # Raw message is already in `messages`; a bad typed
                    # unpack must not stop the logging loop.
                    print(f"[logger] WARN coding_backend_runs insert failed: {exc}")
            if msg["type"] == "replay_narration":
                try:
                    insert_voiced_narration(cur, msg)
                except Exception as exc:
                    print(f"[logger] WARN voiced_narration insert failed: {exc}")
        print(f"[logger] logged {msg['type']} {msg['from']} -> {msg['to']}")


if __name__ == "__main__":
    main()
