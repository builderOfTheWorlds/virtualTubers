# docs/sql/

One-time setup scripts for this project's dedicated Postgres role, database,
and tables, run against the shared Postgres instance referenced by
`POSTGRES_HOST`/`POSTGRES_PORT` in `.env`.

## Order

1. **`01_create_role_and_database.sql`** — creates the `virtualtubers` login
   role and a dedicated `virtualtubers` database owned by it. Run once as a
   Postgres superuser (e.g. `postgres`).
2. **`02_create_tables.sql`** — creates the `messages`, `voiced_narration`,
   `replay_episodes` and `container_logs` tables. Run as the
   `virtualtubers` role against the `virtualtubers` database. Optional:
   `message-logger`, `log-shipper` and `message-api` each create the tables
   they own automatically (`CREATE TABLE IF NOT EXISTS`) on startup, so
   this step is for reviewing/recreating the schema by hand.

## Running

On Windows, `scripts\install_db.ps1` runs both steps in order and prompts for
the two passwords (superuser + new role):

```powershell
.\scripts\install_db.ps1
```

Or run the two scripts manually:

```bash
psql -h <POSTGRES_HOST> -p <POSTGRES_PORT> -U postgres \
     -v pg_password="replace-with-a-real-password" \
     -f docs/sql/01_create_role_and_database.sql

psql -h <POSTGRES_HOST> -p <POSTGRES_PORT> -U virtualtubers -d virtualtubers \
     -f docs/sql/02_create_tables.sql
```

Then point the project at the new role/database in `.env`:

```
POSTGRES_HOST=<POSTGRES_HOST>
POSTGRES_PORT=<POSTGRES_PORT>
POSTGRES_DB=virtualtubers
POSTGRES_USER=virtualtubers
POSTGRES_PASSWORD=<the password you passed to 01_create_role_and_database.sql>
```

## Keeping in sync

`02_create_tables.sql` mirrors the `CREATE_TABLE_SQL` constants in
`services/message-logger/logger.py` (`messages`, `voiced_narration`),
`services/log-shipper/shipper.py` (`container_logs`) and
`app/episode_store.py` (`replay_episodes`). There's no single source of
truth between the SQL file and the Python constants — if you change one,
update the others, and `docs/database_schema.md` too.

`replay_episodes` is the odd one out: no long-lived consumer owns it, so
`message-api` runs its DDL best-effort at import (logged, never fatal) and
retries on every `/replays` request until it succeeds. A Postgres that was
down when `message-api` started therefore still gets the table on the
first request after it comes back — the workers only ever read it.

## Common gotcha: wrong database in a manual client

This project's tables live in the dedicated `virtualtubers` database, not
the older shared `mafober` database that other tools/projects on the same
Postgres instance may default to. A GUI client (DBeaver, pgAdmin, etc.)
left connected to `mafober` from a previous session will show none of this
project's tables and look exactly like they were never created — always
confirm the active connection's database name before concluding a table is
missing. `docker exec <container> env | grep POSTGRES` on any of this
project's running containers shows the database it's actually using.
