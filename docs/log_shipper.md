# services/log-shipper/shipper.py

## Overview

Standalone service that follows the stdout/stderr of every container in this
project's docker-compose stack and durably logs each line to Postgres, so an
LLM or human can review logs from any/all containers with a single SQL query
instead of running `docker logs` per container. Ships new log lines only —
no historical backfill of pre-existing container output.

## Signature

```python
def connect_db() -> psycopg2.extensions.connection
def get_project_label(client: docker.DockerClient) -> str
def parse_log_line(raw_line: bytes) -> tuple[str, str]
def follow_stream(container: docker.models.containers.Container, stream_name: str) -> None
def prune_old_logs(conn: psycopg2.extensions.connection, retention_days: int) -> int
def discover_and_follow(client: docker.DockerClient, project_label: str, followed: set[str]) -> None
def main() -> None
```

## Parameters

None directly — configuration comes entirely from environment variables and
the mounted Docker socket (see docker-compose.yml):

- `POSTGRES_HOST` (optional, default `localhost`), `POSTGRES_PORT` (optional, default `5432`)
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (required)
- `RETENTION_DAYS` (optional, default `7`) — `container_logs` rows with
  `log_timestamp` older than this many days are deleted. Set via
  `LOG_RETENTION_DAYS` in `.env` (docker-compose maps it to `RETENTION_DAYS`).
- `/var/run/docker.sock` must be bind-mounted read-only into the container so
  it can discover and follow sibling containers.

## Return Value

`main()` runs forever (no return) — creates the `container_logs` table if
missing, prunes rows older than `RETENTION_DAYS` immediately and then once an
hour (`PRUNE_INTERVAL_SECONDS = 3600`), and polls every 5 seconds for
containers sharing this service's own `com.docker.compose.project` label,
spawning a stdout + stderr follower thread pair for each newly-seen container
ID.

## Dependencies

- `docker` (Docker SDK for Python) — talks to the daemon over the mounted socket
- `psycopg2` (Postgres client)
- Postgres table `container_logs` (created on startup if missing):

```sql
CREATE TABLE IF NOT EXISTS container_logs (
    id             BIGSERIAL PRIMARY KEY,
    container_name TEXT NOT NULL,
    stream         TEXT NOT NULL,
    message        TEXT NOT NULL,
    log_timestamp  TIMESTAMPTZ NOT NULL,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Usage Examples

> **Gotcha:** this project logs into its own dedicated `virtualtubers`
> database (see docs/sql/README.md), separate from the older shared
> `mafober` database other tools on the host may default to. If a table
> you expect looks missing in a GUI client like DBeaver, check which
> database the connection is pointed at before assuming the table wasn't
> created — `psql`/`docker exec ... env | grep POSTGRES` on the running
> container shows the database it's actually writing to.

Run via docker-compose (part of the main stack):
```bash
docker compose up log-shipper
```

Query recent logs across every container:
```bash
psql -h 192.168.1.120 -U virtualtubers -d virtualtubers \
  -c "SELECT container_name, stream, message, log_timestamp FROM container_logs ORDER BY log_timestamp DESC LIMIT 50;"
```

Query just one container's errors:
```bash
psql -h 192.168.1.120 -U virtualtubers -d virtualtubers \
  -c "SELECT message, log_timestamp FROM container_logs WHERE container_name = 'worker-coder' AND stream = 'stderr' ORDER BY log_timestamp DESC LIMIT 50;"
```

Keep more than the default 7 days of history (set before `docker compose up`):
```bash
echo "LOG_RETENTION_DAYS=30" >> .env
```

Manually prune a specific time range on demand (e.g. a known noisy debugging
window), without waiting for the hourly retention-based prune — via
message-api's `POST /logs/prune` (see `app/log_prune.py`,
`docs/message_api.md` if present):
```bash
curl -X POST http://192.168.1.120:8090/logs/prune \
  -H "Content-Type: application/json" \
  -d '{"after": "2026-07-01T00:00:00Z", "before": "2026-07-02T00:00:00Z"}'
```
At least one of `after`/`before` is required (both together delete an
inclusive-start/exclusive-end range; either alone deletes everything on that
side of the bound).

## Error Handling

- Fails fast (uncaught) if `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`
  aren't set, or if Postgres/the Docker socket aren't reachable — intentional,
  so `restart: unless-stopped` retries rather than silently running
  half-configured, matching `message-logger`'s convention.
- Each `follow_stream` thread catches its own exceptions (e.g. the container
  it's following stops) and exits quietly; the next discovery poll picks up
  the container again if it restarts under a new ID.

## Security Note

Discovering sibling containers requires read access to the Docker socket,
which is a broad permission grant (equivalent to host root) — there's no
finer-grained way to scope it without a proxy like
`tecnativa/docker-socket-proxy`. The socket is mounted read-only
(`:ro`) to reduce, though not eliminate, the risk.

## Changelog

- v1.1.0 (2026-07-12) — Added `prune_old_logs`: `container_logs` rows older
  than `RETENTION_DAYS` (default 7) are now deleted on startup and hourly
  thereafter, so the table no longer grows unbounded. Test coverage added
  in `tests/test_log_shipper.py` for `prune_old_logs` and the new prune call
  in `main`'s loop.
- v1.0.0 (2026-07-02) — Initial version, scoped to this project's own
  docker-compose containers only (not every container on the host), no
  historical backfill. Test coverage in `tests/test_log_shipper.py`
  (`connect_db` env-var resolution/failure, `parse_log_line` timestamp
  splitting, `get_project_label`, `discover_and_follow` thread spawning
  and dedup, `main`'s create-table-then-poll order) — `psycopg2.connect`
  and the `docker` client mocked, matching `message-logger`'s test pattern.
