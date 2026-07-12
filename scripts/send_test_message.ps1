<#
.SYNOPSIS
    Posts a test message to the vtuber.messages Kafka topic via the
    message-api HTTP service (POST /messages, port 8090 by default).
    See docs/message_api.md.

    Pick a message by uncommenting exactly one preset section below
    (and commenting out the others).

.EXAMPLE
    .\scripts\send_test_message.ps1

.EXAMPLE
    .\scripts\send_test_message.ps1 -Url http://localhost:8090/messages
#>
param(
    [string]$Url = "http://192.168.1.120:8090/messages"
)


# =====================================================================
# PRESET MESSAGES — uncomment exactly ONE section
# =====================================================================

# --- Coder task assignment: clamp() + pytest tests -------------------
# $To      = "coder"
# $Type    = "task_assignment"
# $Payload = '{"task": "Create a small test program: a clamp(value, low, high) function that limits a value to the [low, high] range, plus pytest tests covering in-range, below-range, and above-range inputs."}'

# --- Broadcast operator message: chat shoutout -----------------------
# $To      = "broadcast"
# $Type    = "operator_message"
# $Payload = '{"message": "Say hello to Phil, hes in the chat right now!"}'

# --- Broadcast operator message: stream starting ---------------------
# $To      = "broadcast"
# $Type    = "operator_message"
# $Payload = '{"text": "stream starting in 5"}'

# --- Coder replay request: reenact a saved episode --------------------
$To      = "coder"
$Type    = "replay_request"

# Large episode for testing, 2026-06-26_05-00-31_ea5049d8, 1.5MB
# $Payload = '{"episode": "2026-06-26_05-00-31_ea5049d8"}' 

# Smaller episode for testing, 2026-07-05_18-09-43_e2d3d72b, 0.5MB
$Payload = '{"episode": "2026-07-05_18-09-43_e2d3d72b"}'




# =====================================================================

try {
    $payloadObj = $Payload | ConvertFrom-Json
} catch {
    Write-Error "Invalid payload JSON: $_"
    exit 1
}

$body = @{
    to      = $To
    type    = $Type
    payload = $payloadObj
} | ConvertTo-Json -Depth 10

Write-Host "POST $Url  (to=$To, type=$Type)"

try {
    $response = Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body
} catch {
    Write-Error "Request to $Url failed: $_"
    exit 1
}

$response | ConvertTo-Json -Depth 10
