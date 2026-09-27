<#
.SYNOPSIS
    Emergency stop for the virtualTubers stack on gx10 (192.168.1.23).

.DESCRIPTION
    Default (kill-switch mode): SSHes into the prod host and runs
    scripts/emergency_stop.sh (streamed from THIS checkout over ssh stdin, so
    the host's checkout doesn't need to be up to date) which `docker exec`s
    into each worker container and creates its local kill file
    (WORKER_KILL_FILE, default /tmp/worker_disabled). While that file exists
    the worker is OFF regardless of Redis — stream_supervisor.py stops ffmpeg
    within ~0.5s and agent.py pauses — so this works even when Redis or
    message-api is down. Containers keep running; undo with
    emergency_resume.ps1. See docs/worker_control.md.

    -StopStack (legacy mode): `docker compose stop` the whole stack instead
    (NOT `down` — keeps containers/volumes/network so `docker compose start`
    or ./redeploy.sh brings it back). Use when the host itself is overloaded
    and the workers need to stop consuming CPU/RAM, not just go off air.

    Either way, retries every -IntervalSeconds because an overloaded host's
    sshd can itself be flaky; exits as soon as an attempt succeeds.

.PARAMETER Worker
    Worker(s) to stop: service suffix (coder), service (worker-coder) or
    container name. Omit for all running worker-* containers.

.PARAMETER StopStack
    Legacy behaviour: `docker compose stop` the entire stack.

.PARAMETER Resume
    Remove the kill file instead (what emergency_resume.ps1 calls).

.PARAMETER IntervalSeconds
    Seconds between attempts. Default 10.

.PARAMETER MaxAttempts
    Give up after this many attempts. Default 0 (unlimited — keep
    hammering until it succeeds or you Ctrl+C).

.EXAMPLE
    # take every worker off air (containers stay up)
    powershell -ExecutionPolicy Bypass -File .\scripts\emergency_stop.ps1

.EXAMPLE
    .\scripts\emergency_stop.ps1 -Worker coder,gm

.EXAMPLE
    # legacy: stop the whole stack, give up after 30 tries (5 min)
    .\scripts\emergency_stop.ps1 -StopStack -MaxAttempts 30
#>

param(
    [string[]]$Worker = @(),
    [switch]$StopStack,
    [switch]$Resume,
    [int]$IntervalSeconds = 10,
    [int]$MaxAttempts = 0,
    [string]$SshTarget = "secus@192.168.1.23",
    [string]$RemoteDir = "~/codeProjects/virtualTubers"
)

$ScriptPath = Join-Path $PSScriptRoot "emergency_stop.sh"
$ModeArgs = @()
if ($Resume) { $ModeArgs += "--resume" }
# Worker names are passed as bash args; restrict them to safe characters so
# nothing can be injected into the remote command line.
foreach ($w in $Worker) {
    if ($w -notmatch '^[A-Za-z0-9._-]+$') {
        Write-Host "Invalid worker name '$w' (allowed: letters, digits, . _ -)" -ForegroundColor Red
        exit 2
    }
}
$ScriptArgs = ($ModeArgs + $Worker) -join " "

if ($StopStack) {
    $RemoteCmd = "cd $RemoteDir && docker compose stop"
    $Stdin = $null
} else {
    if (-not (Test-Path $ScriptPath)) {
        Write-Host "Missing $ScriptPath" -ForegroundColor Red
        exit 1
    }
    # Strip CRs: a Windows checkout may have converted the .sh to CRLF.
    $Stdin = (Get-Content -Raw $ScriptPath) -replace "`r", ""
    $RemoteCmd = "bash -s -- $ScriptArgs"
    # PS 5.1 pipes native-command stdin as ASCII by default.
    $OutputEncoding = New-Object System.Text.UTF8Encoding $false
}

$attempt = 0
while ($true) {
    $attempt++
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Attempt #$attempt : ssh $SshTarget `"$RemoteCmd`""

    # BatchMode=yes: never hang waiting for a password prompt if key auth
    # fails — fail fast and retry on the next tick instead.
    # ConnectTimeout=8: don't let one hung attempt block the whole loop
    # for longer than the retry interval itself.
    $sshArgs = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                 "-o", "StrictHostKeyChecking=accept-new", $SshTarget, $RemoteCmd)
    if ($null -ne $Stdin) {
        $Stdin | ssh @sshArgs
    } else {
        ssh @sshArgs
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Host "[$timestamp] Succeeded on attempt #$attempt." -ForegroundColor Green
        break
    }

    Write-Host "[$timestamp] Attempt #$attempt failed (exit $LASTEXITCODE). Retrying in ${IntervalSeconds}s..." -ForegroundColor Yellow

    if ($MaxAttempts -gt 0 -and $attempt -ge $MaxAttempts) {
        Write-Host "[$timestamp] Reached MaxAttempts ($MaxAttempts) without success. Giving up." -ForegroundColor Red
        exit 1
    }

    Start-Sleep -Seconds $IntervalSeconds
}
