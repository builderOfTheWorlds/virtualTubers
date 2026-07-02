#!/usr/bin/env python3
"""
agent.py
Agent loop: publishes a heartbeat each tick and dispatches every incoming
message type (docs/VTuber_AI_Dev_Team_Concept.md §3.4) via MESSAGE_HANDLERS.
Workers collaborate by role (agent config `role`): the coder replies
`task_complete` to the sender and hands the commit to the tester
(`commit_notification`), the tester "runs tests" and reports `test_passed`
or `bug_report` to the manager, and the manager re-delegates fixes (bounded
by MAX_BUG_RETRIES via `retry_count` traveling in message payloads) or
reports back to the operator (`manager_report`). Any role answers an
`operator_message` with an `operator_reply`.
"""
import os
import random
import time
import argparse

from message_bus import load_worker_config, build_message, MessageProducer, MessageConsumer
from llm_client import build_llm_client
from agent_state import resolve_state_path, write_state
from tmux_control import select_pane, send_keys, send_raw, send_command, TmuxError


# Stub test-outcome heuristic — no real test execution yet (see
# .claude/prompts/worker_interaction_kafka.md §2/§9). Module-level constants
# so tuning or replacing the heuristic is a one-edit change.
TEST_PASS_PROBABILITY = 0.7
BUG_SEVERITIES = ["low", "medium", "high", "critical"]
BUG_SEVERITY_WEIGHTS = [10, 40, 35, 15]
MAX_BUG_RETRIES = 3


def resolve(env_name, config_value, default=None):
    return os.environ.get(env_name) or config_value or default


def _decide_test_outcome():
    """Weighted-random stand-in for actually running a test suite.

    Returns (passed, severity): (True, None) on pass, otherwise
    (False, severity) with severity drawn from BUG_SEVERITIES weighted by
    BUG_SEVERITY_WEIGHTS. Factored out (not inlined in the tester handlers)
    so tests can monkeypatch it — never assert on the real randomness.
    """
    if random.random() < TEST_PASS_PROBABILITY:
        return True, None
    severity = random.choices(BUG_SEVERITIES, weights=BUG_SEVERITY_WEIGHTS, k=1)[0]
    return False, severity


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
    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    retry_count = payload.get("retry_count", 0)
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

    # Coder hands the "commit" straight to the tester so the ticket keeps
    # flowing: coder -> tester -> manager. retry_count rides along so the
    # manager can bound the bug/fix loop (MAX_BUG_RETRIES).
    if agent_config.get("role") == "coder":
        producer.send(build_message(
            worker_id, "tester", "commit_notification",
            {
                "task": task,
                "commit_message": f"Implement: {task}",
                "narration": narration,
                "retry_count": retry_count,
            },
        ))


def _run_tests_and_report(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    """Shared tester flow for commit_notification / retest_request: narrate a
    test run, decide the outcome via _decide_test_outcome(), and report
    `test_passed` or `bug_report` to the manager. On the tester's own LLM
    failure, send `clarification_request` to the manager (same contract shape
    the coder uses, so one manager handler covers both origins).
    """
    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    retry_count = payload.get("retry_count", 0)
    sender = msg.get("from") or "broadcast"
    prompt = (
        f"{sender} just handed you a commit for: {task}\n\n"
        "Narrate running the test suite against it in 1-3 sentences, in character, "
        "as if speaking to the stream."
    )

    if state_path:
        write_state(state_path, "focused", action=f"testing: {task}")

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
            worker_id, "manager", "clarification_request",
            {"task": task, "error": str(exc)},
        ))
        return

    print(f"[agent:{worker_id}] {narration}")
    passed, severity = _decide_test_outcome()
    if passed:
        if state_path:
            write_state(state_path, "happy", action=f"tests passed: {task}", bubble=narration)
        producer.send(build_message(
            worker_id, "manager", "test_passed",
            {"task": task, "narration": narration, "retry_count": retry_count},
        ))
    else:
        repro = f"Run the suite against '{task}' — the new tests fail ({severity})."
        if state_path:
            write_state(state_path, "speaking", action=f"found a bug: {task}", bubble=narration)
        producer.send(build_message(
            worker_id, "manager", "bug_report",
            {
                "task": task,
                "severity": severity,
                "repro": repro,
                "narration": narration,
                "retry_count": retry_count,
            },
        ))


def handle_commit_notification(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    role = agent_config.get("role")
    if role != "tester":
        print(f"[agent:{worker_id}] ignoring commit_notification (role={role}, expected tester)")
        return
    _run_tests_and_report(worker_id, agent_config, llm_client, producer, msg, state_path)


def handle_retest_request(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    role = agent_config.get("role")
    if role != "tester":
        print(f"[agent:{worker_id}] ignoring retest_request (role={role}, expected tester)")
        return
    _run_tests_and_report(worker_id, agent_config, llm_client, producer, msg, state_path)


def _send_manager_report(worker_id, producer, report_type, task, narration, extra=None):
    """Manager -> operator feedback surface. One message type
    (`manager_report`) with payload discriminator
    report_type: "milestone" | "blocker" | "escalation" — deliberately NOT
    `status_update`, which the feed hides by default (heartbeat flood filter).
    """
    payload = {"report_type": report_type, "task": task, "narration": narration}
    if extra:
        payload.update(extra)
    return producer.send(build_message(worker_id, "operator", "manager_report", payload))


def handle_bug_report(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    """Manager triage: re-delegate a fix to the coder with retry_count + 1,
    or — once the incoming retry_count reaches MAX_BUG_RETRIES, or the
    manager's own LLM fails — escalate to the operator instead (a blocker
    must not vanish because two LLM calls failed back to back).
    """
    role = agent_config.get("role")
    if role != "manager":
        print(f"[agent:{worker_id}] ignoring bug_report (role={role}, expected manager)")
        return

    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    severity = payload.get("severity", "unknown")
    repro = payload.get("repro", "(no repro provided)")
    retry_count = payload.get("retry_count", 0)
    sender = msg.get("from") or "broadcast"
    at_cap = retry_count >= MAX_BUG_RETRIES

    if state_path:
        write_state(state_path, "thinking", action=f"triaging bug: {task}")

    if at_cap:
        prompt = (
            f"{sender} reported a {severity} severity bug on '{task}' and it has already "
            f"been through {retry_count} fix attempts. Narrate escalating this blocker to "
            "the boss in 1-3 sentences, in character, as if speaking to the stream."
        )
    else:
        prompt = (
            f"{sender} reported a {severity} severity bug on '{task}'. Repro: {repro}\n\n"
            "Narrate reprioritizing and sending it back to the coder in 1-3 sentences, "
            "in character, as if speaking to the stream."
        )

    try:
        narration = llm_client.complete(
            agent_config.get("system_prompt", ""),
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        narration = (
            f"(narration unavailable: {exc}) Escalating {severity} bug on '{task}' "
            f"after {retry_count} fix attempts."
        )
        if state_path:
            write_state(state_path, "frustrated", action=f"escalating: {task}", bubble=narration)
        _send_manager_report(
            worker_id, producer, "escalation", task, narration,
            extra={"severity": severity, "retry_count": retry_count},
        )
        return

    print(f"[agent:{worker_id}] {narration}")
    if at_cap:
        if state_path:
            write_state(state_path, "frustrated", action=f"escalating: {task}", bubble=narration)
        _send_manager_report(
            worker_id, producer, "escalation", task, narration,
            extra={"severity": severity, "retry_count": retry_count},
        )
        return

    if state_path:
        write_state(state_path, "speaking", action=f"re-delegated: {task}", bubble=narration)
    producer.send(build_message(
        worker_id, "coder", "task_assignment",
        {
            "task": f"Fix bug ({severity}): {task}. Repro: {repro}",
            "retry_count": retry_count + 1,
        },
    ))


def handle_test_passed(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    role = agent_config.get("role")
    if role != "manager":
        print(f"[agent:{worker_id}] ignoring test_passed (role={role}, expected manager)")
        return

    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    sender = msg.get("from") or "broadcast"
    prompt = (
        f"{sender} reports the full test suite passed for '{task}'!\n\n"
        "Narrate celebrating the team win in 1-3 sentences, in character, "
        "as if speaking to the stream."
    )

    if state_path:
        write_state(state_path, "thinking", action=f"reviewing results: {task}")

    try:
        narration = llm_client.complete(
            agent_config.get("system_prompt", ""),
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "happy", action=f"shipped: {task}", bubble=narration)
    _send_manager_report(worker_id, producer, "milestone", task, narration)


def handle_task_complete(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    """Manager acknowledges a coder's task_complete. Deliberately NO bus send:
    the coder's own commit_notification already drives the tester — sending
    anything here (e.g. a retest_request) would duplicate the test run. Do
    not "fix" this by adding a send.
    """
    role = agent_config.get("role")
    if role != "manager":
        print(f"[agent:{worker_id}] ignoring task_complete (role={role}, expected manager)")
        return

    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    sender = msg.get("from") or "broadcast"
    prompt = (
        f"{sender} just finished the task '{task}' and handed the commit to the tester.\n\n"
        "Narrate acknowledging the progress in 1-3 sentences, in character, "
        "as if speaking to the stream."
    )

    if state_path:
        write_state(state_path, "thinking", action=f"reviewing: {task}")

    try:
        narration = llm_client.complete(
            agent_config.get("system_prompt", ""),
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "speaking", action=f"acknowledged: {task}", bubble=narration)


def handle_clarification_request(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    """Manager escalates a worker's blocker to the operator — ALWAYS sends a
    "blocker" manager_report (fallback narration if the manager's own LLM
    also fails). Deliberately does NOT auto-resend task_assignment: that
    risks a retry storm against a broken LLM endpoint (concept doc §11,
    "Human override").
    """
    role = agent_config.get("role")
    if role != "manager":
        print(f"[agent:{worker_id}] ignoring clarification_request (role={role}, expected manager)")
        return

    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    error = payload.get("error", "(no error provided)")
    sender = msg.get("from") or "broadcast"
    prompt = (
        f"{sender} is blocked on '{task}' and needs help: {error}\n\n"
        "Narrate assessing the blocker and flagging it to the boss in 1-3 sentences, "
        "in character, as if speaking to the stream."
    )

    if state_path:
        write_state(state_path, "thinking", action=f"assessing blocker: {task}")

    try:
        narration = llm_client.complete(
            agent_config.get("system_prompt", ""),
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        narration = (
            f"(narration unavailable: {exc}) {sender} is blocked on '{task}': {error}"
        )
    else:
        print(f"[agent:{worker_id}] {narration}")

    if state_path:
        write_state(state_path, "frustrated", action=f"blocker: {task}", bubble=narration)
    _send_manager_report(
        worker_id, producer, "blocker", task, narration,
        extra={"blocked_worker": sender, "error": error},
    )


def handle_operator_message(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    """Direct operator -> worker channel (message-api's default type). Any
    role handles it. Lightweight by design: LLM reply only, NO tmux demo
    side effects. Always answers `to: "operator"` with an operator_reply.
    """
    message = msg.get("payload", {}).get("message", "(no message provided)")
    prompt = (
        f"The operator (your boss) just messaged you directly: {message}\n\n"
        "Reply in 1-3 sentences, in character, as if speaking to the stream."
    )

    if state_path:
        write_state(state_path, "thinking", action="replying to operator")

    try:
        narration = llm_client.complete(
            agent_config.get("system_prompt", ""),
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action="failed replying to operator", bubble=f"Ugh... {exc}")
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"error": str(exc)},
        ))
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "speaking", action="replied to operator", bubble=narration)
    producer.send(build_message(
        worker_id, "operator", "operator_reply",
        {"narration": narration},
    ))


# Dispatch table — the 8 message types from docs/VTuber_AI_Dev_Team_Concept.md
# §3.4 (status_update is send-only heartbeat traffic; operator_message is
# message-api's default type standing in for direct operator chat).
MESSAGE_HANDLERS = {
    "task_assignment": handle_task_assignment,
    "commit_notification": handle_commit_notification,
    "retest_request": handle_retest_request,
    "bug_report": handle_bug_report,
    "test_passed": handle_test_passed,
    "task_complete": handle_task_complete,
    "clarification_request": handle_clarification_request,
    "operator_message": handle_operator_message,
}


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
            handler = MESSAGE_HANDLERS.get(msg["type"])
            if handler:
                handler(worker_id, agent_config, llm_client, producer, msg, state_path)

        heartbeat = build_message(worker_id, "broadcast", "status_update", {"text": f"heartbeat #{i}"})
        producer.send(heartbeat)
        print(f"[agent:{worker_id}] {heartbeat['type']} #{i}")

        i += 1
        time.sleep(tick_rate_s)


if __name__ == "__main__":
    main()
