#!/bin/bash
#
# redeploy.sh — one-shot "pull the latest code onto the running stack"
#
# Rebuilds every app-code Docker image this stack uses, then force-recreates
# the containers that run them so the new code actually takes effect
# (docker compose up -d alone is a no-op when only the image CONTENTS changed
# — compose only recreates on a config/tag change, not an image rebuild).
#
# This stack streams to 7 live Twitch channels 24/7 (6 character workers +
# the GM/roundtable channel). Recreating a worker container restarts its
# ffmpeg process, causing a brief (~15-30s) stream drop/reconnect on that
# channel. Confirms before touching anything unless -y is passed.
#
# Usage:
#   ./redeploy.sh              # interactive, asks to confirm the live-stream hit
#   ./redeploy.sh -y           # skip confirmation (e.g. from cron/CI)
#   ./redeploy.sh --skip-tests # skip the pytest gate (not recommended)
#   ./redeploy.sh --office     # run the ashiorid_office show instead: layers
#                              # docker-compose.office.yml over docker-compose.yml
#                              # (8 office seats + worker-observer, see
#                              # docs/office_deployment.md). Omit it to go back
#                              # to the dev-team show.
#
# What it does, in order:
#   1. pytest smoke gate (non-blocking warn-only — see Testing note below)
#   2. ensure base infra is up (docker compose up -d, no service list) —
#      fills in redis/kafka/postgres if any went missing since last deploy.
#   3. install.sh (SKIP_VOICES=1) — rebuilds every image with new code:
#        vtube-worker:latest, virtualtubers-message-logger,
#        virtualtubers-message-api, virtualtubers-control-panel,
#        virtualtubers-log-shipper, virtualtubers-twitch-presence
#      plus the two docker-compose `build:` services (campaign-manager,
#      3layer-generator) via `docker compose build`.
#   4. force-recreate every container backed by those images.
#   5. verify: image ID parity + a live rendered frame off the GM channel.
#
# Testing note: the full pytest suite has pre-existing failures unrelated to
# deploy readiness (test_episode_validator_show.py's own harness bug,
# 3 test_tile_pane.py fade-lifecycle failures — see session notes). This
# script runs pytest and prints the result but does NOT abort on failure;
# read the output before answering the confirmation prompt.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# ── flags ───────────────────────────────────────────────────────────────────
ASSUME_YES=0
SKIP_TESTS=0
OFFICE=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        --office) OFFICE=1 ;;
        *) echo "unknown flag: $arg" >&2; exit 1 ;;
    esac
done

log()  { echo "[redeploy] $*"; }
warn() { echo "[redeploy] WARN: $*" >&2; }

# ── office mode (--office) ────────────────────────────────────────────────────
# COMPOSE_FILE is read by every `docker compose` call below (it takes
# precedence over a COMPOSE_FILE line in .env), so the office override applies
# to infra-up, build, the derived worker list (which then includes
# worker-observer) and the force-recreate alike. Without --office nothing is
# exported, so the dev-team show is exactly what docker-compose.yml describes.
if [[ "$OFFICE" == "1" ]]; then
    export COMPOSE_FILE="docker-compose.yml:docker-compose.office.yml"
    log "Office mode: COMPOSE_FILE=${COMPOSE_FILE}"
else
    # Leaving office mode does not remove the office-only container.
    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'virtualtubers-worker-observer-1'; then
        warn "virtualtubers-worker-observer-1 (office tuber_7) still exists; stop it with: docker rm -f virtualtubers-worker-observer-1"
    fi
fi

# ── 0. pytest smoke gate (warn-only, see Testing note above) ─────────────────
if [[ "$SKIP_TESTS" == "1" ]]; then
    log "Skipping pytest (--skip-tests)"
elif command -v pytest >/dev/null 2>&1 || python3 -m pytest --version >/dev/null 2>&1; then
    log "Running pytest (informational — pre-existing failures do not block deploy)"
    python3 -m pytest tests/ -q --ignore=tests/test_episode_validator_show.py || \
        warn "pytest reported failures — review above before confirming the deploy"
else
    warn "pytest not found on PATH — skipping test gate"
fi

# ── 1. confirm the live-stream interruption ───────────────────────────────────
if [[ "$ASSUME_YES" != "1" ]]; then
    echo ""
    echo "This will rebuild images and FORCE-RECREATE every worker container,"
    echo "briefly interrupting all 7 live Twitch streams (character workers +"
    echo "the GM/roundtable channel) as ffmpeg restarts on each."
    read -r -p "Proceed? [y/N] " reply
    case "$reply" in
        [yY]|[yY][eE][sS]) ;;
        *) log "Aborted."; exit 0 ;;
    esac
fi

# ── 2. ensure base infra is up ────────────────────────────────────────────────
# redeploy.sh only force-recreates the app-code services it knows about
# (WORKERS + SUPPORT_SERVICES, below) — it never touches base infra like
# redis or the profile-gated kafka/postgres, so if one of those containers
# is ever missing entirely (host reboot, `docker compose down`, manual prune,
# a prior deploy whose image pull got interrupted), nothing downstream would
# notice: the worker/image-parity checks at the end don't probe Kafka/Redis
# connectivity, so the script would happily report "Redeploy complete" while
# message-api silently crash-loops on `Unable to bootstrap from kafka:9092`
# (confirmed happening in practice — see Gitea/session notes 2026-09-27).
# Plain `docker compose up -d` with no service list and no --force-recreate
# is the fix: compose only creates what's missing or config-changed, so this
# is a no-op for anything already running correctly, but fills in any base
# service (redis unconditionally, kafka/postgres per whatever
# COMPOSE_PROFILES is set in this host's .env) that vanished since the last
# deploy — without disturbing already-running workers.
log "Ensuring base infra (redis + any active COMPOSE_PROFILES) is up"
docker compose up -d

# ── 3. rebuild every app-code image ───────────────────────────────────────────
log "Rebuilding docker-tagged images via install.sh (SKIP_VOICES=1)"
SKIP_VOICES=1 ./install.sh

log "Rebuilding compose-managed images (campaign-manager, 3layer-generator)"
docker compose build campaign-manager 3layer-generator

# ── 4. force-recreate every container running those images ───────────────────
# Ordinary `docker compose up -d` is a NO-OP here: none of these services'
# compose config changed, only the underlying image content — compose only
# recreates on a config/tag diff, so --force-recreate is required to actually
# pick up the rebuilt image.
# The worker list is DERIVED from docker-compose.yml, never hardcoded. A
# hardcoded array silently skips any newly added worker: the deploy reports
# success, every listed container restarts with fresh code, and the new
# service is simply never created — which looks exactly like "my changes
# didn't deploy" with nothing in the logs to say why. Compose is the single
# source of truth for which workers exist, so ask it.
mapfile -t WORKERS < <(docker compose config --services | grep '^worker-' | sort)

if [[ ${#WORKERS[@]} -eq 0 ]]; then
    echo "ERROR: no worker-* services found in docker compose config." >&2
    echo "Refusing to continue — this would restart nothing and report success." >&2
    exit 1
fi

SUPPORT_SERVICES=(message-logger message-api control-panel log-shipper \
                   twitch-presence campaign-manager 3layer-generator)

log "Force-recreating worker containers (streams will blip): ${WORKERS[*]}"
docker compose up -d --no-deps --force-recreate "${WORKERS[@]}"

log "Force-recreating support services: ${SUPPORT_SERVICES[*]}"
docker compose up -d --no-deps --force-recreate "${SUPPORT_SERVICES[@]}"

# ── 5. verify ──────────────────────────────────────────────────────────────
log "Waiting 20s for containers to settle..."
sleep 20

log "Container status:"
docker compose ps --format 'table {{.Name}}\t{{.Status}}' \
    | grep -E 'worker-|message-|control-panel|log-shipper|twitch-presence|campaign-manager|3layer-generator|NAME'

FAIL=0
log "Verifying image ID parity across worker containers..."
EXPECTED_IMG="$(docker images -q vtube-worker:latest)"
for w in "${WORKERS[@]}"; do
    cid="virtualtubers-${w}-1"
    actual_img="$(docker inspect "$cid" --format '{{.Image}}' 2>/dev/null | sed 's/^sha256://')"
    if [[ "$actual_img" != "$EXPECTED_IMG"* ]]; then
        warn "$cid is NOT running the freshly built vtube-worker image"
        FAIL=1
    fi
done

log "Verifying message-api can reach kafka + redis..."
# healthz only ever returns a static {"status":"ok"} — it doesn't probe
# Kafka/Redis at all, so it would NOT have caught the kafka/redis containers
# going missing entirely (the actual incident this check exists for, see
# session notes 2026-09-27: kafka/redis vanished from a prior deploy,
# message-api crash-looped on `Unable to bootstrap from kafka:9092`, and the
# old verify step — image parity + a GM frame grab — reported success anyway
# since none of that touches Kafka/Redis). Probe both directly by DNS+TCP
# connect from inside the message-api container, which is where it matters.
for infra_host_port in "kafka:9092" "redis:6379"; do
    infra_host="${infra_host_port%:*}"
    infra_port="${infra_host_port#*:}"
    if docker exec virtualtubers-message-api-1 python3 -c "
import socket
s = socket.create_connection(('${infra_host}', ${infra_port}), timeout=5)
s.close()
" 2>/dev/null; then
        log "  ${infra_host}:${infra_port} reachable from message-api"
    else
        warn "message-api CANNOT reach ${infra_host}:${infra_port} — check 'docker compose up -d ${infra_host}' and 'docker logs virtualtubers-message-api-1'"
        FAIL=1
    fi
done

log "Capturing a live frame off the GM/roundtable channel (display :105)..."
if docker exec virtualtubers-worker-gm-1 sh -c \
    'ffmpeg -loglevel error -f x11grab -video_size 1920x1080 -i :105 -frames:v 1 -y /tmp/redeploy_check.png' 2>/dev/null; then
    docker cp virtualtubers-worker-gm-1:/tmp/redeploy_check.png /tmp/redeploy_check.png
    log "Frame saved to /tmp/redeploy_check.png — inspect it (e.g. vision_analyze) to confirm no crash/blank screen"
else
    warn "Could not capture a frame from worker-gm — check it manually: docker logs virtualtubers-worker-gm-1"
    FAIL=1
fi

echo ""
if [[ "$FAIL" == "0" ]]; then
    log "Redeploy complete. All worker containers on the freshly built image."
else
    warn "Redeploy finished with warnings above — please verify manually."
fi
log "Useful commands:"
log "  docker compose ps"
log "  docker compose logs -f worker-gm"
log "  docker exec virtualtubers-worker-<name>-1 ps -eo etimes,args | grep ffmpeg"
