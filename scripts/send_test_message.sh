#!/usr/bin/env bash
#
# send_test_message.sh
#
# Posts a test message to the vtuber.messages Kafka topic via the
# message-api HTTP service (POST /messages, port 8090 by default).
# See docs/message_api.md.
#
# Pick a message by uncommenting exactly one preset section below
# (and commenting out the others).
#
# Requires: curl, jq
#
# Usage:
#   ./scripts/send_test_message.sh
#   ./scripts/send_test_message.sh http://localhost:8090/messages
#
set -euo pipefail

# URL="${1:-http://192.168.2.170:8090/messages}"
URL="${1:-http://192.168.1.23:8090/messages}"
# URL="${1:-http://192.168.1.120:8090/messages}"
# URL="${1:-http://192.168.2.158:8090/messages}"

for cmd in curl jq; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Error: required command '$cmd' not found on PATH." >&2
        exit 1
    fi
done

# Reset preset variables so stale values can't leak in from a sourced shell.
TO=""
TYPE=""
PAYLOAD=""

# =====================================================================
# PRESET MESSAGES — uncomment exactly ONE section
# =====================================================================

# --- Coder task assignment: clamp() + pytest tests -------------------
# TO="coder"
# TYPE="task_assignment"
# PAYLOAD='{"task": "Create a small test program: a clamp(value, low, high) function that limits a value to the [low, high] range, plus pytest tests covering in-range, below-range, and above-range inputs."}'

# --- Broadcast operator message: chat shoutout -----------------------
# TO="broadcast"
# TYPE="operator_message"
# PAYLOAD='{"message": "Say hello to Phil, hes in the chat right now!"}'

# --- Broadcast operator message: stream starting ---------------------
# TO="broadcast"
# TYPE="operator_message"
# PAYLOAD='{"text": "stream starting in 5"}'

# --- Viewer joined: fake a Twitch viewer arriving (docs/twitch_presence.md)
#     Normally sent automatically by the twitch-presence service; inject
#     manually to test the on-stream greeting without Twitch.
# TO="coder"
# TYPE="viewer_joined"
# PAYLOAD='{"username": "deezzzz", "channel": "mycoderchannel"}'

# --- Coder replay request: reenact a saved episode --------------------
TO="coder"
TYPE="replay_request"

# Test small size
# PAYLOAD='{"episode": "2026-07-01_17-25-00_f4268f99", "narration": "reuse"}'

# Test medium sized
# PAYLOAD='{"episode": "2026-07-01_04-40-28_b569358b", "narration": "reuse"}'

# Long test
# PAYLOAD='{"episode": "2026-07-12_21-42-20_462f5abc", "narration": "reuse"}'

# Real 2-speaker duet (old default) - real recorded episode, boss/coder only
# PAYLOAD='{"episode": "2026-07-02_04-27-00_6ecdde82", "cast": {"boss": "manager", "coder": "coder"},  "narration": "reuse"}'

# 3-worker duet: sample fixture, boss/coder/tester (matches scripts/worker3.json).
# All three are duet-capable in docker-compose.yml today (LAYOUT_PRESET=replay,
# POSTGRES_*, replay library mount) - see scripts/duet_test_payloads.md.
# PAYLOAD='{"episode": "sample", "cast": {"boss": "manager", "coder": "coder", "tester": "tester"}, "narration": "reuse"}'
# PAYLOAD='{"episode": "sample", "cast": {"boss": "manager", "coder": "coder", "tester": "tester"}}'

# Multi-speaker sample fixture: full 6-persona duet fan-out (matches scripts/worker6.json,
# see replays/sample.json and docs/duet_replay.md). All six workers are now
# duet-capable in docker-compose.yml (LAYOUT_PRESET/POSTGRES_*/mounts) - set
# each of TUBER2_LAYOUT_PRESET/TUBER3_LAYOUT_PRESET/
# TUBER4_LAYOUT_PRESET to "replay" in the stack env and redeploy first,
# or this will still refuse with ready_timeout.
# PAYLOAD='{"episode": "sample", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}, "narration": "reuse"}'

# PAYLOAD='{"episode": "sample_long", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}}'

# PAYLOAD='{"episode": "sample_roster", "cast": {"boss": "manager", "coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode", "coder-aider": "coder-aider"}, "narration": "reuse"}'

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
# PAYLOAD='{"episode": "ashiorid", "voice": true}'

# Ashiorid DUET - each character speaks on its own avatar. The cast keys are
# the episode's speaker names, which for this episode are already worker ids,
# so each maps to itself. Verified airing to "-- fin --" 2026-08-31.
# PAYLOAD='{"episode": "ashiorid", "cast": {"coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode"}}'

# =====================================================================
# ASHIORID GENERATED - 3-layer generator output, NOT the authored pack
# =====================================================================
# Built from generator run ashiorid_1_20260913_180158_ce8d (arc job
# job_20260913T180158_66d6a7 / segment job job_20260913T194943_2d5ff1 /
# dialogue job job_20260913T195715_f66367: 73 dialogue takes, 0 failed,
# across 13 of 15 planned segments - 2 segments produced zero content,
# see Gitea issue #15) by:
#
#   .venv/bin/python3 .claude/prompts/build_generated_episode.py \
#       --run ashiorid_1_20260913_180158_ce8d --name ashiorid_generated_ce8d --upload
#
# Re-run that (new --name) after generating a new run - this converter has
# no relationship to build_campaign_episode.py's authored-pack episodes.
#
# Episode "ashiorid_generated_ce8d": 346 events / 6353 words. Content quality
# is visibly rougher than the authored "ashiorid" episode above - see the
# converter's module docstring for the specific junk patterns it strips
# (meta-filler lines, unreliable narration/dialogue kind labelling). Same
# 4-worker duet cast as the authored episode (Chadwick/Leena/Vigil/Sodacan
# Bob -> coder/tester/coder-native/coder-opencode; Ashiorid/manager narrates
# any line the converter couldn't attribute to a mapped speaker).
PAYLOAD='{"episode": "ashiorid_generated_ce8d", "cast": {"coder": "coder", "tester": "tester", "coder-native": "coder-native", "coder-opencode": "coder-opencode"}}'

# =====================================================================

if [[ -z "$TO" || -z "$TYPE" || -z "$PAYLOAD" ]]; then
    echo "Error: No preset selected: uncomment exactly one preset section, including its TO/TYPE lines." >&2
    exit 1
fi

if ! echo "$PAYLOAD" | jq empty >/dev/null 2>&1; then
    echo "Error: Invalid payload JSON: $PAYLOAD" >&2
    exit 1
fi

BODY=$(jq -n --arg to "$TO" --arg type "$TYPE" --argjson payload "$PAYLOAD" \
    '{to: $to, type: $type, payload: $payload}')

echo "POST $URL  (to=$TO, type=$TYPE)"

if ! RESPONSE=$(curl -sS -f -X POST "$URL" -H "Content-Type: application/json" -d "$BODY"); then
    echo "Error: Request to $URL failed." >&2
    exit 1
fi

echo "$RESPONSE" | jq .
