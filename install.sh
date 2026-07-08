#!/bin/bash
set -e

# Rebuilds the images this stack needs after a `git pull`.
# Run from the repo root (e.g. /opt/virtualTubers on the Portainer host).
#
#   git pull && ./install.sh
#
# None of these images are built by Portainer — every service in
# docker-compose.yml uses `image:` + `pull_policy: never`, never `build:`.
# Portainer's stack working directory (/data/compose/<id>/) only holds the
# compose YAML, not the rest of the repo, so a `build:` block pointing at
# `services/<name>/Dockerfile` fails with "no such file or directory" on
# every deploy. Images must be built here, on the host, then the stack
# redeployed (Portainer UI → Update the stack → Re-pull image and redeploy)
# to pick them up.

cd "$(dirname "${BASH_SOURCE[0]}")"

log() { echo "[install] $*"; }

log "Building vtube-worker:latest"
docker build -t vtube-worker:latest .

log "Building virtualtubers-message-logger:latest"
docker build -t virtualtubers-message-logger:latest -f services/message-logger/Dockerfile .

log "Building virtualtubers-message-api:latest"
docker build -t virtualtubers-message-api:latest -f services/message-api/Dockerfile .

log "Building virtualtubers-log-shipper:latest"
docker build -t virtualtubers-log-shipper:latest -f services/log-shipper/Dockerfile .

log "Done. Redeploy the stack (Portainer: Update the stack → Re-pull image and redeploy)."
