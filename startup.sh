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
log "Starting PulseAudio"
pulseaudio --system --disallow-exit --disallow-module-loading --daemonize=true || true
sleep 1
pactl load-module module-null-sink sink_name=vout sink_properties=device.description=VirtualOut 2>/dev/null || true

# ── 4. Tmux session + pane layout ─────────────────────────────────────────────
SESSION="worker"
COLS=240
ROWS=67
log "Creating tmux session: ${SESSION} (${COLS}x${ROWS})"

tmux new-session -d -s "${SESSION}" -x "${COLS}" -y "${ROWS}"

# Left column (25%) | right column (75%)
tmux split-window -h -t "${SESSION}" -p 75

# Left: file list (top 40%) / avatar (bottom 60%)
tmux select-pane -t "${SESSION}:0.0"
tmux split-window -v -p 60

# Right: editor (top 70%) / agent chat (bottom 30%)
tmux select-pane -t "${SESSION}:0.2"
tmux split-window -v -p 30

# Bottom strip: htop
tmux select-pane -t "${SESSION}:0.0"
tmux split-window -v -p 15

# ── 5. Start processes in panes ───────────────────────────────────────────────
log "Starting pane processes"
tmux send-keys -t "${SESSION}:0.0" \
    'watch -n2 "tree /data/repo 2>/dev/null || echo (no workspace yet)"' Enter
tmux send-keys -t "${SESSION}:0.1" \
    "python3 /app/avatar.py --config ${CONFIG_PATH}" Enter
tmux send-keys -t "${SESSION}:0.2" 'nvim' Enter
tmux send-keys -t "${SESSION}:0.3" \
    "python3 /app/tail_bus.py --config ${CONFIG_PATH}" Enter
tmux send-keys -t "${SESSION}:0.4" 'htop' Enter

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
WID=$(DISPLAY="${DISPLAY}" xdotool search --sync --class xterm | head -1)
DISPLAY="${DISPLAY}" xdotool windowmove "$WID" 0 0
DISPLAY="${DISPLAY}" xdotool windowsize "$WID" "$VW" "$VH"
sleep 1

# xterm recomputes its cell grid to fill the window; make tmux follow the new
# client size and redraw so its panes expand to the full frame (no fixed 240x67 box).
DISPLAY="${DISPLAY}" tmux set -g window-size latest \; refresh-client -t "${SESSION}" 2>/dev/null || true

# ── 7. Agent loop ─────────────────────────────────────────────────────────────
log "Starting agent loop"
python3 /app/agent.py --config "${CONFIG_PATH}" &
AGENT_PID=$!

# ── 8. Broadcaster ────────────────────────────────────────────────────────────
log "Starting ffmpeg broadcaster → ${STREAM_RTMP_URL}/${STREAM_KEY}"
ffmpeg \
    -f x11grab \
        -video_size "${RESOLUTION}" \
        -framerate 30 \
        -i "${DISPLAY}" \
    -f lavfi \
        -i anullsrc=channel_layout=stereo:sample_rate=44100 \
    -c:v libx264 \
        -preset veryfast \
        -tune zerolatency \
        -b:v 3000k \
        -maxrate 3000k \
        -bufsize 6000k \
        -pix_fmt yuv420p \
        -g 60 \
    -c:a aac \
        -b:a 128k \
        -ar 44100 \
    -reconnect 1 \
    -reconnect_streamed 1 \
    -reconnect_delay_max 5 \
    -f flv \
    "${STREAM_RTMP_URL}/${STREAM_KEY}"

log "Broadcaster exited. Cleaning up."
kill $AGENT_PID $XTERM_PID $XVFB_PID 2>/dev/null
