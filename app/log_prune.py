#!/usr/bin/env python3
"""
log_prune.py
On-demand container_logs deletion for a caller-specified time range, wired to
message-api's POST /logs/prune. Complements log-shipper's own hourly
RETENTION_DAYS-based prune (docs/log_shipper.md), which only ever deletes by
age — this lets an operator reclaim space from a known window (e.g. a noisy
debugging session) without waiting for the retention cutoff to catch up.
"""
import os

import psycopg2


def connect_db():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def prune_logs(after=None, before=None):
    """Deletes container_logs rows with log_timestamp in [after, before). Returns rows deleted."""
    if after is None and before is None:
        raise ValueError("at least one of after/before is required")

    clauses = []
    params = {}
    if after is not None:
        clauses.append("log_timestamp >= %(after)s")
        params["after"] = after
    if before is not None:
        clauses.append("log_timestamp < %(before)s")
        params["before"] = before

    sql = f"DELETE FROM container_logs WHERE {' AND '.join(clauses)};"
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount
    finally:
        conn.close()
