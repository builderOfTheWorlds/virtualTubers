#!/usr/bin/env python3
"""
agent.py — STUB
Real agent loop comes later. For now just keeps the process alive
and writes dummy messages to the world state so other panes have
something to display.
"""
import time
import os
import pathlib
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/worker.yaml")
    args = parser.parse_args()

    # Read worker ID from env or default
    worker_id = os.environ.get("WORKER_ID", "worker")

    # Ensure message bus log exists
    log_path = pathlib.Path("/data/world-state/messages/bus.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[agent] {worker_id} stub started. Config: {args.config}")

    i = 0
    while True:
        msg = f"[{worker_id}] heartbeat #{i}"
        print(msg)
        with open(log_path, "a") as f:
            f.write(msg + "\n")
        i += 1
        time.sleep(10)

if __name__ == "__main__":
    main()
