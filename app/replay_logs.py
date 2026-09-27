#!/usr/bin/env python3
"""
replay_logs.py
Read-only Postgres queries backing message-api's `GET /logs/containers` and
`GET /logs/messages` — the two log sources the control-panel's Rerun Theater
"Play" button now surfaces live (docs/replay_logs.md). Both are scoped to a
caller-given identifier list plus an optional `since` timestamp, so the
panel can tail just the handful of workers a single Play click targeted
instead of every container/message in the stack.

Two very different tables, two very different join keys:

  * container_logs (services/log-shipper) — one row per stdout/stderr line
    from ANY container in the compose project. Rows carry the full Docker
    container name (e.g. "virtualtubers-worker-coder-1"), not a bare
    worker id, so callers pass compose SERVICE names ("worker-coder") and
    this module anchors a regex on the compose naming convention
    "<project>-<service>-<replica>" to match. A naive substring/LIKE match
    would be wrong here: "worker-coder" is a prefix of the real container
    name for "worker-coder-native" too.
  * messages (services/message-logger) — one row per Kafka bus message.
    Rows carry the bare `from`/`to` worker ids used on the bus, so callers
    pass worker ids ("coder", "roundtable") directly.

Connection handling mirrors log_prune.py deliberately: a fresh psycopg2
connection per call, no pooling. This is polled every few seconds by the
control panel while a replay airs, not a high-throughput path.
"""
import os
import re

import psycopg2
import psycopg2.extras


def connect_db():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def _service_suffix_pattern(service: str) -> str:
    """Anchors on docker-compose's own container-naming convention
    "<project>-<service>-<replica>" so a lookup for service "worker-coder"
    never matches the real container for "worker-coder-native" — a plain
    substring/LIKE '%worker-coder%' would, since the former is a literal
    prefix of the latter."""
    return f"-{re.escape(service)}-[0-9]+$"


def fetch_container_logs(services, since=None, limit=200):
    """The most recent `limit` container_logs rows whose container name ends
    in "-<service>-<replica>" for any service in `services`. Returns
    oldest-first (a human-readable tail), even though the underlying query
    orders newest-first to bound the LIMIT correctly."""
    services = list(services)
    if not services:
        return []

    clauses = [f"container_name ~ %(svc{i})s" for i in range(len(services))]
    params = {f"svc{i}": _service_suffix_pattern(s) for i, s in enumerate(services)}
    sql = (
        "SELECT container_name, stream, message, log_timestamp "
        f"FROM container_logs WHERE ({' OR '.join(clauses)})"
    )
    if since is not None:
        sql += " AND log_timestamp > %(since)s"
        params["since"] = since
    sql += " ORDER BY log_timestamp DESC LIMIT %(limit)s"
    params["limit"] = limit

    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    rows.reverse()
    return rows


def fetch_messages(worker_ids, since=None, limit=200):
    """The most recent `limit` messages rows addressed to OR from any of
    `worker_ids` (e.g. replay_request/replay_stop/replay_narration for the
    workers a Play click targeted). Returns oldest-first, same convention
    as fetch_container_logs."""
    worker_ids = list(worker_ids)
    if not worker_ids:
        return []

    sql = (
        'SELECT "from", "to", type, payload, timestamp FROM messages '
        'WHERE ("to" = ANY(%(ids)s) OR "from" = ANY(%(ids)s))'
    )
    params = {"ids": worker_ids}
    if since is not None:
        sql += " AND timestamp > %(since)s"
        params["since"] = since
    sql += " ORDER BY timestamp DESC LIMIT %(limit)s"
    params["limit"] = limit

    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    rows.reverse()
    return rows
