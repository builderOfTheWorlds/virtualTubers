# WP-01 brief for local subagent: character_reader role

Repo root: C:/Users/matt/PycharmProjects/virtualTubers (Windows, bash shell via git-bash).
Work ONLY inside deploy/character-profile-db/. Do not touch any other file. Do not git commit.

## Goal
Add an optional read-only Postgres role `character_reader` to the deploy package.

## Edits
1. deploy/character-profile-db/docker-compose.yml, `init` service `command:` script
   (around lines 63-91). After the existing role/database block and the
   `CREATE EXTENSION IF NOT EXISTS vector` line, add a step that:
   - runs only when env var CHARACTER_READER_PASSWORD is non-empty; otherwise
     echoes "character-profile-db-init: CHARACTER_READER_PASSWORD empty, skipping character_reader";
   - creates role character_reader WITH LOGIN idempotently, using a
     `DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'character_reader') THEN CREATE ROLE character_reader LOGIN; END IF; END $$;`
     block, then sets its password the same way the existing script sets the app
     role's password (psql `\getenv` so the password never appears in argv);
   - on the app database: GRANT CONNECT ON DATABASE, GRANT USAGE ON SCHEMA public,
     GRANT SELECT ON ALL TABLES IN SCHEMA public, and
     ALTER DEFAULT PRIVILEGES FOR ROLE <app user> IN SCHEMA public GRANT SELECT ON TABLES TO character_reader.
   Remember: inside docker-compose `command:` a literal `$` must be written `$$`
   (look at how the existing script writes `$$APP_USER`). Copy the existing style exactly.
   Also pass CHARACTER_READER_PASSWORD into the init service `environment:` as
   `CHARACTER_READER_PASSWORD: ${CHARACTER_READER_PASSWORD:-}`.
2. deploy/character-profile-db/.env.example: add a commented line explaining the
   variable (optional; leave empty to skip the read-only role) followed by
   `CHARACTER_READER_PASSWORD=` with NO value.
3. deploy/character-profile-db/README.md: add a short "Read-only role" note
   describing character_reader and the env var.
4. deploy/character-profile-db/tests/run_tests.sh: add checks, in the file's existing
   check style, that (a) docker-compose.yml init command contains `character_reader`,
   `IF NOT EXISTS` and `CHARACTER_READER_PASSWORD`; (b) .env.example contains a line
   exactly `CHARACTER_READER_PASSWORD=`.

## Verify before finishing
Run: `bash deploy/character-profile-db/tests/run_tests.sh` -> must report failed: 0
(currently passed: 51). Also run
`cd deploy/character-profile-db && CHARACTER_READER_PASSWORD=x docker compose --env-file .env.example config >/dev/null && echo COMPOSE_OK`
(docker daemon is not needed for `config`).

## Final answer
List files changed, the final run_tests.sh passed/failed line, and whether COMPOSE_OK printed.
