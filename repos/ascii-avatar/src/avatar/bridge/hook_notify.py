#!/usr/bin/env python3
"""Hook script for Claude Code 'Notification' event.

Speaks notifications with varied, natural phrasing instead of
the same robotic line every time.
"""

import json
import random
import sys
import datetime

from avatar.bridge.hooks import respond
from avatar.bridge.paths import get_log_path, get_socket_path

LOG = get_log_path()

# Varied idle phrases — rotated to avoid repetition
_IDLE_PHRASES = [
    "Done. Your turn.",
    "Finished up. Over to you.",
    "All yours.",
    "That's done. What's next?",
    "Ready when you are.",
    "Standing by.",
    "Wrapped up. Waiting on you.",
    "I'm here. Go ahead.",
]

_PERMISSION_PHRASES = [
    "I need your go-ahead to continue.",
    "Waiting for permission.",
    "Need your approval on this one.",
    "Can I proceed?",
]


def log(msg: str):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} [notify] {msg}\n")


def main():
    log("hook fired")

    try:
        stdin_data = sys.stdin.read()
        hook_input = json.loads(stdin_data) if stdin_data.strip() else {}
    except (json.JSONDecodeError, EOFError) as e:
        log(f"stdin parse error: {e}")
        hook_input = {}

    socket_path = get_socket_path()
    notification_type = hook_input.get("notification_type", "")
    message = hook_input.get("message", "")

    log(f"type: {notification_type}, message: {message[:100]}")

    if notification_type == "permission_prompt":
        speech = random.choice(_PERMISSION_PHRASES)
    elif notification_type == "idle_prompt":
        speech = random.choice(_IDLE_PHRASES)
    elif message:
        speech = message[:150]
    else:
        speech = "Hey. Need your attention."

    try:
        respond(speech, socket_path=socket_path)
        log(f"speak sent: {speech}")
    except Exception as e:
        log(f"speak failed: {e}")


if __name__ == "__main__":
    main()
