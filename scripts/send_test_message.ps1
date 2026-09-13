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
    # [string]$Url = "http://192.168.1.120:8090/messages"
    # [string]$Url = "http://192.168.2.158:8090/messages"
    [string]$Url = "http://192.168.2.170:8090/messages"
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
$To      = "coder"
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
# each of TUBER2_LAYOUT_PRESET/TUBER3_LAYOUT_PRESET/
# TUBER4_LAYOUT_PRESET to "replay" in the stack env and redeploy first,
# or this will still refuse with ready_timeout.
# $Payload = '{"episode": "sample", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}, "narration": "reuse"}'

# $Payload = '{"episode": "sample_long", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}}'

# $Payload = '{"episode": "sample_roster", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}, "narration": "reuse"}'

# =====================================================================
# ASHIORID CAMPAIGN - the authored D&D campaign, not a session recording
# =====================================================================
# Built from campaigns/ashiorid_1 by .claude/prompts/build_campaign_episode.py,
# which walks the pack's default_next scene chain and converts beats into
# episode events, then uploads to the episode store:
#
#   .venv/bin/python3 .claude/prompts/build_campaign_episode.py \
#       --pack campaigns/ashiorid_1 --name ashiorid --max-scenes 12 --upload
#
# Re-run that after editing the pack - the library holds a converted SNAPSHOT,
# so scene edits do NOT reach the stream until the episode is rebuilt.
#
# Episode "ashiorid": 85 events / 1955 words / 12 spine scenes.
# Its four speakers are WORKER ids already (the converter maps the campaign
# cast: chadwick->coder, Leena->tester, Vigil->coder-native,
# sodacan_bob->coder-opencode). GM/Ashiorid narration is user_message, so there is
# deliberately no "manager" speaker and no coder-aider role in this episode.

# Ashiorid SOLO - one worker performs every part. Verified airing 2026-08-31.
# $Payload = '{"episode": "ashiorid", "voice": true}'

# Ashiorid DUET - each character speaks on its own avatar. The cast keys are
# the episode's speaker names, which for this episode are already worker ids,
# so each maps to itself. Verified airing to "-- fin --" 2026-08-31.
$Payload = '{"episode": "ashiorid", "cast": {"coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode"}}'



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
