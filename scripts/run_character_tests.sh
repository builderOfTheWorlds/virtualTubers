#!/usr/bin/env bash
# Run character v4 tests against the LOCAL character-profile-db (docker compose
# project character-profile-db, :5433). Builds CHARACTER_TEST_DSN from the
# gitignored deploy/character-profile-db/.env without ever printing it.
# The superuser is used because the `pg` fixture needs CREATEDB (it creates
# and drops one throwaway database per test; the character_profile DB is untouched).
#
#   scripts/run_character_tests.sh [pytest args...]   (default: tests/character)
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/deploy/character-profile-db/.env"
[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE" >&2; exit 2; }
pw="$(grep -E '^POSTGRES_SUPERUSER_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
port="$(grep -E '^CHARACTER_DB_PORT=' "$ENV_FILE" | cut -d= -f2-)"
export CHARACTER_TEST_DSN="host=127.0.0.1 port=${port:-5433} dbname=postgres user=postgres password=${pw}"
export CHARACTER_TEST_REQUIRE_DB="${CHARACTER_TEST_REQUIRE_DB:-1}"
unset pw
cd "$ROOT"
if [[ $# -eq 0 ]]; then set -- tests/character; fi
exec .venv/bin/python -m pytest -p no:warnings "$@"
