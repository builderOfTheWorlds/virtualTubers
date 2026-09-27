"""
agent_handlers/manager.py
Manager-side handlers (split out of app/agent.py): bug triage (re-delegate
or escalate, bounded by MAX_BUG_RETRIES), `test_passed` milestones,
narration-only `task_complete` acknowledgement, and `clarification_request`
blocker escalation.
"""
from message_bus import build_message, correlation_of, reply_ids
from agent_state import write_state

from .common import _complete_with_emotion, _send_manager_report


# Bug/fix loop bound: once an incoming bug_report's retry_count reaches this,
# the manager escalates to the operator instead of re-delegating.
MAX_BUG_RETRIES = 3


def handle_bug_report(worker_id, agent_config, llm_client, producer, msg,
                      state_path=None, coding_backend=None):
    """Manager triage: re-delegate a fix to the originating coder (payload
    coder_id — there are several coders now) with retry_count + 1, or — once
    the incoming retry_count reaches MAX_BUG_RETRIES, or the manager's own
    LLM fails — escalate to the operator instead (a blocker must not vanish
    because two LLM calls failed back to back).
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
    coder_id = payload.get("coder_id") or "coder"
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
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        narration, emotion = (
            f"(narration unavailable: {exc}) Escalating {severity} bug on '{task}' "
            f"after {retry_count} fix attempts."
        ), "neutral"
        if state_path:
            write_state(state_path, "frustrated", action=f"escalating: {task}", bubble=narration, emotion=emotion)
        _send_manager_report(
            worker_id, producer, "escalation", task, narration,
            extra={"severity": severity, "retry_count": retry_count},
            cause=msg,
        )
        return

    print(f"[agent:{worker_id}] {narration}")
    if at_cap:
        if state_path:
            write_state(state_path, "frustrated", action=f"escalating: {task}", bubble=narration, emotion=emotion)
        _send_manager_report(
            worker_id, producer, "escalation", task, narration,
            extra={"severity": severity, "retry_count": retry_count},
            cause=msg,
        )
        return

    if state_path:
        write_state(state_path, "speaking", action=f"re-delegated: {task}", bubble=narration, emotion=emotion)
    # The fix re-assignment stays on the bug_report's chain, which is the
    # original task's chain — so a task and all its retries share one
    # correlation_id.
    print(f"[agent:{worker_id}] re-delegating fix to={coder_id} retry_count={retry_count + 1} "
          f"correlation_id={correlation_of(msg)}")
    producer.send(build_message(
        worker_id, coder_id, "task_assignment",
        {
            "task": f"Fix bug ({severity}): {task}. Repro: {repro}",
            "retry_count": retry_count + 1,
        },
        **reply_ids(msg),
    ))


def handle_test_passed(worker_id, agent_config, llm_client, producer, msg,
                       state_path=None, coding_backend=None):
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
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "happy", action=f"shipped: {task}", bubble=narration, emotion=emotion)
    _send_manager_report(worker_id, producer, "milestone", task, narration, cause=msg)


def handle_task_complete(worker_id, agent_config, llm_client, producer, msg,
                         state_path=None, coding_backend=None):
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
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "speaking", action=f"acknowledged: {task}", bubble=narration, emotion=emotion)


def handle_clarification_request(worker_id, agent_config, llm_client, producer, msg,
                                 state_path=None, coding_backend=None):
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
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        narration, emotion = (
            f"(narration unavailable: {exc}) {sender} is blocked on '{task}': {error}"
        ), "neutral"
    else:
        print(f"[agent:{worker_id}] {narration}")

    if state_path:
        write_state(state_path, "frustrated", action=f"blocker: {task}", bubble=narration, emotion=emotion)
    _send_manager_report(
        worker_id, producer, "blocker", task, narration,
        extra={"blocked_worker": sender, "error": error},
        cause=msg,
    )
