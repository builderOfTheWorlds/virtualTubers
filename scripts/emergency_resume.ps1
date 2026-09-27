<#
.SYNOPSIS
    Undo emergency_stop.ps1: remove the local kill file from worker container(s)
    on the prod host so they return to Redis on/off control.

.DESCRIPTION
    Thin wrapper around emergency_stop.ps1 -Resume (same SSH/retry handling).
    Removing the kill file does NOT force a worker on: it streams again only
    if its Redis flag (worker:<id>:enabled) isn't "0" — or Redis is down, in
    which case the normal fail-open rule applies. Idempotent.
    Does not undo -StopStack; for that run `docker compose start` on the host.

.EXAMPLE
    .\scripts\emergency_resume.ps1

.EXAMPLE
    .\scripts\emergency_resume.ps1 -Worker coder
#>
param(
    [string[]]$Worker = @(),
    [int]$IntervalSeconds = 10,
    [int]$MaxAttempts = 0,
    [string]$SshTarget = "secus@192.168.1.23"
)

& (Join-Path $PSScriptRoot "emergency_stop.ps1") -Resume -Worker $Worker `
    -IntervalSeconds $IntervalSeconds -MaxAttempts $MaxAttempts -SshTarget $SshTarget
exit $LASTEXITCODE
