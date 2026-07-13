#!/usr/bin/env python3
# scripts/claude-hook-event.py
"""Unified Claude Code hook → avatar agent event forwarder.

Reads hook JSON from stdin, pushes it as-is to the avatar agent's ZeroMQ socket.
No filtering, no logic — the agent decides what to do with each event.
"""
import argparse
import json
import sys

import zmq

DEFAULT_SOCKET = "/tmp/ascii-avatar.sock"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default=DEFAULT_SOCKET)
    args = parser.parse_args()

    try:
        hook_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.setsockopt(zmq.LINGER, 500)  # Don't hang if receiver is down
    sock.connect(f"ipc://{args.socket}")
    sock.send_json(hook_data)
    sock.close()
    ctx.term()


if __name__ == "__main__":
    main()
