#!/usr/bin/env python3
"""
shipper.py
Follows the stdout/stderr of every container in this project's docker-compose
stack (discovered via the Docker socket) and durably logs each line to
Postgres, so logs from all workers/services can be reviewed with a single SQL
query instead of `docker logs` per container. Only ships new log lines from
the moment it starts — no historical backfill.
"""
import os
import threading
import time

import docker
import psycopg2

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS container_logs (
    id             BIGSERIAL PRIMARY KEY,
    container_name TEXT NOT NULL,
    stream         TEXT NOT NULL,
    message        TEXT NOT NULL,
    log_timestamp  TIMESTAMPTZ NOT NULL,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_container_logs_name ON container_logs (container_name);
CREATE INDEX IF NOT EXISTS idx_container_logs_timestamp ON container_logs (log_timestamp);
"""

INSERT_SQL = """
INSERT INTO container_logs (container_name, stream, message, log_timestamp)
VALUES (%(container_name)s, %(stream)s, %(message)s, %(log_timestamp)s);
"""

DELETE_OLD_LOGS_SQL = """
DELETE FROM container_logs WHERE log_timestamp < now() - %(retention_days)s * interval '1 day';
"""

POLL_INTERVAL_SECONDS = 5
PRUNE_INTERVAL_SECONDS = 3600
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))


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


def get_project_label(client):
    """Reads this container's own compose-project label so sibling containers can be filtered by it."""
    self_container = client.containers.get(os.environ["HOSTNAME"])
    return self_container.labels["com.docker.compose.project"]


def parse_log_line(raw_line):
    """Docker's timestamps=True prefixes each line with an RFC3339Nano timestamp, then a space."""
    text = raw_line.decode("utf-8", errors="replace").rstrip("\n")
    timestamp, _, message = text.partition(" ")
    return timestamp, message


def follow_stream(container, stream_name):
    conn = connect_db()
    print(f"[log-shipper] following {stream_name} of {container.name}")
    try:
        log_stream = container.logs(
            stream=True, follow=True, timestamps=True,
            stdout=(stream_name == "stdout"), stderr=(stream_name == "stderr"),
        )
        for raw_line in log_stream:
            timestamp, message = parse_log_line(raw_line)
            if not message:
                continue
            with conn.cursor() as cur:
                cur.execute(INSERT_SQL, {
                    "container_name": container.name,
                    "stream": stream_name,
                    "message": message,
                    "log_timestamp": timestamp,
                })
    except Exception as exc:
        print(f"[log-shipper] stopped following {stream_name} of {container.name}: {exc}")
    finally:
        conn.close()


def prune_old_logs(conn, retention_days):
    """Deletes container_logs rows older than retention_days; returns rows deleted."""
    with conn.cursor() as cur:
        cur.execute(DELETE_OLD_LOGS_SQL, {"retention_days": retention_days})
        return cur.rowcount


def discover_and_follow(client, project_label, followed):
    """Starts a stdout/stderr follower thread pair for any not-yet-followed container in the project."""
    containers = client.containers.list(filters={"label": f"com.docker.compose.project={project_label}"})
    for container in containers:
        if container.id in followed:
            continue
        followed.add(container.id)
        for stream_name in ("stdout", "stderr"):
            thread = threading.Thread(target=follow_stream, args=(container, stream_name), daemon=True)
            thread.start()


def main():
    print("[log-shipper] connecting to Postgres")
    conn = connect_db()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    print("[log-shipper] container_logs table ready")
    print(f"[log-shipper] pruning rows older than {RETENTION_DAYS} day(s) every {PRUNE_INTERVAL_SECONDS}s")

    client = docker.from_env()
    project_label = get_project_label(client)
    print(f"[log-shipper] watching compose project '{project_label}'")

    followed = set()
    last_prune = time.monotonic()
    deleted = prune_old_logs(conn, RETENTION_DAYS)
    print(f"[log-shipper] pruned {deleted} row(s) older than {RETENTION_DAYS} day(s)")
    while True:
        discover_and_follow(client, project_label, followed)
        if time.monotonic() - last_prune >= PRUNE_INTERVAL_SECONDS:
            deleted = prune_old_logs(conn, RETENTION_DAYS)
            print(f"[log-shipper] pruned {deleted} row(s) older than {RETENTION_DAYS} day(s)")
            last_prune = time.monotonic()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
