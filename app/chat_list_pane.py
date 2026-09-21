#!/usr/bin/env python3
"""
chat_list_pane.py
Standalone display process for the tmux narrow "c" (chat-room-name list)
pane. Per docs/tuber_base_layout_plan.md Decision 5, this is a STATIC STUB:
always shows exactly one entry, `all`, highlighted (reverse video). No real
multi-channel Kafka routing exists yet — selecting it does nothing. That is
explicitly future work.

Extremely narrow (~3-5 cols) — plain print(), no TUI, no arguments needed.
"""
import argparse
import time

REVERSE = "\033[7m"
RESET = "\033[0m"

CHANNELS = ["all"]


def render_chat_list(entries=None):
    """Pure render: each entry on its own line, reverse-video highlighted."""
    entries = entries if entries is not None else CHANNELS
    return "\n".join(f"{REVERSE}{entry}{RESET}" for entry in entries)


def run(sleep=time.sleep):
    print(render_chat_list(), flush=True)
    # Static pane: nothing to refresh, just stay resident so the tmux pane
    # doesn't fall back to a bare shell prompt.
    while True:
        sleep(3600)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Static chat-room-name list stub.")
    parser.parse_args(argv)
    run()


if __name__ == "__main__":
    main()
