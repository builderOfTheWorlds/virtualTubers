#!/bin/bash
set -euo pipefail

AVATAR_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$AVATAR_DIR"

if [ -d .venv ]; then
    source .venv/bin/activate
fi

CLI="python -m avatar.bridge.cli"
# Socket path matches paths.py: XDG_RUNTIME_DIR > ~/.local/share fallback
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    _RTDIR="$XDG_RUNTIME_DIR/ascii-avatar"
else
    _RTDIR="$HOME/.local/share/ascii-avatar"
fi
mkdir -p "$_RTDIR" && chmod 700 "$_RTDIR"
SOCKET="$_RTDIR/ascii-avatar-demo.sock"

echo "=== ASCII Avatar Demo ==="
echo "Starting avatar process..."
python -m avatar.main --socket "$SOCKET" --no-voice &
AVATAR_PID=$!
sleep 1

echo "Cycling through states..."

echo "[idle] — breathing..."
sleep 2

echo "[thinking] — processing..."
$CLI --socket "$SOCKET" think
sleep 3

echo "[speaking] — responding..."
$CLI --socket "$SOCKET" speak "Hello, I am your AI assistant."
sleep 3

echo "[listening] — waiting for input..."
$CLI --socket "$SOCKET" listen
sleep 2

echo "[error] — something went wrong..."
$CLI --socket "$SOCKET" error "Demo error"
sleep 2

echo "[idle] — back to rest..."
$CLI --socket "$SOCKET" idle
sleep 2

echo "Demo complete. Shutting down..."
kill $AVATAR_PID 2>/dev/null
wait $AVATAR_PID 2>/dev/null
