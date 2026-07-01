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
from agent_state import resolve_state_path, write_state
from tmux_control import select_pane, send_keys, send_raw, send_command, TmuxError


def resolve(env_name, config_value, default=None):
    return os.environ.get(env_name) or config_value or default


def demo_editor_note(worker_id, task):
    """Scripted (non-LLM) demo of the agent acting on its own tmux UI (see
    docs/tmux_control.md): focus the editor pane and drop a fixed TODO
    comment noting the task, so pane-switching/typing is visible on stream
    ahead of any real LLM-driven tool use. nvim opens in normal mode, so "i"
    enters insert mode first and "Escape" returns to normal mode after —
    this only touches the in-memory buffer, it's never saved.

    Best-effort: no tmux session (e.g. running outside the container, or in
    tests) must not take the tick loop down, so tmux/pane-resolution
    failures are swallowed here rather than propagated.
    """
    flat_task = " ".join(task.split())
    try:
        select_pane("editor")
        send_raw("editor", "i")
        send_keys("editor", f"# TODO: {flat_task}")
        send_raw("editor", "Escape")
    except (TmuxError, OSError) as exc:
        print(f"[agent:{worker_id}] tmux editor demo skipped: {exc}")


def demo_filetree_ls(worker_id):
    """Scripted (non-LLM) demo of the agent using the filetree pane: focus
    it, run `ls` now that it's an interactive shell (see
    config/panels/filetree.yaml — no longer a `watch` loop, which can't
    accept keystrokes as commands), then refocus the editor pane so the
    coder visibly returns to work.

    Best-effort like demo_editor_note: no tmux session must not take the
    tick loop down.
    """
    try:
        select_pane("filetree")
        send_command("filetree", "ls")
        select_pane("editor")
    except (TmuxError, OSError) as exc:
        print(f"[agent:{worker_id}] tmux filetree demo skipped: {exc}")


def handle_task_assignment(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    task = msg.get("payload", {}).get("task", "(no task description provided)")
    reply_to = msg.get("from") or "broadcast"
    prompt = (
        f"You've just been assigned a new task by {reply_to}: {task}\n\n"
        "Narrate what you're doing in 1-3 sentences, in character, as if speaking to the stream."
    )

    if state_path:
        write_state(state_path, "thinking", action=f"working on: {task}")
    demo_editor_note(worker_id, task)
    demo_filetree_ls(worker_id)

    try:
        narration = llm_client.complete(
            agent_config.get("system_prompt", ""),
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        producer.send(build_message(
            worker_id, reply_to, "clarification_request",
            {"task": task, "error": str(exc)},
        ))
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "speaking", action=f"replied to {reply_to}", bubble=narration)
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
    state_path = resolve_state_path(agent_config)

    print(f"[agent] {worker_id} started. Config: {args.config}")
    print(f"[agent] Kafka bootstrap={bootstrap_servers} topic={topic}")
    print(f"[agent] LLM provider={config.get('llm', {}).get('provider', 'ollama')}")
    print(f"[agent] avatar state file={state_path}")

    write_state(state_path, "idle", action="starting up")

    producer = MessageProducer(bootstrap_servers, topic)
    consumer = MessageConsumer(bootstrap_servers, topic, group_id=f"vtuber-agent-{worker_id}", worker_id=worker_id)

    i = 0
    while True:
        for msg in consumer.poll_new():
            print(f"[agent:{worker_id}] received {msg['type']} from {msg['from']}: {msg['payload']}")
            if msg["type"] == "task_assignment":
                handle_task_assignment(worker_id, agent_config, llm_client, producer, msg, state_path)

        heartbeat = build_message(worker_id, "broadcast", "status_update", {"text": f"heartbeat #{i}"})
        producer.send(heartbeat)
        print(f"[agent:{worker_id}] {heartbeat['type']} #{i}")

        i += 1
        time.sleep(tick_rate_s)


if __name__ == "__main__":
    main()
