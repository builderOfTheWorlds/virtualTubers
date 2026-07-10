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
        print(f"[logger] logged {msg['type']} {msg['from']} -> {msg['to']}")


if __name__ == "__main__":
    main()
