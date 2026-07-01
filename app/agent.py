#!/usr/bin/env python3
"""
agent.py
Agent loop: publishes a heartbeat each tick, and on receiving a
`task_assignment` message calls the configured LLM (using the worker's
system_prompt) to produce an in-character narration, then replies with
`task_complete` (or `clarification_request` if the LLM call fails).
"""
import os
import time
import argparse

from message_bus import load_worker_config, build_message, MessageProducer, MessageConsumer
from llm_client import build_llm_client


def resolve(env_name, config_value, default=None):
    return os.environ.get(env_name) or config_value or default


def handle_task_assignment(worker_id, agent_config, llm_client, producer, msg):
    task = msg.get("payload", {}).get("task", "(no task description provided)")
    reply_to = msg.get("from") or "broadcast"
    prompt = (
        f"You've just been assigned a new task by {reply_to}: {task}\n\n"
        "Narrate what you're doing in 1-3 sentences, in character, as if speaking to the stream."
    )

    try:
        narration = llm_client.complete(
            agent_config.get("system_prompt", ""),
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        producer.send(build_message(
            worker_id, reply_to, "clarification_request",
            {"task": task, "error": str(exc)},
        ))
        return

    print(f"[agent:{worker_id}] {narration}")
    producer.send(build_message(
        worker_id, reply_to, "task_complete",
        {"task": task, "narration": narration},
    ))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/worker.yaml")
    args = parser.parse_args()

    config = load_worker_config(args.config)
    agent_config = config.get("agent", {})
    bus_config = config.get("message_bus", {})

    worker_id = resolve("WORKER_ID", bus_config.get("worker_id"), "worker")
    bootstrap_servers = resolve("KAFKA_BOOTSTRAP_SERVERS", bus_config.get("bootstrap_servers"))
    topic = resolve("KAFKA_TOPIC", bus_config.get("topic"))
    tick_rate_s = agent_config.get("tick_rate_ms", 5000) / 1000

    llm_client = build_llm_client(config)

    print(f"[agent] {worker_id} started. Config: {args.config}")
    print(f"[agent] Kafka bootstrap={bootstrap_servers} topic={topic}")
    print(f"[agent] LLM provider={config.get('llm', {}).get('provider', 'ollama')}")

    producer = MessageProducer(bootstrap_servers, topic)
    consumer = MessageConsumer(bootstrap_servers, topic, group_id=f"vtuber-agent-{worker_id}", worker_id=worker_id)

    i = 0
    while True:
        for msg in consumer.poll_new():
            print(f"[agent:{worker_id}] received {msg['type']} from {msg['from']}: {msg['payload']}")
            if msg["type"] == "task_assignment":
                handle_task_assignment(worker_id, agent_config, llm_client, producer, msg)

        heartbeat = build_message(worker_id, "broadcast", "status_update", {"text": f"heartbeat #{i}"})
        producer.send(heartbeat)
        print(f"[agent:{worker_id}] {heartbeat['type']} #{i}")

        i += 1
        time.sleep(tick_rate_s)


if __name__ == "__main__":
    main()
