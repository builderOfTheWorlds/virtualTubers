# services/message-logger/logger.py

## Overview

Standalone service that durably logs every message on the Kafka bus to
Postgres, for history/audit and to back future querying (e.g. a UI over
`SELECT * FROM messages`). Runs as its own consumer group (`vtuber-logger`),
fully decoupled from the agents and from the `message-api` injection service,
so it sees every message regardless of who produced or consumed it.

## Signature

```python
def connect_db() -> psycopg2.extensions.connection
def main() -> None
```

## Parameters

None directly — configuration comes entirely from environment variables (see docker-compose.yml):

- `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC` (required)
- `KAFKA_GROUP_ID` (optional, default `vtuber-logger`)
- `POSTGRES_HOST` (optional, default `localhost`), `POSTGRES_PORT` (optional, default `5432`)
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (required)

## Return Value

`main()` runs forever (no return) — consumes messages one at a time and inserts each into the `messages` table.

## Dependencies

- `message_bus.MessageConsumer` (`app/message_bus.py`, copied into this service's image)
- `psycopg2` (Postgres client)
- Postgres table `messages` (created on startup if missing):

```sql
CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY,
    "from"      TEXT NOT NULL,
    "to"        TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     JSONB NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Usage Examples

Run via docker-compose (part of the main stack):
```bash
docker compose up message-logger
```

Query logged history directly:
```bash
psql -h 192.168.1.120 -U mafober -d mafober \
  -c "SELECT id, \"from\", \"to\", type, payload, timestamp FROM messages ORDER BY ingested_at DESC LIMIT 20;"
```

## Error Handling

- Fails fast (uncaught) if `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` aren't set (`KeyError` on `os.environ[...]`), or if Postgres/Kafka aren't reachable — intentional, so `restart: unless-stopped` retries rather than silently running half-configured.
- Inserts use `ON CONFLICT (id) DO NOTHING`, so consumer restarts / at-least-once redelivery never produce duplicate rows.

## Changelog

- v1.0.0 (2026-07-01) — Initial version.
