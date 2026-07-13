#!/usr/bin/env python3
"""Hook script for Claude Code 'PostToolUse' event.

Provides brief mid-response narration so the avatar doesn't sit silent
for minutes during long tool-use sequences. Speaks short status phrases
like "Reading the file." or "Making edits."

Throttled to avoid speaking on every single tool call — only narrates
when there's been silence for a few seconds.
"""

import json
import sys
import time
import datetime

from avatar.bridge.paths import get_log_path, get_socket_path, get_throttle_path

LOG = get_log_path()
THROTTLE_FILE = get_throttle_path()
THROTTLE_SECONDS = 8  # minimum gap between tool narrations


def log(msg: str):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} [tool] {msg}\n")


def _should_speak() -> bool:
    """Throttle: only speak if enough time has passed since last narration."""
    try:
        if THROTTLE_FILE.exists():
            last = float(THROTTLE_FILE.read_text().strip())
            if time.time() - last < THROTTLE_SECONDS:
                return False
    except (ValueError, OSError):
        pass
    return True


def _mark_spoken():
    """Record that we just spoke."""
    try:
        THROTTLE_FILE.write_text(str(time.time()))
    except OSError:
        pass


def main():
    log("hook fired")

    try:
        stdin_data = sys.stdin.read()
        hook_input = json.loads(stdin_data) if stdin_data.strip() else {}
    except (json.JSONDecodeError, EOFError) as e:
        log(f"stdin parse error: {e}")
        hook_input = {}

    tool_name = hook_input.get("tool_name", "")
    log(f"tool: {tool_name}")

    if not _should_speak():
        log("throttled — skipping")
        return

    # Don't narrate very fast tools (they'd overlap with the next one)
    skip_tools = {"todoread", "todowrite", "taskoutput", "sendmessage"}
    if tool_name.lower() in skip_tools:
        log(f"skipping trivial tool: {tool_name}")
        return

    socket_path = get_socket_path()

    try:
        from avatar.voice.summarizer import tool_narration
        speech = tool_narration(tool_name, hook_input.get("tool_input"))
        log(f"narration: {speech}")

        from avatar.bridge.hooks import respond
        respond(speech, socket_path=socket_path)
        _mark_spoken()
        log("speak sent OK")
    except Exception as e:
        log(f"speak failed: {e}")


if __name__ == "__main__":
    main()
