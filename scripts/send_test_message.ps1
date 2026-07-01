<#
.SYNOPSIS
    Posts a test message to the vtuber.messages Kafka topic via the
    message-api HTTP service (POST /messages, port 8090 by default).
    See docs/message_api.md.

.EXAMPLE
    .\scripts\send_test_message.ps1

.EXAMPLE
    .\scripts\send_test_message.ps1 -To broadcast -Type operator_message -Payload '{"text":"stream starting in 5"}'
#>
param(
    [string]$Url = "http://192.168.1.120:8090/messages",
    [string]$To = "coder",
    [string]$Type = "task_assignment",
    [string]$Payload = '{"task": "Say hello to the stream and introduce yourself. Then list the files in the current directory.*"}'
)

try {
    $payloadObj = $Payload | ConvertFrom-Json
} catch {
    Write-Error "Invalid -Payload JSON: $_"
    exit 1
}

$body = @{
    to      = $To
    type    = $Type
    payload = $payloadObj
} | ConvertTo-Json -Depth 10

try {
    $response = Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body
} catch {
    Write-Error "Request to $Url failed: $_"
    exit 1
}

$response | ConvertTo-Json -Depth 10
