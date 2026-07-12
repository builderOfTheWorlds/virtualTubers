# Database Schema Reference

Local lookup for every Postgres table in this project — what exists, what each column means, and why the table exists. Update this file whenever a table or column changes.

Postgres is an external, pre-existing instance (not run via docker-compose) — see `docs/sql/README.md` and `scripts/install_db.ps1` for one-time setup. There is no ORM; all access is raw SQL via `psycopg2`.

**Source of truth:** `docs/sql/02_create_tables.sql` mirrors the `CREATE_TABLE_SQL` constants that `services/message-logger/logger.py` and `services/log-shipper/shipper.py` each run on startup. There are three independent copies of the schema (the SQL file + two Python constants); if you change one, update all three. This doc is a fourth copy, kept in sync for humans — it is not authoritative.

---

## `messages`

**Owner:** `message-logger` service (`services/message-logger/logger.py`), a Kafka consumer (`vtuber-logger` group) that durably logs every message on the inter-agent bus.

**Why it exists:** history/audit backing for the bus — e.g. a UI over `SELECT * FROM messages`. Runs decoupled from agents/`message-api` so it captures every message regardless of producer or consumer.

| Column | Type | Constraints | Meaning |
|---|---|---|---|
| `id` | UUID | PRIMARY KEY | Message ID from the bus envelope |
| `"from"` | TEXT | NOT NULL | Sender agent/service ID |
| `"to"` | TEXT | NOT NULL | Recipient agent/service ID |
| `type` | TEXT | NOT NULL | Message type (e.g. `coding_run_report`) |
| `payload` | JSONB | NOT NULL | Full message body |
| `timestamp` | TIMESTAMPTZ | NOT NULL | When the message was sent (from envelope) |
| `ingested_at` | TIMESTAMPTZ | NOT NULL, DEFAULT `now()` | When message-logger wrote the row |

Indexes: `idx_messages_to (to)`, `idx_messages_type (type)`.

Inserts use `ON CONFLICT (id) DO NOTHING`, making consumer restarts / at-least-once redelivery safe against duplicates.

Defined in: `docs/sql/02_create_tables.sql:13-23`, `services/message-logger/logger.py:19-30`. Prose: `docs/message_logger.md`.

---

## `coding_backend_runs`

**Owner:** `message-logger` service — `insert_coding_run()` (`services/message-logger/logger.py:75-95`), triggered whenever a consumed message has `type == "coding_run_report"`. Reports are published by `app/coding_backend.py`, the coder worker's pluggable backend layer (`native` / `opencode` / `aider`).

**Why it exists:** one row per coding-backend run — a typed unpacking of `coding_run_report` bus messages — used as the A/B comparison table across the three coder backends. Example query (`docs/coding_backend.md`):

```sql
SELECT backend, count(*) FILTER (WHERE success) AS wins,
       avg(duration_s) AS avg_s, avg(insertions + deletions) AS avg_churn
FROM coding_backend_runs GROUP BY backend;
```

The insert is deliberately best-effort/defensive: a malformed report still lands in `messages` regardless; this typed row must never crash the logger loop, so a failed unpack only logs a WARN.

| Column | Type | Constraints | Meaning |
|---|---|---|---|
| `message_id` | UUID | PRIMARY KEY | FK-like link back to `messages.id` (not enforced) |
| `worker_id` | TEXT | NOT NULL | Which coder worker ran the task |
| `backend` | TEXT | NOT NULL | `native` \| `opencode` \| `aider` |
| `task` | TEXT | NOT NULL | Task description/identifier given to the backend |
| `retry_count` | INTEGER | NOT NULL, DEFAULT 0 | Retries before this result |
| `success` | BOOLEAN | NOT NULL | Whether the run succeeded |
| `commit_sha` | TEXT | nullable | Resulting commit, if one was made |
| `files_changed` | INTEGER | NOT NULL, DEFAULT 0 | File count touched |
| `insertions` | INTEGER | NOT NULL, DEFAULT 0 | Lines added |
| `deletions` | INTEGER | NOT NULL, DEFAULT 0 | Lines removed |
| `duration_s` | DOUBLE PRECISION | NOT NULL, DEFAULT 0 | Wall-clock run time |
| `output` | TEXT | nullable | Backend stdout/log output |
| `error` | TEXT | nullable | Error text if `success = false` |
| `reported_at` | TIMESTAMPTZ | NOT NULL | When the backend finished the run |
| `ingested_at` | TIMESTAMPTZ | NOT NULL, DEFAULT `now()` | When message-logger wrote the row |

Indexes: `idx_runs_backend (backend)`, `idx_runs_worker (worker_id)`.

Defined in: `docs/sql/02_create_tables.sql:28-46`, `services/message-logger/logger.py:35-53`. Prose: `docs/coding_backend.md`.

---

## `container_logs`

**Owner:** `log-shipper` service (`services/log-shipper/shipper.py`) — tails stdout/stderr of every container in this project's docker-compose stack (discovered via a read-only-mounted Docker socket) and inserts each line.

**Why it exists:** so logs from all workers/services can be reviewed with a single SQL query instead of running `docker logs` per container. Ships new lines only from service start — no historical backfill.

| Column | Type | Constraints | Meaning |
|---|---|---|---|
| `id` | BIGSERIAL | PRIMARY KEY | Row ID |
| `container_name` | TEXT | NOT NULL | Source container name |
| `stream` | TEXT | NOT NULL | `"stdout"` or `"stderr"` |
| `message` | TEXT | NOT NULL | Raw log line |
| `log_timestamp` | TIMESTAMPTZ | NOT NULL | Docker-reported timestamp of the line |
| `ingested_at` | TIMESTAMPTZ | NOT NULL, DEFAULT `now()` | When log-shipper wrote the row |

Indexes: `idx_container_logs_name (container_name)`, `idx_container_logs_timestamp (log_timestamp)`.

Security note: reading the Docker socket to discover sibling containers is equivalent to host root access; mounted `:ro` to reduce (not eliminate) that risk.

Defined in: `docs/sql/02_create_tables.sql:48-57`, `services/log-shipper/shipper.py:17-27`. Prose: `docs/log_shipper.md`.

---

## Keeping this in sync

When adding or changing a table:
1. Update the owning service's `CREATE_TABLE_SQL` constant (`message-logger` or `log-shipper`).
2. Update `docs/sql/02_create_tables.sql` to match.
3. Update this file.

There is no migration framework (no alembic/flyway) — all `CREATE TABLE` statements use `IF NOT EXISTS`, and there are no `ALTER TABLE` migrations tracked anywhere. Column changes to an existing table need a manual `ALTER TABLE` run against the live database in addition to updating the three schema copies above.
