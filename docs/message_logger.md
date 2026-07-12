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

- Beyond the raw `messages` row every message gets, two message types are
  also typed-unpacked into their own table for structured querying (see
  `docs/sql/02_create_tables.sql` for the full schema): `coding_run_report`
  → `coding_backend_runs` (docs/coding_backend.md), and `replay_narration`
  → `voiced_narration` — one row per spoken scene from a Rerun Theater
  airing (`insert_voiced_narration`, docs/revoice.md). Both typed inserts
  are best-effort: a malformed payload logs a warning and is skipped, it
  never stops the consume loop or the raw `messages` insert.

## Usage Examples

> **Gotcha:** this project logs into its own dedicated `virtualtubers`
> database (see docs/sql/README.md), separate from the older shared
> `mafober` database other tools on the host may default to. If a table
> you expect (e.g. `voiced_narration`) looks missing in a GUI client like
> DBeaver, check which database the connection is pointed at before
> assuming the table wasn't created — `psql`/`docker exec ... env | grep
> POSTGRES` on the running container shows the database it's actually
> writing to.

Run via docker-compose (part of the main stack):
```bash
docker compose up message-logger
```

Query logged history directly:
```bash
psql -h 192.168.1.120 -U virtualtubers -d virtualtubers \
  -c "SELECT id, \"from\", \"to\", type, payload, timestamp FROM messages ORDER BY ingested_at DESC LIMIT 20;"
```

## Error Handling

- Fails fast (uncaught) if `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` aren't set (`KeyError` on `os.environ[...]`), or if Postgres/Kafka aren't reachable — intentional, so `restart: unless-stopped` retries rather than silently running half-configured.
- Inserts use `ON CONFLICT (id) DO NOTHING`, so consumer restarts / at-least-once redelivery never produce duplicate rows.

## Changelog

- v1.0.0 (2026-07-01) — Initial version. Test coverage added the same day
  in `tests/test_message_logger.py` (`connect_db` env-var resolution/
  failure, `main`'s create-table-then-insert order, per-message insert
  params, fail-fast on missing Kafka env vars) — `psycopg2.connect` and
  `MessageConsumer` mocked, matching `message-api`'s test pattern.
- v1.1.0 (2026-07-12) — Added `voiced_narration` typed unpacking for
  `replay_narration` messages (`insert_voiced_narration`, one row per
  scene) — the durable transcript for Rerun Theater's spoken narration,
  published by `app/replay_pane.py` after each voiced airing. Covered by
  new tests: multi-scene insert, empty-scenes no-op, dispatch-by-type in
  `main`, and malformed-payload resilience.
