<#
.SYNOPSIS
    Stops a running (or queued) Rerun Theater replay on demand by posting a
    replay_stop message to the vtuber.messages Kafka topic via the
    message-api HTTP service (POST /messages, port 8090 by default).
    See docs/operator_commands.md and docs/replay_pane.md.

    Unlike send_test_message.ps1's preset library, this takes -To directly
    since "stop it now" needs to be a single command, not an edit-then-run.

.EXAMPLE
    .\scripts\stop_replay.ps1
    Stops any replay running or queued on ANY worker (default -To broadcast).

.EXAMPLE
    .\scripts\stop_replay.ps1 -To coder

.EXAMPLE
    .\scripts\stop_replay.ps1 -To coder -Url http://localhost:8090/messages
#>
param(
    [string]$To = "broadcast",
    [string]$Url = "http://192.168.1.120:8090/messages"
)

$body = @{
    to      = $To
    type    = "replay_stop"
    payload = @{}
} | ConvertTo-Json -Depth 10

Write-Host "POST $Url  (to=$To, type=replay_stop)"

try {
    $response = Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body
} catch {
    Write-Error "Request to $Url failed: $_"
    exit 1
}

$response | ConvertTo-Json -Depth 10
