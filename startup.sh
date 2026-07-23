#!/bin/bash
set -e

CONFIG_PATH="${CONFIG_PATH:-/config/worker.yaml}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
DISPLAY=":${DISPLAY_NUM}"
RESOLUTION="${RESOLUTION:-1920x1080}"
FONT_SIZE="${FONT_SIZE:-14}"
STREAM_RTMP_URL="${STREAM_RTMP_URL:-rtmp://localhost:1935/live}"
STREAM_KEY="${STREAM_KEY:-test}"

# Pixel dimensions of the capture, derived from RESOLUTION (e.g. 1920x1080)
VW="${RESOLUTION%x*}"
VH="${RESOLUTION#*x}"

log() { echo "[startup] $*"; }

# ── 1. Clean up stale Xvfb lock from previous run ─────────────────────────────
rm -f "/tmp/.X${DISPLAY_NUM}-lock"
rm -f "/tmp/.X11-unix/X${DISPLAY_NUM}"

# ── 2. Virtual display ─────────────────────────────────────────────────────────
log "Starting Xvfb on display ${DISPLAY}"
Xvfb "${DISPLAY}" -screen 0 "${RESOLUTION}x24" -ac +extension GLX &
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
    -bg '#0d1117' -fg '#e6edf3' \
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

# ── 7. Agent loop ─────────────────────────────────────────────────────────────
log "Starting agent loop"
python3 /app/agent.py --config "${CONFIG_PATH}" &
AGENT_PID=$!

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
    --display "${DISPLAY}"

log "Stream supervisor exited. Cleaning up."
kill $AGENT_PID $XTERM_PID $XVFB_PID 2>/dev/null
