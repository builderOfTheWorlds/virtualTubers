#!/usr/bin/env python3
"""Hook script for Claude Code 'Stop' event.

Reads last_assistant_message from hook input, generates a natural
spoken summary (via Haiku or local heuristics), speaks it, then
switches to listening state.
"""

import json
import sys
import datetime

from avatar.bridge.hooks import respond, listen
from avatar.bridge.paths import get_log_path, get_socket_path
from avatar.voice.summarizer import summarize_for_voice

LOG = get_log_path()


def log(msg: str):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} [stop] {msg}\n")


def main():
    log("hook fired")

    try:
        stdin_data = sys.stdin.read()
        hook_input = json.loads(stdin_data) if stdin_data.strip() else {}
    except (json.JSONDecodeError, EOFError) as e:
        log(f"stdin parse error: {e}")
        hook_input = {}

    socket_path = get_socket_path()

    last_message = hook_input.get("last_assistant_message", "")
    log(f"last_assistant_message length: {len(last_message)}")

    speech = summarize_for_voice(last_message)
    log(f"speech ({len(speech)} chars): {speech}")

    if speech:
        try:
            respond(speech, socket_path=socket_path)
            log("speak sent OK")
        except Exception as e:
            log(f"speak failed: {e}")

    try:
        listen(socket_path=socket_path)
        log("listen sent OK")
    except Exception as e:
        log(f"listen failed: {e}")


if __name__ == "__main__":
    main()
