#!/usr/bin/env python3
"""
agent.py — STUB
Real agent loop (LLM-driven think/act) comes later. For now this proves
the Kafka message bus plumbing: it publishes a heartbeat status_update
each tick and prints any messages addressed to it (or broadcast).
"""
import os
import time
import argparse

from message_bus import load_worker_config, build_message, MessageProducer, MessageConsumer


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
    tick_rate_s = config.get("agent", {}).get("tick_rate_ms", 5000) / 1000

    print(f"[agent] {worker_id} stub started. Config: {args.config}")
    print(f"[agent] Kafka bootstrap={bootstrap_servers} topic={topic}")

    producer = MessageProducer(bootstrap_servers, topic)
    consumer = MessageConsumer(bootstrap_servers, topic, group_id=f"vtuber-agent-{worker_id}", worker_id=worker_id)

    i = 0
    while True:
        for msg in consumer.poll_new():
            print(f"[agent:{worker_id}] received {msg['type']} from {msg['from']}: {msg['payload']}")

        heartbeat = build_message(worker_id, "broadcast", "status_update", {"text": f"heartbeat #{i}"})
        producer.send(heartbeat)
        print(f"[agent:{worker_id}] {heartbeat['type']} #{i}")

        i += 1
        time.sleep(tick_rate_s)


if __name__ == "__main__":
    main()
