#!/bin/bash
set -e

# Rebuilds the images this stack needs after a `git pull`.
# Run from the repo root (e.g. /opt/virtualTubers on the Portainer host).
#
#   git pull && ./install.sh
#
# The worker image is NOT built by `docker compose build` — the compose file
# uses `pull_policy: never` on purpose, so Portainer/compose never touches it.
# It must be rebuilt manually on the host, then the stack redeployed
# (Portainer UI → Update the stack → Re-pull image and redeploy) to pick it up.

cd "$(dirname "${BASH_SOURCE[0]}")"

log() { echo "[install] $*"; }

log "Building vtube-worker:latest"
docker build -t vtube-worker:latest .

log "Building message-logger and message-api"
docker compose build message-logger message-api

log "Done. Redeploy the stack (Portainer: Update the stack → Re-pull image and redeploy)."
