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
# A preset MAY also set TO2/TYPE2/PAYLOAD2 to fire a second POST right
# after the first. This exists because the roundtable channel (tuber_0)
# and the six individual character channels are two separate audiences
# that no single replay_request can address at once: a request "to" a
# character worker (e.g. "coder") fans out over Kafka to the OTHER
# character workers only, while a request "to" tuber_0 resolves every
# cast slot to a LOCAL tile and never touches Kafka at all
# (app/replay_pane.py _resolve_local_tiles) - so tuber_0/roundtable
# never joins a character-directed duet, and the six character channels
# never join a tuber_0-directed one. Firing both airs the same episode
# on all seven channels; see docs/duet_replay.md and
# .claude/prompts/roundtable_stream_design.md SS2.2 WP-7 (identity-rename
# migration that would let one request address both, not yet run in
# this deployment).
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

# Optional second POST (roundtable companion request - see header comment
# above). Left unset by presets that don't need it; reset here so a stale
# value can't leak in the same way TO/TYPE/PAYLOAD can't.
TO2=""
TYPE2=""
PAYLOAD2=""

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
#
# Roundtable companion (see header comment above): SAME episode, SAME
# speaker names, but addressed to tuber_0 so the GM's local tiles
# (config/workers/tuber_0.yaml roster: tuber_1=Chadwick/coder,
# tuber_2=Vigil/coder-native, tuber_3=Sodacan Bob/coder-opencode,
# tuber_5=Leena/tester, tuber_6=MAX-1/manager) light up too. manager IS
# mapped here (unlike the request above) so its narrator lines get
# MAX-1's own tile instead of falling to the roundtable director's own
# uncast-speaker fallback. Fired right after the request above so both
# audiences get the same show - keep this episode name in sync with
# PAYLOAD's above by hand; nothing enforces it automatically.
# TO2="tuber_0"
# TYPE2="replay_request"
# PAYLOAD2='{"episode": "ashiorid", "cast": {"coder": "tuber_1", "coder-native": "tuber_2", "coder-opencode": "tuber_3", "tester": "tuber_5"}}'

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

# Roundtable companion (see header comment above): SAME episode, SAME
# speaker names, but addressed to tuber_0 so the GM's local tiles
# (config/workers/tuber_0.yaml roster: tuber_1=Chadwick/coder,
# tuber_2=Vigil/coder-native, tuber_3=Sodacan Bob/coder-opencode,
# tuber_5=Leena/tester, tuber_6=MAX-1/manager) light up too. manager IS
# mapped here (unlike the request above) so its narrator lines get
# MAX-1's own tile instead of falling to the roundtable director's own
# uncast-speaker fallback. Fired right after the request above so both
# audiences get the same show - keep this episode name in sync with
# PAYLOAD's above by hand; nothing enforces it automatically.
TO2="tuber_0"
TYPE2="replay_request"
PAYLOAD2='{"episode": "ashiorid_generated_ce8d", "cast": {"coder": "tuber_1", "coder-native": "tuber_2", "coder-opencode": "tuber_3", "tester": "tuber_5", "manager": "tuber_6"}}'

# =====================================================================

if [[ -z "$TO" || -z "$TYPE" || -z "$PAYLOAD" ]]; then
    echo "Error: No preset selected: uncomment exactly one preset section, including its TO/TYPE lines." >&2
    exit 1
fi

send_test_message() {
    local url="$1" to="$2" type="$3" payload="$4"

    if ! echo "$payload" | jq empty >/dev/null 2>&1; then
        echo "Error: Invalid payload JSON: $payload" >&2
        exit 1
    fi

    local body
    body=$(jq -n --arg to "$to" --arg type "$type" --argjson payload "$payload" \
        '{to: $to, type: $type, payload: $payload}')

    echo "POST $url  (to=$to, type=$type)"

    local response
    if ! response=$(curl -sS -f -X POST "$url" -H "Content-Type: application/json" -d "$body"); then
        echo "Error: Request to $url failed." >&2
        exit 1
    fi

    echo "$response" | jq .
}

send_test_message "$URL" "$TO" "$TYPE" "$PAYLOAD"

if [[ -n "$TO2" && -n "$TYPE2" && -n "$PAYLOAD2" ]]; then
    send_test_message "$URL" "$TO2" "$TYPE2" "$PAYLOAD2"
fi
