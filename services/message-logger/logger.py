#!/usr/bin/env python3
"""
logger.py
Consumes every message on the Kafka bus and durably logs it to Postgres.
Runs as its own consumer group, fully decoupled from agents and the
message-api injection service.
"""
import json
import os

import psycopg2

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
"""

INSERT_SQL = """
INSERT INTO messages (id, "from", "to", type, payload, timestamp)
VALUES (%(id)s, %(from)s, %(to)s, %(type)s, %(payload)s, %(timestamp)s)
ON CONFLICT (id) DO NOTHING;
"""


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

    for msg in consumer:
        with conn.cursor() as cur:
            cur.execute(INSERT_SQL, {
                "id": msg["id"],
                "from": msg["from"],
                "to": msg["to"],
                "type": msg["type"],
                "payload": json.dumps(msg["payload"]),
                "timestamp": msg["timestamp"],
            })
        print(f"[logger] logged {msg['type']} {msg['from']} -> {msg['to']}")


if __name__ == "__main__":
    main()
