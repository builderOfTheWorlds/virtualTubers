#!/usr/bin/env python3
# DEPRECATED: Use scripts/claude-hook-event.py with --agent mode instead.
# This script will be removed in a future version.
"""Claude Code PostToolUse hook — sends tool output to avatar for TTS."""
import json
import sys
import subprocess

BRIDGE = "avatar-bridge"  # Assumes installed in PATH via pip install -e .
SOCKET = "/tmp/ascii-avatar.sock"
MAX_CHARS = 300


def main():
    try:
        hook_data = json.load(sys.stdin)
    except Exception:
        return

    tool_name = hook_data.get("tool_name", "")
    tool_response = hook_data.get("tool_response", {})

    # Extract text from tool response
    text = ""
    if isinstance(tool_response, dict):
        text = tool_response.get("stdout", "") or tool_response.get("content", "") or ""
    elif isinstance(tool_response, str):
        text = tool_response

    if not text:
        # Still signal speaking state
        subprocess.run(
            [BRIDGE, "--socket", SOCKET, "speak", "done"],
            capture_output=True, timeout=2,
        )
        return

    # Clean up for speech
    text = text.strip()
    # Strip ANSI escape codes
    import re
    text = re.sub(r'\033\[[0-9;]*m', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)

    # Truncate
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    # Skip if just noise (very short or all symbols)
    if len(text) < 5:
        text = "done"

    subprocess.run(
        [BRIDGE, "--socket", SOCKET, "speak", text],
        capture_output=True, timeout=5,
    )


if __name__ == "__main__":
    main()
