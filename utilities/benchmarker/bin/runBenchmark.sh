#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# runBenchmark.sh — one-command launcher for the D&D-agents benchmark.
#
# Replaces the old `python -m benchmarks.dnd_agents.runner …` invocation.
# All run arguments now live in conf/benchmark.toml (see that file for the
# full option set). This script just:
#
#   1. locates the repo root + the project venv
#   2. resolves the config file  (conf/benchmark.toml, or $BENCH_MARKER_CONFIG)
#   3. hands off to `main.py` with any flags you append
#
# Usage:
#   ./bin/runBenchmark.sh                          # full battery from conf
#   ./bin/runBenchmark.sh --dry-run                # show the resolved plan
#   ./bin/runBenchmark.sh --models llama3.1:8b \\
#                         --skip-full-round        # quick per-model bench
#   BENCH_MARKER_CONFIG=/path/other.toml ./bin/runBenchmark.sh
#
# Color logging + idempotent setup follows the repo's install.sh conventions.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── color (only when stdout is a TTY) ────────────────────────────────────────
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""
fi
info()  { printf '%s\n' "${C_CYAN}▸${C_RESET} $*"; }
ok()    { printf '%s\n' "${C_GREEN}✓${C_RESET} $*"; }
warn()  { printf '%s\n' "${C_YELLOW}⚠${C_RESET} $*" >&2; }
fail()  { printf '%s\n' "${C_RED}✗${C_RESET} $*" >&2; exit 1; }

# ── locate repo root from this script's location (bin/ → benchmarker/) ──────
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BENCHMARKER_DIR="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${BENCHMARKER_DIR}/../.." >/dev/null 2>&1 && pwd)"

[[ -d "${REPO_ROOT}" ]] || fail "couldn't resolve repo root from ${SCRIPT_DIR}"
ok "repo      : ${REPO_ROOT}"

# ── resolve the Python interpreter (prefer the project venv) ─────────────────
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PY="${REPO_ROOT}/.venv/bin/python"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PY="${VIRTUAL_ENV}/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"; warn "no .venv found — falling back to ${PY}"
else
  fail "no python interpreter found (expected ${REPO_ROOT}/.venv/bin/python)"
fi
ok "python    : ${PY}"

# ── resolve the config file ──────────────────────────────────────────────────
DEFAULT_CONF="${BENCHMARKER_DIR}/conf/benchmark.toml"
CONF_FILE="${BENCH_MARKER_CONFIG:-${DEFAULT_CONF}}"
if [[ ! -f "${CONF_FILE}" ]]; then
  fail "benchmark config not found: ${CONF_FILE}
       (create it, or point \${BENCH_MARKER_CONFIG} at one)"
fi
ok "config    : ${CONF_FILE}"

# ── quick connectivity note (non-fatal; real check happens in main) ─────────
HOST_URL="$(grep -E '^\s*base_url' "${CONF_FILE}" 2>/dev/null | head -n1 \
            | sed -E 's/.*"(.*)".*/\1/' || true)"
if [[ -n "${HOST_URL:-}" ]]; then
  info "host URL  : ${HOST_URL}  (checked live by the runner)"
fi

# ── default flags: --dry-run if no args and user is on a TTY — NO. We always
#    just run. If you want a preview, pass --dry-run yourself. ────────────────
if [[ $# -eq 0 ]]; then
  info "no extra flags — running the full battery defined in the config"
fi

# ── hand off ─────────────────────────────────────────────────────────────────
# `main.py` inserts `lib/` onto sys.path and calls runner.main(argv).
info "running   : ${PY} main.py $*"
# shellcheck disable=SC2086
exec "${PY}" "${BENCHMARKER_DIR}/main.py" "$@"
