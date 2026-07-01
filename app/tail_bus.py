#!/usr/bin/env python3
"""
tail_bus.py
Standalone display process for the tmux "agent chat" pane. Consumes every
message on the bus (not just ones addressed to this worker) and prints a
formatted line, replacing the old `tail -f bus.log` behavior.
"""
import os
import argparse
from datetime import datetime

from message_bus import load_worker_config, MessageConsumer


def resolve(env_name, config_value, default=None):
    return os.environ.get(env_name) or config_value or default


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/worker.yaml")
    args = parser.parse_args()

    config = load_worker_config(args.config)
    bus_config = config.get("message_bus", {})

    worker_id = resolve("WORKER_ID", bus_config.get("worker_id"), "worker")
    bootstrap_servers = resolve("KAFKA_BOOTSTRAP_SERVERS", bus_config.get("bootstrap_servers"))
    topic = resolve("KAFKA_TOPIC", bus_config.get("topic"))

    print("Waiting for message bus...")
    consumer = MessageConsumer(bootstrap_servers, topic, group_id=f"vtuber-display-{worker_id}")

    while True:
        for msg in consumer.poll_new(to_filter=False):
            ts = datetime.fromisoformat(msg["timestamp"]).strftime("%H:%M:%S")
            print(f"[{ts}] {msg['from']} -> {msg['to']} ({msg['type']}): {msg['payload']}")


if __name__ == "__main__":
    main()
