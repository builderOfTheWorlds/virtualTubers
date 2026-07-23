#Requires -Version 5.1
$ErrorActionPreference = "Stop"

# Rebuilds the images this stack needs after a `git pull`, and fetches the
# Piper voice models for Rerun Theater's spoken narration. This is the
# primary install path — production runs on d2000 (Windows, Docker Desktop,
# plain `docker compose`, no Portainer). No WSL/Git Bash needed. install.sh
# is the bash equivalent, for a Linux/macOS host or a Windows host with
# WSL/Git Bash.
#
# Run from the repo root:
#
#   git pull; .\install.ps1
#
# None of these images are built by `docker compose up` — every service in
# docker-compose.yml uses `image:` + `pull_policy: never`, never `build:`
# (builds stay explicit/scriptable across every service in one pass rather
# than being folded into the compose file). Images must be built here, on
# the host, then the stack recreated (`docker compose up -d`) to pick them
# up.
#
# Whenever a new service is added to the stack, add its build line to BOTH
# this file and install.sh in the same change — see the "Deploy / redeploy"
# section in README.md.

Set-Location -Path $PSScriptRoot

function Log($Message) {
    Write-Host "[install] $Message"
}

function Invoke-ImageBuild {
    param(
        [Parameter(Mandatory)][string]$Tag,
        [string]$Dockerfile
    )
    Log "Building $Tag"
    if ($Dockerfile) {
        docker build -t $Tag -f $Dockerfile .
    } else {
        docker build -t $Tag .
    }
    if ($LASTEXITCODE -ne 0) {
        throw "docker build failed for $Tag (exit code $LASTEXITCODE)"
    }
}

Log "Fetching Piper voice models (voices/)"
$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python -ErrorAction SilentlyContinue
}
if ($python) {
    & $python.Source scripts/download_voices.py --out voices
    if ($LASTEXITCODE -ne 0) {
        Log "WARN: voice download failed - replays will play silent until voices/ is populated (rerun .\install.ps1 once network/host issue is resolved)"
    }
} else {
    Log "WARN: python3/python not found - skipping voice download (see scripts/download_voices.py)"
}

Invoke-ImageBuild -Tag "vtube-worker:latest"
Invoke-ImageBuild -Tag "virtualtubers-message-logger:latest" -Dockerfile "services/message-logger/Dockerfile"
Invoke-ImageBuild -Tag "virtualtubers-message-api:latest" -Dockerfile "services/message-api/Dockerfile"
Invoke-ImageBuild -Tag "virtualtubers-log-shipper:latest" -Dockerfile "services/log-shipper/Dockerfile"
Invoke-ImageBuild -Tag "virtualtubers-twitch-presence:latest" -Dockerfile "services/twitch-presence/Dockerfile"

# --force-recreate, not a plain `up -d`: a container whose only difference is
# an image rebuilt under the same tag looks unchanged to Compose, so `up -d`
# alone would leave stale containers running old code. Recreating also gives
# every worker a clean container filesystem, which matters for anything that
# only initializes correctly on first boot within a container's writable
# layer (e.g. PulseAudio's runtime state — see startup.sh's cleanup comment).
Log "Recreating containers (docker compose up -d --force-recreate)"
docker compose up -d --force-recreate
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up -d --force-recreate failed (exit code $LASTEXITCODE)"
}

Log "Done. Stack recreated and running the freshly built images."
