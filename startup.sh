#!/bin/bash
set -e

CONFIG_PATH="${CONFIG_PATH:-/config/worker.yaml}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
DISPLAY=":${DISPLAY_NUM}"
RESOLUTION="${RESOLUTION:-1920x1080}"
STREAM_RTMP_URL="${STREAM_RTMP_URL:-rtmp://localhost:1935/live}"
STREAM_KEY="${STREAM_KEY:-test}"

log() { echo "[startup] $*"; }

# ── 1. Virtual display ─────────────────────────────────────────────────────────
log "Starting Xvfb on display ${DISPLAY}"
Xvfb "${DISPLAY}" -screen 0 "${RESOLUTION}x24" -ac &
XVFB_PID=$!
export DISPLAY
sleep 1

# ── 2. PulseAudio virtual sink ─────────────────────────────────────────────────
log "Starting PulseAudio"
pulseaudio --start --exit-idle-time=-1
sleep 1
pactl load-module module-null-sink sink_name=vout sink_properties=device.description=VirtualOut
export PULSE_SINK=vout

# ── 3. Tmux session + pane layout ─────────────────────────────────────────────
SESSION="worker"
log "Creating tmux session: ${SESSION}"

tmux new-session -d -s "${SESSION}" -x 220 -y 55

# Split into columns: left 25% | right 75%
tmux split-window -h -t "${SESSION}" -p 75

# Left column: split into file list (top) / avatar (middle) — right column stays full for now
tmux select-pane -t "${SESSION}:0.0"
tmux split-window -v -p 60   # file list gets top 40%, avatar gets bottom 60%

# Right column: split into editor (top) / agent chat (bottom 30%)
tmux select-pane -t "${SESSION}:0.2"
tmux split-window -v -p 30

# Bottom strip across full width: htop
tmux select-pane -t "${SESSION}:0.0"
tmux split-window -v -p 15

# ── 4. Start processes in panes ───────────────────────────────────────────────
log "Starting pane processes"

# Pane 0: file list
tmux send-keys -t "${SESSION}:0.0" \
    'watch -n2 -c "lsd --tree --color=always /data/repo 2>/dev/null || echo (no workspace yet)"' Enter

# Pane 1: avatar (Python process owns this pane)
tmux send-keys -t "${SESSION}:0.1" \
    "python3 /app/avatar.py --config ${CONFIG_PATH}" Enter

# Pane 2: editor
tmux send-keys -t "${SESSION}:0.2" 'nvim' Enter

# Pane 3: agent chat (inter-agent message log)
tmux send-keys -t "${SESSION}:0.3" \
    'tail -n 20 -f /data/world-state/messages/bus.log 2>/dev/null || echo "Waiting for message bus..."' Enter

# Pane 4: htop (bottom strip)
tmux send-keys -t "${SESSION}:0.4" 'htop' Enter

# ── 5. Open xterm pointing at the tmux session ────────────────────────────────
log "Opening xterm"
xterm \
    -fa 'Monospace' -fs 12 \
    -geometry "${RESOLUTION%x*}x${RESOLUTION#*x}+0+0" \
    -bg '#0d1117' -fg '#e6edf3' \
    -e "tmux attach -t ${SESSION}" &
XTERM_PID=$!
sleep 1

# ── 6. Agent loop ─────────────────────────────────────────────────────────────
log "Starting agent loop"
python3 /app/agent.py --config "${CONFIG_PATH}" &
AGENT_PID=$!

# ── 7. Broadcaster ────────────────────────────────────────────────────────────
log "Starting ffmpeg broadcaster → ${STREAM_RTMP_URL}/${STREAM_KEY}"
ffmpeg \
    -f x11grab \
        -video_size "${RESOLUTION}" \
        -framerate 30 \
        -i "${DISPLAY}" \
    -f pulse \
        -i vout.monitor \
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

# ffmpeg exiting means the stream died — container stops, K8s restarts it
log "Broadcaster exited. Cleaning up."
kill $AGENT_PID $XTERM_PID $XVFB_PID 2>/dev/null
