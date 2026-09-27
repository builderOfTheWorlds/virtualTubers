#!/usr/bin/env bash
# emergency_stop.sh — take worker streams off air using ONLY Docker.
#
# Creates the local kill file (WORKER_KILL_FILE, default /tmp/worker_disabled)
# inside each targeted worker container via `docker exec`. While it exists,
# app/worker_control.py reports the worker disabled WITHOUT consulting Redis,
# so stream_supervisor.py stops ffmpeg (within ~0.5s) and agent.py pauses —
# even when Redis or message-api is down. See docs/worker_control.md.
#
# The kill file survives agent/supervisor restarts and `docker restart`, but
# NOT container re-creation (docker compose up with a changed spec,
# ./redeploy.sh) — a recreated container comes back under Redis control.
#
# Usage:
#   scripts/emergency_stop.sh                 # all running worker-* containers
#   scripts/emergency_stop.sh --all           # same, explicit
#   scripts/emergency_stop.sh coder gm        # by service suffix, service name
#                                             # (worker-coder) or container name
#   scripts/emergency_stop.sh --resume coder  # undo (= emergency_resume.sh)
#
# Idempotent: re-running on an already-stopped worker just reports it.
# Exit code: 0 if every target ended in the requested state, 1 otherwise.
set -uo pipefail

MODE=stop
VERIFY_TIMEOUT_S="${VERIFY_TIMEOUT_S:-10}"
TARGETS=()

usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --resume) MODE=resume ;;
        --all) ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
        *) TARGETS+=("$1") ;;
    esac
    shift
done

if [ -t 1 ]; then RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; RST=$'\e[0m'; else RED=; GRN=; YLW=; RST=; fi
info() { echo "${GRN}[ok]${RST}   $*"; }
warn() { echo "${YLW}[warn]${RST} $*"; }
err()  { echo "${RED}[fail]${RST} $*"; }

command -v docker >/dev/null 2>&1 || { err "docker CLI not found"; exit 1; }

# "container<TAB>compose-service" for every running worker-* container.
WORKERS="$(docker ps --format '{{.Names}}	{{.Label "com.docker.compose.service"}}' \
    | awk -F'\t' '$2 ~ /^worker-/')"
if [ -z "$WORKERS" ]; then
    err "no running worker-* containers found (docker ps)"
    exit 1
fi

resolve_target() {
    # Accept service suffix (coder), service (worker-coder) or container name.
    awk -F'\t' -v t="$1" '$1 == t || $2 == t || $2 == "worker-" t { print $1 }' <<<"$WORKERS"
}

CONTAINERS=()
if [ ${#TARGETS[@]} -eq 0 ]; then
    while IFS=$'\t' read -r name _; do CONTAINERS+=("$name"); done <<<"$WORKERS"
else
    for t in "${TARGETS[@]}"; do
        match="$(resolve_target "$t")"
        if [ -z "$match" ]; then
            err "no running worker container matches '$t'"
            exit 1
        fi
        while read -r name; do CONTAINERS+=("$name"); done <<<"$match"
    done
fi

# Runs inside the container so WORKER_KILL_FILE resolves from ITS env.
# shellcheck disable=SC2016
KF='${WORKER_KILL_FILE:-/tmp/worker_disabled}'
failures=0

for c in "${CONTAINERS[@]}"; do
    if [ "$MODE" = stop ]; then
        state="$(docker exec "$c" sh -c "f=\"$KF\"; if [ -e \"\$f\" ]; then echo already; \
            else echo \"disabled_at=\$(date -Iseconds) reason=emergency_stop.sh\" > \"\$f\" && echo created; fi; echo \"\$f\"" 2>&1)"
        rc=$?
        kf="$(tail -n1 <<<"$state")"
        if [ $rc -ne 0 ]; then
            err "$c: could not create kill file: $state"; failures=$((failures + 1)); continue
        fi
        case "$(head -n1 <<<"$state")" in
            already) info "$c: kill file already present ($kf)" ;;
            *)       info "$c: kill file created ($kf)" ;;
        esac
        # Verify ffmpeg actually went away — an image older than the kill
        # switch ignores the file, and the operator needs to know that.
        stopped=0
        for _ in $(seq 1 "$VERIFY_TIMEOUT_S"); do
            docker exec "$c" pgrep -x ffmpeg >/dev/null 2>&1
            prc=$?
            [ $prc -eq 1 ] && { stopped=1; break; }         # no match
            [ $prc -ne 0 ] && { stopped=2; break; }         # pgrep unusable
            sleep 1
        done
        if [ $stopped -eq 1 ]; then
            info "$c: ffmpeg not running — OFF AIR"
        elif [ $stopped -eq 2 ]; then
            warn "$c: could not verify ffmpeg state (pgrep exit $prc) — check the stream"
        else
            err "$c: ffmpeg still running after ${VERIFY_TIMEOUT_S}s (image may predate the kill switch)."
            err "      last resort: docker stop $c"
            failures=$((failures + 1))
        fi
    else
        state="$(docker exec "$c" sh -c "f=\"$KF\"; if [ -e \"\$f\" ]; then rm -f \"\$f\" && echo removed; \
            else echo absent; fi; echo \"\$f\"" 2>&1)"
        rc=$?
        kf="$(tail -n1 <<<"$state")"
        if [ $rc -ne 0 ]; then
            err "$c: could not remove kill file: $state"; failures=$((failures + 1)); continue
        fi
        case "$(head -n1 <<<"$state")" in
            removed) info "$c: kill file removed ($kf) — back under Redis control" ;;
            *)       info "$c: no kill file present ($kf) — nothing to resume" ;;
        esac
        warn "$c: streams again only if Redis worker:<id>:enabled is not \"0\" (or Redis is down: fail-open)"
    fi
done

echo
if [ $failures -eq 0 ]; then
    info "$MODE complete for ${#CONTAINERS[@]} container(s)"
    exit 0
fi
err "$MODE finished with $failures failure(s)"
exit 1
