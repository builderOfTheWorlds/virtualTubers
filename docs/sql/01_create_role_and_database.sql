-- One-time setup: creates this project's dedicated Postgres role + database
-- on the shared Postgres instance. Run once, as a superuser (e.g. `postgres`):
--
--   psql -h <POSTGRES_HOST> -p <POSTGRES_PORT> -U postgres \
--        -v pg_password="replace-with-a-real-password" \
--        -f docs/sql/01_create_role_and_database.sql
--
-- Pass the raw password, with no extra quotes -- the :'pg_password' form
-- below already adds SQL-literal quoting/escaping. Wrapping it in quotes
-- here too would bake literal quote characters into the stored password.

-- Built as text (rather than inside a DO $$ ... $$ block) because psql does
-- NOT perform :'var' substitution inside dollar-quoted strings -- it's
-- skipped there so literal colons in PL/pgSQL bodies aren't mangled. Building
-- the statement with format()/%L and running it via \gexec keeps the
-- substitution outside any dollar-quoting.
SELECT format('CREATE ROLE virtualtubers LOGIN PASSWORD %L', :'pg_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'virtualtubers')
\gexec

-- CREATE DATABASE can't run inside a DO block or transaction, hence \gexec:
-- this SELECT only produces (and then executes) the CREATE DATABASE
-- statement when the database doesn't already exist.
SELECT 'CREATE DATABASE virtualtubers OWNER virtualtubers'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'virtualtubers')
\gexec


