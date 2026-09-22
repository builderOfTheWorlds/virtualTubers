<#
.SYNOPSIS
    Emergency stop for the virtualTubers stack on gx10 (192.168.1.23).

.DESCRIPTION
    Repeatedly SSHes into the prod host and runs `docker compose stop`
    (NOT `down` — keeps containers/volumes/network intact so a normal
    `docker compose up -d` or ./redeploy.sh brings it back later) to
    kill the overloaded workers. Retries every 10s because an
    overloaded host's sshd can itself be flaky/slow to respond.
    Exits as soon as a stop attempt succeeds, or keeps trying forever
    if you don't pass -MaxAttempts.

.PARAMETER IntervalSeconds
    Seconds between attempts. Default 10.

.PARAMETER MaxAttempts
    Give up after this many attempts. Default 0 (unlimited — keep
    hammering until it succeeds or you Ctrl+C).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\emergency_stop.ps1

.EXAMPLE
    # give up after 30 tries (5 min) instead of running forever
    .\scripts\emergency_stop.ps1 -MaxAttempts 30
#>

param(
    [int]$IntervalSeconds = 10,
    [int]$MaxAttempts = 0
)

$SshTarget = "secus@192.168.1.23"
$RemoteDir = "~/codeProjects/virtualTubers"
# `docker compose stop` (not `down`): stops containers but leaves them,
# their volumes, and the network in place so a later `docker compose
# start` / `./redeploy.sh` brings the stack back without re-creating
# anything.
$RemoteCmd = "cd $RemoteDir && docker compose stop"

$attempt = 0
while ($true) {
    $attempt++
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Attempt #$attempt : ssh $SshTarget `"$RemoteCmd`""

    # BatchMode=yes: never hang waiting for a password prompt if key auth
    # fails — fail fast and retry on the next tick instead.
    # ConnectTimeout=8: don't let one hung attempt block the whole loop
    # for longer than the retry interval itself.
    ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new `
        $SshTarget $RemoteCmd

    if ($LASTEXITCODE -eq 0) {
        Write-Host "[$timestamp] Stack stopped successfully on attempt #$attempt." -ForegroundColor Green
        break
    }

    Write-Host "[$timestamp] Attempt #$attempt failed (exit $LASTEXITCODE). Retrying in ${IntervalSeconds}s..." -ForegroundColor Yellow

    if ($MaxAttempts -gt 0 -and $attempt -ge $MaxAttempts) {
        Write-Host "[$timestamp] Reached MaxAttempts ($MaxAttempts) without success. Giving up." -ForegroundColor Red
        exit 1
    }

    Start-Sleep -Seconds $IntervalSeconds
}
