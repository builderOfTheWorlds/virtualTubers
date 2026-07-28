<#
.SYNOPSIS
    Posts a test message to the vtuber.messages Kafka topic via the
    message-api HTTP service (POST /messages, port 8090 by default).
    See docs/message_api.md.

    Pick a message by uncommenting exactly one preset section below
    (and commenting out the others).

    NOTE: this file is UTF-8 without a BOM, which PowerShell 5.1 reads as
    the system ANSI codepage. A non-ASCII character (em dash, curly quote,
    etc.) inside a double-quoted string can decode to a different
    character and break the string, cascading into confusing parse errors
    several lines later. Keep double-quoted string literals ASCII-only.

.EXAMPLE
    .\scripts\send_test_message.ps1

.EXAMPLE
    .\scripts\send_test_message.ps1 -Url http://localhost:8090/messages
#>
param(
    [string]$Url = "http://192.168.1.120:8090/messages"
    # [string]$Url = "http://192.168.2.158:8090/messages"
)

# Reset preset variables so stale values can't leak in from the console
# session (VSCode's F5 dot-sources this script - a leftover $Type from an
# earlier run once sent a replay_request out as viewer_joined).
$To      = $null
$Type    = $null
$Payload = $null

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

# --- Viewer joined: fake a Twitch viewer arriving (docs/twitch_presence.md)
#     Normally sent automatically by the twitch-presence service; inject
#     manually to test the on-stream greeting without Twitch.
# $To      = "coder"
# $Type    = "viewer_joined"
# $Payload = '{"username": "deezzzz", "channel": "mycoderchannel"}'

# --- Coder replay request: reenact a saved episode --------------------
# NOTE (2026-07-27, blank-worker fleet): $To and every "cast" value below
# must be the literal WORKER_ID (worker-1..worker-8), NOT the persona name.
# Message routing (app/message_bus.py's poll_new) matches a message's `to`
# field against each container's own WORKER_ID env var verbatim -- there is
# no persona-name alias resolution anywhere in the pipeline. Persona names
# only exist after POST /campaigns/coder/start assigns them to worker ids
# (docs/campaign_control.md); find the current mapping with
# GET /campaigns/active. The old $To = "coder" presets below predate
# the worker-1..8 migration and will silently go nowhere (message-logger
# still logs them, but no worker's consumer filter matches) until rewritten
# to the assigned worker id, same as the fix made to the roundtable preset.
$To      = "worker-1"
$Type    = "replay_request"

# Test small size
# $Payload = '{"episode": "2026-07-01_17-25-00_f4268f99", "narration": "reuse"}'

# Test medium sized
# $Payload = '{"episode": "2026-07-01_04-40-28_b569358b", "narration": "reuse"}'

# Long test
# $Payload = '{"episode": "2026-07-12_21-42-20_462f5abc", "narration": "reuse"}'

# Real 2-speaker duet (old default) - real recorded episode, boss/coder only
# $Payload = '{"episode": "2026-07-02_04-27-00_6ecdde82", "cast": {"boss": "manager", "coder": "coder"},  "narration": "reuse"}'

# 3-worker duet: sample fixture, boss/coder/tester (matches scripts/worker3.json).
# All three are duet-capable in docker-compose.yml today (LAYOUT_PRESET=replay,
# POSTGRES_*, replay library mount) - see scripts/duet_test_payloads.md.
# $Payload = '{"episode": "sample", "cast": {"boss": "manager", "coder": "coder", "tester": "tester"}, "narration": "reuse"}'
# $Payload = '{"episode": "sample", "cast": {"boss": "manager", "coder": "coder", "tester": "tester"}}'

# Multi-speaker sample fixture: full 6-persona duet fan-out (matches scripts/worker6.json,
# see replays/sample.json and docs/duet_replay.md). All six workers are now
# duet-capable in docker-compose.yml (LAYOUT_PRESET/POSTGRES_*/mounts) - set
# each of CODER_NATIVE_LAYOUT_PRESET/CODER_OPENCODE_LAYOUT_PRESET/
# CODER_AIDER_LAYOUT_PRESET to "replay" in the stack env and redeploy first,
# or this will still refuse with ready_timeout.
# $Payload = '{"episode": "sample", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}, "narration": "reuse"}'

# Harry Potter Ch. 1 "Doorstep Delivery" hand-authored 4-persona duet
# (matches scripts/harry_potter_ch1.json): narrator -> boss/manager,
# Dumbledore -> coder, McGonagall -> tester, Hagrid -> coder-native.
# No "narration": "reuse" on first run - add it once this episode has
# aired at least once and got cached to Postgres.
# $Payload = '{"episode": "harry_potter_ch1_doorstep_delivery", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native"}}'

# $Payload = '{"episode": "sample_long", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}}'

# $Payload = '{"episode": "sample_roster", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}, "narration": "reuse"}'



# $Payload = '{"episode": "2026-07-08_03-15-03_640f9d57", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}, "narration": "reuse"}'


# 6-worker campaign-platform test story (replays/coder/test_worker_roundtable.json):
# hand-authored episode where every persona speaks under its own id
# (manager/coder/coder-native/coder-opencode/coder-aider/tester). cast maps
# each episode speaker id to the WORKER_ID currently holding that persona
# (assigned via POST /campaigns/coder/start -- see the NOTE above and
# docker-compose.coder.yml's documented worker-1..6 convention). Never aired
# before, so no "narration": "reuse" on this first run - add it once it's
# aired once and gotten cached to Postgres.
$Payload = '{"episode": "test_worker_roundtable", "cast": {"manager": "worker-5", "coder": "worker-1", "coder-native": "worker-2", "coder-opencode": "worker-3", "coder-aider": "worker-4", "tester": "worker-6"}}'


# $Payload = '{
#     "episode": "harry_potter_ch1_doorstep_delivery",
#     "cast": {
#       "boss": "manager",
#       "coder": "coder",
#       "tester": "tester",
#       "coder-native": "coder-native"
#     }
#   }'







# =====================================================================

if (-not $To -or -not $Type -or -not $Payload) {
    Write-Error "No preset selected: uncomment exactly one preset section, including its `$To/`$Type lines."
    exit 1
}

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
