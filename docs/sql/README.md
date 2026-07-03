# docs/sql/

One-time setup scripts for this project's dedicated Postgres role, database,
and tables, run against the shared Postgres instance referenced by
`POSTGRES_HOST`/`POSTGRES_PORT` in `.env`.

## Order

1. **`01_create_role_and_database.sql`** — creates the `virtualtubers` login
   role and a dedicated `virtualtubers` database owned by it. Run once as a
   Postgres superuser (e.g. `postgres`).
2. **`02_create_tables.sql`** — creates the `messages` and `container_logs`
   tables. Run as the `virtualtubers` role against the `virtualtubers`
   database. Optional: `message-logger` and `log-shipper` each create these
   tables automatically (`CREATE TABLE IF NOT EXISTS`) the first time they
   start, so this step is for reviewing/recreating the schema by hand.

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
`services/message-logger/logger.py` and `services/log-shipper/shipper.py`.
There's no single source of truth between the SQL file and the Python
constants — if you change one, update the other.
