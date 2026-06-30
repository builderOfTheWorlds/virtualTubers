#!/usr/bin/env python3
"""
avatar.py — STUB
Draws a static ASCII face in the terminal and cycles through
expression states on a timer. Real expression logic comes later.
"""
import time
import os
import sys
import argparse
import itertools

FACES = [
    ("idle",       "◉  ◉", "╰───╯"),
    ("thinking",   "⊙  ⊙", "─────"),
    ("typing",     "◉  ◉", "╰───╯"),
    ("speaking",   "◕  ◕", "╰▾──╯"),
    ("happy",      "◉  ◉", "╰▾▾▾╯"),
    ("focused",    "◔  ◔", "─────"),
]

def render(name, title, state, eyes, mouth, bubble=""):
    os.system("clear")
    lines = [
        "",
        "  ╭───────────╮",
        f"  │  {eyes}  │",
        "  │     ▾     │",
        f"  │  {mouth}  │",
        "  ╰───────────╯",
        f"  [ {name:^9} ]",
        f"  [ {state:^9} ]",
    ]
    if bubble:
        lines[2] += f"   ╭{'─' * (len(bubble) + 2)}╮"
        lines[3] += f"   │ {bubble} │"
        lines[4] += f"   ╰{'─' * (len(bubble) + 2)}╯"

    print("\n".join(lines))
    sys.stdout.flush()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/worker.yaml")
    args = parser.parse_args()

    name  = os.environ.get("AGENT_NAME", "WORKER")
    title = os.environ.get("AGENT_TITLE", "Agent")

    for state, eyes, mouth in itertools.cycle(FACES):
        render(name, title, state, eyes, mouth)
        time.sleep(4)

if __name__ == "__main__":
    main()
