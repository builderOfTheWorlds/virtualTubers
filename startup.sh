#!/bin/bash
set -e

CONFIG_PATH="${CONFIG_PATH:-/config/worker.yaml}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
DISPLAY=":${DISPLAY_NUM}"
RESOLUTION="${RESOLUTION:-1920x1080}"
# Capture resolution (docs/tuber_base_layout_plan.md Contract E): Xvfb/xterm
# render at this size — bigger than the stream OUTPUT (RESOLUTION above) lets
# more detail fit on screen before stream_supervisor.py's ffmpeg -vf scale
# step scales it down. Defaults to 2560x1440 (scaled down from 4K
# 2026-09-22 — 6 concurrent 4K x11grab captures were saturating host
# memory bandwidth, the real cause of a ~6-9fps Twitch stream even with
# GPU rendering/NVENC/CBR all confirmed working in isolation; see
# docker-compose.yml's CAPTURE_RESOLUTION and stream_supervisor.py);
# RESOLUTION stays the unchanged 1920x1080 default stream output.
CAPTURE_RESOLUTION="${CAPTURE_RESOLUTION:-2560x1440}"
FONT_SIZE="${FONT_SIZE:-20}"
STREAM_RTMP_URL="${STREAM_RTMP_URL:-rtmp://localhost:1935/live}"
STREAM_KEY="${STREAM_KEY:-test}"

# Pixel dimensions of the capture, derived from CAPTURE_RESOLUTION (e.g.
# 3840x2160) — NOT from RESOLUTION, which is the stream's OUTPUT size and is
# passed to stream_supervisor.py separately below.
VW="${CAPTURE_RESOLUTION%x*}"
VH="${CAPTURE_RESOLUTION#*x}"

log() { echo "[startup] $*"; }

# ── 1. Clean up stale Xvfb lock from previous run ─────────────────────────────
rm -f "/tmp/.X${DISPLAY_NUM}-lock"
rm -f "/tmp/.X11-unix/X${DISPLAY_NUM}"

# ── 2. Virtual display ─────────────────────────────────────────────────────────
log "Starting Xvfb on display ${DISPLAY}"
Xvfb "${DISPLAY}" -screen 0 "${CAPTURE_RESOLUTION}x24" -ac +extension GLX &
XVFB_PID=$!
export DISPLAY
sleep 2

# ── 3. PulseAudio (system mode for root) ──────────────────────────────────────
# No --disallow-module-loading: that flag rejects exactly the kind of
# runtime `pactl load-module` call made right below to create the "vout"
# null sink — with it set, that load always failed ("Module initialization
# failed"), which is why narration audio never made it to the stream even
# after fixing the pulse-access group membership.
#
# Stale runtime state cleanup, same reason as the Xvfb lock removal above:
# a plain container restart (crash + restart policy, or `docker restart`) —
# unlike a full recreate — keeps the writable layer, so a PID file/socket
# left behind by the PREVIOUS pulseaudio process (killed uncleanly on
# shutdown) makes this run's `pulseaudio --system` fail ("Daemon startup
# failed"), and every client (paplay, pactl, ffmpeg's `-f pulse` input) then
# gets ECONNREFUSED against the dead socket. Both failures are swallowed
# (`|| true` here; audio_player.py never checks paplay's exit code either),
# so a worker that has restarted even once can lose narration/stream audio
# permanently with no error anywhere else in the pipeline.
pkill -9 pulseaudio 2>/dev/null || true
rm -rf /var/run/pulse /run/pulse /tmp/pulse-*
log "Starting PulseAudio"
pulseaudio --system --disallow-exit --daemonize=true || true
sleep 1
# Not `2>/dev/null || true` (the previous version): a failure here — e.g. the
# image's root user missing from the "pulse-access" group PulseAudio's
# system-mode gates connections on — used to be completely invisible, and
# silently meant narration audio could never reach the stream (paplay and
# ffmpeg's `-f pulse` input hit the same access check). Non-fatal either way
# (`if`, not `set -e`), but now it says which one happened.
if SINK_OUTPUT=$(pactl load-module module-null-sink sink_name=vout sink_properties=device.description=VirtualOut 2>&1); then
    log "PulseAudio null sink 'vout' ready (module id ${SINK_OUTPUT})"
else
    log "WARNING: could not create PulseAudio null sink 'vout': ${SINK_OUTPUT} — narration audio will stream silent (docs/stream_supervisor.md)"
fi

# PulseAudio's default system.pa loads module-suspend-on-idle, which parks
# ("SUSPENDED") any sink with no active stream after a couple of seconds.
# The roundtable channel's paplay calls are bursty by design (one line, then
# silence until the next cue) — vout sits idle between lines, gets suspended,
# and the FIRST paplay after that gap has to physically resume the sink
# before ffmpeg's `-f pulse -i vout.monitor` capture sees any samples. That
# resume isn't instant: it consistently ate the leading ~0.25-0.5s of every
# new line, so the audience heard dialogue start mid-word. Unloading
# suspend-on-idle keeps vout RUNNING permanently once created, at the cost
# of a few extra idle watts inside the container — a fair trade for a
# broadcast where every line is heard from its first syllable. Best-effort:
# module-suspend-on-idle may already be gone on some base images.
if pactl unload-module module-suspend-on-idle 2>/dev/null; then
    log "Disabled PulseAudio suspend-on-idle (keeps 'vout' from clipping the start of each line)"
else
    log "module-suspend-on-idle not loaded (nothing to disable) — continuing"
fi

# ── 4+5. Tmux session + panes (config-driven) ─────────────────────────────────
# The layout engine resolves config/layouts/<preset>.yaml + config/panels/*.yaml,
# writes each pane's resolved config to /tmp/panes/<id>.yaml, and emits the tmux
# command sequence (new-session named "worker" + splits + titles + send-keys).
# Reorder/resize/disable a pane by editing config only — no change here.
# SESSION must match the session name the engine creates (hardcoded "worker");
# it is reused below for the xterm attach and the window-size refresh.
SESSION="worker"
log "Building tmux layout from ${CONFIG_PATH}"
# Build the tmux layout with errexit DISABLED: the emitted script is a sequence
# of tmux commands, and a single one returning non-zero (e.g. an option the
# container's tmux version rejects) must NOT abort startup before the ffmpeg
# broadcaster below. new-session/splits/send-keys run first regardless.
set +e
LAYOUT_SCRIPT="$(python3 /app/build_layout.py --config "${CONFIG_PATH}")"
BUILD_RC=$?
eval "${LAYOUT_SCRIPT}"
set -e
[ "${BUILD_RC}" -eq 0 ] || log "build_layout.py exited ${BUILD_RC} — continuing to broadcaster"

# ── 6. Open a borderless, full-screen xterm on the virtual display ────────────
# No window manager: a decorated window (title bar + borders) would inset the
# terminal and leave black margins in the capture. Running xterm undecorated and
# sizing it to the exact display dimensions makes it fill the whole 1920x1080 frame.
log "Opening xterm (${VW}x${VH}, font ${FONT_SIZE})"
DISPLAY="${DISPLAY}" xterm \
    -fa 'Monospace' -fs "${FONT_SIZE}" \
    -b 0 -bw 0 \
    -geometry "+0+0" \
    -bg '#2b2b2b' -fg '#e6edf3' \
    -e "tmux attach -t ${SESSION}" &
XTERM_PID=$!
sleep 2

log "Sizing xterm to fill ${VW}x${VH}"
# Match by PID, not `--class xterm`: xterm's WM_CLASS class field is "XTerm"
# (capitalized), so a case-sensitive --class match against lowercase "xterm"
# silently finds nothing, leaves $WID empty, and the window is never resized
# off its small default — it just sits in the corner of the captured frame.
WID=$(DISPLAY="${DISPLAY}" xdotool search --sync --pid "${XTERM_PID}" | head -1)
if [ -n "${WID}" ]; then
    DISPLAY="${DISPLAY}" xdotool windowmove "$WID" 0 0
    DISPLAY="${DISPLAY}" xdotool windowsize "$WID" "$VW" "$VH"
else
    log "WARNING: could not find xterm window (pid ${XTERM_PID}) to resize; capture may not fill ${VW}x${VH}"
fi
sleep 1

# xterm recomputes its cell grid to fill the window; make tmux follow the new
# client size and redraw so its panes expand to the full frame (no fixed 240x67 box).
DISPLAY="${DISPLAY}" tmux set -g window-size latest \; refresh-client -t "${SESSION}" 2>/dev/null || true

# ── 6.5 Voice registry inventory check (V2) ───────────────────────────────────
# roundtable_stream_design.md v1.1 §8.2. A show header names voices symbolically
# (e.g. "alto_bright"); message-api validates that the NAME is known at upload,
# but it has no /data/voices mount and so cannot check the asset. This is the
# tier that can: it confirms every registry entry's model file exists and
# actually loads.
#
# Why this runs at boot and says so loudly: the director's voice preparation is
# best-effort and returns None on failure, which the duet refusal contract turns
# into "the show does not air on ANY channel" — with an error that does not name
# the offending voice. Discovering that at air time is expensive; discovering it
# here is free.
#
# Non-fatal by design, exactly like the PulseAudio sink above: a worker with one
# bad voice should still boot, broadcast, and perform every show that doesn't
# cast it.
if [ -f /app/voice_registry.py ]; then
    log "Verifying voice registry (V2 inventory check)"
    if VOICE_CHECK=$(python3 /app/voice_registry.py --verify 2>&1); then
        log "Voice registry OK: $(echo "${VOICE_CHECK}" | tail -1)"
    else
        log "WARNING: voice registry verification FAILED — a show casting a failed"
        log "         voice will refuse to air on EVERY channel (design §8.2):"
        echo "${VOICE_CHECK}" | while IFS= read -r line; do log "         ${line}"; done
    fi
fi

# ── 7. Agent loop ─────────────────────────────────────────────────────────────
log "Starting agent loop"
python3 /app/agent.py --config "${CONFIG_PATH}" &
AGENT_PID=$!

# ── 7.5 Roundtable director (headless) ─────────────────────────────────────────
# app/replay_pane.py is not merely a display: running it IS how any
# replay_request ever actually airs (perform_request / perform_director_request
# in app/replay_pane.py poll the request file the agent writes). Every layout
# used to host it as a visible "Rerun Theater" tmux pane (config/panels/
# replay.yaml), so removing that pane from a layout (e.g. the roundtable
# preset's v1.3 pure-tile-grid pass) silently stops the SHOW from ever airing
# at all — replay_request gets queued to a file nobody polls, with no error
# anywhere (confirmed live: only tile_pane.py/agent.py/stream_supervisor.py
# processes running, zero replay_pane.py). Launching it here, unconditionally
# and off-screen, decouples "the director runs" from "a layout chose to also
# show its log" — a future layout can drop the pane again without breaking
# playback. TILE_RELAY_DIR-gated in the process itself (_resolve_local_tiles),
# so this is a no-op for the six character workers beyond one idle process.
log "Starting roundtable director (headless — see startup.sh §7.5)"
python3 /app/replay_pane.py --config "${CONFIG_PATH}" &
REPLAY_PANE_PID=$!

# ── 8. Stream supervisor ───────────────────────────────────────────────────────
# Runs ffmpeg as a child process it starts/stops based on this worker's on/off
# flag (app/worker_control.py, toggled via message-api's /workers/{id}/enable|
# disable — no stack redeploy needed). Replaces a raw foreground `ffmpeg`
# call: killing that directly would have exited the whole container (see the
# cleanup line below), so a "disable" needs a supervisor that can stop/restart
# ffmpeg in place instead.
log "Starting stream supervisor (toggle via worker control API — no redeploy needed) → ${STREAM_RTMP_URL}/${STREAM_KEY}"
python3 /app/stream_supervisor.py \
    --config "${CONFIG_PATH}" \
    --rtmp-url "${STREAM_RTMP_URL}" \
    --stream-key "${STREAM_KEY}" \
    --resolution "${RESOLUTION}" \
    --capture-resolution "${CAPTURE_RESOLUTION}" \
    --display "${DISPLAY}"

log "Stream supervisor exited. Cleaning up."
kill $AGENT_PID $XTERM_PID $XVFB_PID 2>/dev/null
