#Requires -Version 5.1
$ErrorActionPreference = "Stop"

# Windows/PowerShell equivalent of install.sh — rebuilds the images this stack
# needs after a `git pull`, and fetches the Piper voice models for Rerun
# Theater's spoken narration. For hosts with Docker Desktop but no bash
# (no WSL/Git Bash needed).
#
# Run from the repo root:
#
#   git pull; .\install.ps1
#
# None of these images are built by Portainer (or pulled by plain `docker
# compose up`) — every service in docker-compose.yml uses `image:` +
# `pull_policy: never`, never `build:`. Images must be built here, on the
# host, then the stack redeployed (Portainer UI -> Update the stack ->
# Re-pull image and redeploy, or `docker compose up -d` for a plain Compose
# host) to pick them up.
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

Log "Done. Redeploy the stack (Portainer: Update the stack -> Re-pull image and redeploy), or 'docker compose up -d' for a plain Compose host."
