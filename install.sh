#!/bin/bash
set -e

# Bash equivalent of install.ps1 — rebuilds the images this stack needs
# after a `git pull`, and fetches the Piper voice models for Rerun
# Theater's spoken narration. Production runs on d2000 (Windows, Docker
# Desktop) via install.ps1; use this instead on a Linux/macOS host, or a
# Windows host with WSL/Git Bash.
# Run from the repo root:
#
#   git pull && ./install.sh
#
# None of these images are built by `docker compose up` — every service in
# docker-compose.yml uses `image:` + `pull_policy: never`, never `build:`
# (builds stay explicit/scriptable across every service in one pass rather
# than being folded into the compose file). Images must be built here, on
# the host, then the stack recreated (`docker compose up -d`) to pick them
# up.

cd "$(dirname "${BASH_SOURCE[0]}")"

log() { echo "[install] $*"; }

log "Fetching Piper voice models (voices/)"
if command -v python3 >/dev/null 2>&1; then
    python3 scripts/download_voices.py --out voices \
        || log "WARN: voice download failed — replays will play silent until voices/ is populated (rerun ./install.sh once network/host issue is resolved)"
else
    log "WARN: python3 not found — skipping voice download (see scripts/download_voices.py)"
fi

log "Building vtube-worker:latest"
docker build -t vtube-worker:latest .

log "Building virtualtubers-message-logger:latest"
docker build -t virtualtubers-message-logger:latest -f services/message-logger/Dockerfile .

log "Building virtualtubers-message-api:latest"
docker build -t virtualtubers-message-api:latest -f services/message-api/Dockerfile .

log "Building virtualtubers-log-shipper:latest"
docker build -t virtualtubers-log-shipper:latest -f services/log-shipper/Dockerfile .

log "Building virtualtubers-twitch-presence:latest"
docker build -t virtualtubers-twitch-presence:latest -f services/twitch-presence/Dockerfile .

log "Done. Redeploy the stack: docker compose up -d"
