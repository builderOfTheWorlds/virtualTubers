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
import json
import os
import random
import time
import argparse

from message_bus import load_worker_config, build_message, MessageProducer, MessageConsumer
from worker_control import WorkerControl
from llm_client import build_llm_client
from coding_backend import build_coding_backend
from test_runner import run_pytest, workspace_testable
from agent_state import resolve_state_path, write_state
from tmux_control import select_pane, send_keys, send_raw, send_command, TmuxError


# Stub test-outcome heuristic — fallback for workspaces the tester can't
# reach (e.g. the legacy narration-only coder never seeds its volume). Real
# runs go through test_runner.run_pytest. Module-level constants so tuning
# or replacing the heuristic is a one-edit change.
TEST_PASS_PROBABILITY = 0.7
BUG_SEVERITIES = ["low", "medium", "high", "critical"]
BUG_SEVERITY_WEIGHTS = [10, 40, 35, 15]
MAX_BUG_RETRIES = 3

# Tester-side default: where each coder's workspace volume is mounted
# (read-only) inside the tester container; override per-coder via the tester
# config's `agent.workspaces` map.
WORKSPACE_MOUNT_PATTERN = "/data/repos/{coder_id}"

# Agent -> replay pane handoff file (see app/replay_pane.py, which polls it).
REPLAY_REQUEST_FILE_ENV = "REPLAY_REQUEST_FILE"
DEFAULT_REPLAY_REQUEST_FILE = "/tmp/replay_request.json"

# Duet replay relay files (agent -> pane): the cue file carries the latest
# scene cue/end for the ratchet loop in replay_pane.py; the ready file
# accumulates which followers have loaded a duet airing. Same env-override +
# atomic-write convention as REPLAY_REQUEST_FILE_ENV above.
REPLAY_CUE_FILE_ENV = "REPLAY_CUE_FILE"
DEFAULT_REPLAY_CUE_FILE = "/tmp/replay_cue.json"

REPLAY_READY_FILE_ENV = "REPLAY_READY_FILE"
DEFAULT_REPLAY_READY_FILE = "/tmp/replay_ready.json"

# Episode library for viewer-join reruns — same path convention as
# replay_pane.py, which owns the actual resolution/performance.
REPLAY_LIBRARY_ENV = "REPLAY_LIBRARY"
DEFAULT_REPLAY_LIBRARY = "/data/replays"


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


def show_commit_in_filetree(worker_id, workspace):
    """After a real coding-backend run, show the resulting commit on stream:
    run `git show --stat HEAD` in the filetree pane (an interactive shell —
    see demo_filetree_ls) and refocus the editor. Best-effort: no tmux
    session must never fail the run itself."""
    try:
        select_pane("filetree")
        send_command("filetree", f"git -C {workspace} show --stat HEAD")
        select_pane("editor")
    except (TmuxError, OSError) as exc:
        print(f"[agent:{worker_id}] tmux commit replay skipped: {exc}")


def handle_task_assignment(worker_id, agent_config, llm_client, producer, msg,
                           state_path=None, coding_backend=None):
    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    retry_count = payload.get("retry_count", 0)
    reply_to = msg.get("from") or "broadcast"

    if state_path:
        write_state(state_path, "thinking", action=f"working on: {task}")
    demo_editor_note(worker_id, task)
    demo_filetree_ls(worker_id)

    # Real work first (when this coder has a coding backend), narration
    # second — so the narration can describe what actually happened instead
    # of inventing an outcome.
    result = None
    if coding_backend is not None and agent_config.get("role") == "coder":
        if state_path:
            write_state(state_path, "focused", action=f"coding: {task}")
        result = coding_backend.run_task(task)
        print(
            f"[agent:{worker_id}] coding run backend={result.backend} "
            f"success={result.success} commit={result.commit} "
            f"files={result.files_changed} +{result.insertions}/-{result.deletions} "
            f"in {result.duration_s}s"
        )
        # Durable A/B record: message-logger unpacks this type into the
        # coding_backend_runs table (broadcast so the feed pane shows it too).
        producer.send(build_message(
            worker_id, "broadcast", "coding_run_report",
            {"task": task, "retry_count": retry_count, **result.to_payload()},
        ))
        if result.success:
            show_commit_in_filetree(worker_id, coding_backend.workspace)
        else:
            # A failed coding run is a blocker, not a commit to hand over —
            # same clarification_request contract the LLM-failure path uses,
            # so the manager escalates it identically.
            print(f"[agent:{worker_id}] coding run failed: {result.error}")
            if state_path:
                write_state(state_path, "frustrated", action=f"failed: {task}",
                            bubble=f"Ugh... {result.error}")
            producer.send(build_message(
                worker_id, "manager", "clarification_request",
                {"task": task, "error": f"coding backend failed: {result.error}"},
            ))
            return

    if result is not None:
        prompt = (
            f"You've just been assigned this task by {reply_to}: {task}\n\n"
            f"You implemented it for real: commit {result.commit[:8] if result.commit else '?'} "
            f"changed {result.files_changed} file(s) (+{result.insertions}/-{result.deletions}). "
            "Narrate what you did in 1-3 sentences, in character, as if speaking to the stream."
        )
    else:
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
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        if result is not None and result.success:
            # The code exists and is committed; only the narration failed.
            # Hand the commit over anyway — never let a flaky narration LLM
            # strand real, finished work.
            narration = f"(narration unavailable: {exc})"
        else:
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
    # manager can bound the bug/fix loop (MAX_BUG_RETRIES); coder_id rides
    # along so the tester finds the right workspace mount and the manager
    # re-delegates fixes to the right coder now that there are several.
    if agent_config.get("role") == "coder":
        commit_payload = {
            "task": task,
            "commit_message": f"Implement: {task}",
            "narration": narration,
            "retry_count": retry_count,
            "coder_id": worker_id,
        }
        if result is not None:
            commit_payload.update({
                "commit": result.commit,
                "backend": result.backend,
                "files_changed": result.files_changed,
            })
        producer.send(build_message(
            worker_id, "tester", "commit_notification", commit_payload,
        ))


def _resolve_workspace(agent_config, coder_id):
    """Tester-side: where is this coder's workspace mounted in MY container?
    Config map `agent.workspaces: {coder_id: path}` wins; otherwise the
    conventional read-only mount path."""
    workspaces = agent_config.get("workspaces") or {}
    return workspaces.get(coder_id) or WORKSPACE_MOUNT_PATTERN.format(coder_id=coder_id)


def _severity_from_failures(failed_tests):
    """Map real failure counts onto the message schema's severity levels.
    Not trying to be clever — count is the only signal a suite gives us."""
    if len(failed_tests) >= 3:
        return "high"
    if len(failed_tests) == 2:
        return "medium"
    return "low"


def _run_tests_and_report(worker_id, agent_config, llm_client, producer, msg, state_path=None):
    """Shared tester flow for commit_notification / retest_request: REALLY run
    pytest against the coder's workspace mount when it's reachable (see
    test_runner.py), falling back to the _decide_test_outcome() stub when it
    isn't (legacy narration-only coder). Reports `test_passed` or
    `bug_report` to the manager, threading coder_id through so the manager
    re-delegates fixes to the right coder. On the tester's own LLM failure,
    send `clarification_request` to the manager (same contract shape the
    coder uses, so one manager handler covers both origins).
    """
    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    retry_count = payload.get("retry_count", 0)
    coder_id = payload.get("coder_id") or msg.get("from") or "coder"
    sender = msg.get("from") or "broadcast"

    if state_path:
        write_state(state_path, "focused", action=f"testing: {task}")

    # Real run first — its outcome feeds the narration prompt.
    workspace = _resolve_workspace(agent_config, coder_id)
    run = None
    if workspace_testable(workspace):
        print(f"[agent:{worker_id}] running pytest against {workspace} (coder={coder_id})")
        run = run_pytest(workspace)
        if run.ran:
            passed, severity = run.passed, (
                None if run.passed else _severity_from_failures(run.failed_tests)
            )
            print(f"[agent:{worker_id}] pytest verdict: passed={passed} failed={run.failed_tests}")
        else:
            # Suite couldn't produce a verdict (timeout/collection error) —
            # that's a real bug report in itself, highest confidence signal.
            passed, severity = False, "high"
            print(f"[agent:{worker_id}] pytest produced no verdict: {run.summary}")
    else:
        print(f"[agent:{worker_id}] workspace {workspace} not testable — using stub outcome")
        passed, severity = _decide_test_outcome()

    if run is not None and run.ran:
        outcome_desc = (
            "the whole suite passed" if passed
            else f"these tests failed: {', '.join(run.failed_tests) or '(collection error)'}"
        )
        prompt = (
            f"{sender} just handed you a commit for: {task}\n\n"
            f"You really ran the test suite and {outcome_desc}. "
            "Narrate the run in 1-3 sentences, in character, as if speaking to the stream."
        )
    else:
        prompt = (
            f"{sender} just handed you a commit for: {task}\n\n"
            "Narrate running the test suite against it in 1-3 sentences, in character, "
            "as if speaking to the stream."
        )

    try:
        narration = llm_client.complete(
            agent_config.get("system_prompt", ""),
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        if run is not None and run.ran:
            # A real verdict exists — report it with fallback narration
            # rather than dropping it on the floor over a narration failure.
            narration = f"(narration unavailable: {exc})"
        else:
            producer.send(build_message(
                worker_id, "manager", "clarification_request",
                {"task": task, "error": str(exc)},
            ))
            return

    print(f"[agent:{worker_id}] {narration}")
    real_run = run is not None and run.ran
    if passed:
        if state_path:
            write_state(state_path, "happy", action=f"tests passed: {task}", bubble=narration)
        producer.send(build_message(
            worker_id, "manager", "test_passed",
            {
                "task": task,
                "narration": narration,
                "retry_count": retry_count,
                "coder_id": coder_id,
                "real_run": real_run,
            },
        ))
    else:
        if real_run:
            repro = (
                f"pytest in {workspace}: "
                f"{', '.join(run.failed_tests) or run.summary or 'suite failed'}"
            )
        elif run is not None:
            repro = f"pytest could not produce a verdict: {run.summary}"
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
                "coder_id": coder_id,
                "real_run": real_run,
            },
        ))


def handle_commit_notification(worker_id, agent_config, llm_client, producer, msg,
                               state_path=None, coding_backend=None):
    role = agent_config.get("role")
    if role != "tester":
        print(f"[agent:{worker_id}] ignoring commit_notification (role={role}, expected tester)")
        return
    _run_tests_and_report(worker_id, agent_config, llm_client, producer, msg, state_path)


def handle_retest_request(worker_id, agent_config, llm_client, producer, msg,
                          state_path=None, coding_backend=None):
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
        worker_id, coder_id, "task_assignment",
        {
            "task": f"Fix bug ({severity}): {task}. Repro: {repro}",
            "retry_count": retry_count + 1,
        },
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


def handle_operator_message(worker_id, agent_config, llm_client, producer, msg,
                            state_path=None, coding_backend=None):
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


def _resolve_replay_request_file():
    return os.environ.get(REPLAY_REQUEST_FILE_ENV) or DEFAULT_REPLAY_REQUEST_FILE


def _resolve_replay_cue_file():
    return os.environ.get(REPLAY_CUE_FILE_ENV) or DEFAULT_REPLAY_CUE_FILE


def _resolve_replay_ready_file():
    return os.environ.get(REPLAY_READY_FILE_ENV) or DEFAULT_REPLAY_READY_FILE


def _atomic_write_json(path, data):
    """Atomic write of a small agent -> replay-pane relay file (same
    temp+replace pattern as agent_state.py) so a polling pane never reads a
    half-written file. Raises OSError — callers decide how loudly to report
    a failure."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def _read_json_file(path):
    """Best-effort read of a relay file this module owns. Missing or
    corrupt content returns None — callers treat that as "start fresh"
    rather than crashing the handler."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_replay_request(request):
    """Atomic write of the agent -> replay pane request file. Raises
    OSError — callers decide how loudly to report a failure."""
    request_file = _resolve_replay_request_file()
    _atomic_write_json(request_file, request)
    return request_file


def _pick_rerun_episode(payload):
    """Which episode should a viewer-join rerun play? payload.episode wins
    (manual/test injections); otherwise a random pick from the episode
    library — every arrival gets "a rerun", not one hardcoded show. Returns
    None when no episode is available (no library mount, empty library)."""
    requested = payload.get("episode")
    if requested and str(requested).strip():
        return str(requested).strip()
    library = os.environ.get(REPLAY_LIBRARY_ENV) or DEFAULT_REPLAY_LIBRARY
    try:
        episodes = sorted(f[:-5] for f in os.listdir(library) if f.endswith(".json"))
    except OSError:
        return None
    if not episodes:
        return None
    return random.choice(episodes)


def handle_viewer_joined(worker_id, agent_config, llm_client, producer, msg,
                         state_path=None, coding_backend=None):
    """A viewer just showed up in this worker's Twitch chat — sent by the
    twitch-presence service via message-api (docs/twitch_presence.md). Any
    role handles it, in two steps:

    1. Start a rerun for them: queue a Rerun Theater episode for the replay
       pane (payload.episode override, else a random library pick) — the
       rerun is queued FIRST, before the LLM call, so a slow/dead LLM can
       never keep the show from starting. If a request is already pending
       (file exists), it is left alone: a viewer join must not stomp an
       operator's queued episode, and the pane only holds one request anyway.
    2. Greet them: an LLM-written welcome on the console output and avatar
       speech bubble, mentioning the rerun when one was queued.

    Everything is narration-only BY DESIGN: NOTHING is sent back onto the
    bus — viewer arrivals are outside-world events, not pipeline traffic,
    and a burst of joins must never fan out into a burst of bus messages.
    Failures (no episodes, unwritable request file, LLM down) likewise just
    log: a missed hello or rerun is not worth an error message anywhere.
    """
    payload = msg.get("payload", {})
    username = payload.get("username", "someone")

    episode = _pick_rerun_episode(payload)
    queued = False
    if episode is None:
        print(f"[agent:{worker_id}] no replay episodes available — greeting {username!r} only")
    elif os.path.exists(_resolve_replay_request_file()):
        print(f"[agent:{worker_id}] a replay request is already pending — greeting {username!r} only")
    else:
        request = {"episode": episode}
        # voice/narration ride along verbatim, same as handle_replay_request
        # (useful for manual/test injections; twitch-presence never sets them).
        if isinstance(payload.get("voice"), bool):
            request["voice"] = payload["voice"]
        if payload.get("narration"):
            request["narration"] = str(payload["narration"])
        try:
            _write_replay_request(request)
            queued = True
            print(f"[agent:{worker_id}] viewer {username!r} arrived — queued rerun {episode!r}")
        except OSError as exc:
            print(f"[agent:{worker_id}] failed to queue viewer-join rerun: {exc}")

    if queued:
        prompt = (
            f"A viewer named '{username}' just started watching your stream, and "
            "you're firing up a rerun of one of your past coding sessions for them. "
            "Welcome them and introduce the rerun in 1-2 sentences, in character, "
            "as if speaking to the stream."
        )
    else:
        prompt = (
            f"A viewer named '{username}' just started watching your stream. "
            "Give them a short, warm welcome in 1-2 sentences, in character, "
            "as if speaking to the stream."
        )

    if state_path:
        write_state(state_path, "thinking", action=f"greeting {username}")

    try:
        narration = llm_client.complete(
            agent_config.get("system_prompt", ""),
            [{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed greeting {username!r}: {exc}")
        if state_path:
            write_state(state_path, "idle", action=f"missed greeting {username}")
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "happy", action=f"welcomed {username}", bubble=narration)


def _is_valid_cast(cast):
    """True when `cast` is a non-empty dict mapping non-empty string speaker
    names to non-empty string worker ids (duet replay contract). Anything
    else — not a dict, empty dict, non-string/blank keys or values — is
    invalid and must be rejected by handle_replay_request rather than
    forwarded to the replay pane."""
    if not isinstance(cast, dict) or not cast:
        return False
    for key, value in cast.items():
        if not isinstance(key, str) or not key.strip():
            return False
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def handle_replay_request(worker_id, agent_config, llm_client, producer, msg,
                          state_path=None, coding_backend=None):
    """Operator lever: queue a "Rerun Theater" episode for this worker's
    replay pane (docs/replay_pane.md). Any role handles it.

    Deliberately NO LLM call and NO episode-name validation here beyond
    non-empty: the agent only writes the request file; replay_pane.py owns
    resolution (basename-only, inside REPLAY_LIBRARY) so a hostile payload
    can never reach files outside the episode library. Always answers the
    operator so a bad episode name doesn't just vanish.

    Optional payload.cast (duet replay contract): a speaker -> worker_id
    map. When present it must be a non-empty dict of non-empty strings
    (see _is_valid_cast) — valid casts are forwarded verbatim into the
    request file; an invalid cast is rejected with an operator_reply error
    and NOTHING is written (a half-formed duet must never reach the pane).
    Absent cast leaves solo-request behavior byte-for-byte unchanged.
    """
    payload = msg.get("payload", {})
    episode = payload.get("episode")
    if not episode or not str(episode).strip():
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"error": "replay_request needs payload.episode (episode script name)"},
        ))
        return

    cast = payload.get("cast")
    if cast is not None and not _is_valid_cast(cast):
        print(f"[agent:{worker_id}] rejected replay_request: invalid cast {cast!r}")
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"error": "replay_request payload.cast must be a non-empty dict mapping "
                       "non-empty speaker names to non-empty worker ids"},
        ))
        return

    request = {"episode": str(episode).strip()}
    if payload.get("speed") is not None:
        request["speed"] = payload["speed"]
    if payload.get("worker_name"):
        request["worker_name"] = str(payload["worker_name"])
    # voice/narration are interpreted entirely by replay_pane.py (see
    # docs/operator_commands.md) — the agent just forwards them verbatim.
    if isinstance(payload.get("voice"), bool):
        request["voice"] = payload["voice"]
    if payload.get("narration"):
        request["narration"] = str(payload["narration"])
    if cast is not None:
        # Already validated above — forwarded verbatim, per contract.
        request["cast"] = cast

    try:
        _write_replay_request(request)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to queue replay request: {exc}")
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"error": f"could not queue replay: {exc}"},
        ))
        return

    print(f"[agent:{worker_id}] queued replay episode {request['episode']!r}")
    if state_path:
        write_state(state_path, "happy", action="rerun time!",
                    bubble=f"Time for a rerun: {request['episode']}")
    producer.send(build_message(
        worker_id, "operator", "operator_reply",
        {"narration": f"Queued rerun episode {request['episode']!r} - rolling it in the theater pane."},
    ))


# ── Duet replay: director/follower relay handlers ──────────────────────────
# These four handle the bus side of the "duet replay" feature (multi-worker
# Rerun Theater airings). Every one of them is any-role, makes NO LLM call,
# sends NOTHING back onto the bus, and only ever relays the inbound payload
# into a small local JSON file for replay_pane.py to poll — so a bad/partial
# write must log and return, never raise out of the tick loop.

def handle_replay_invite(worker_id, agent_config, llm_client, producer, msg,
                         state_path=None, coding_backend=None):
    """Director -> follower: queue this worker's replay pane into "follow"
    mode for a duet airing. Any role handles it.

    Mirrors handle_viewer_joined's "don't clobber a pending request" rule:
    if a request file is already sitting there (an operator queue, another
    pending invite, ...) the invite is dropped — log and move on. The
    director's own replay_ready timeout is what surfaces this as a refusal;
    this handler does not report anything back itself.
    """
    payload = msg.get("payload", {})
    request_file = _resolve_replay_request_file()
    if os.path.exists(request_file):
        print(f"[agent:{worker_id}] dropped replay_invite — a replay request is already pending")
        return

    # Fields copied verbatim from the invite payload, plus the follower-mode
    # marker replay_pane.py switches on.
    request = dict(payload)
    request["mode"] = "follow"

    try:
        _write_replay_request(request)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to write follower request for replay_invite: {exc}")
        return

    print(
        f"[agent:{worker_id}] queued as follower for airing {request.get('airing_id')!r} "
        f"episode={request.get('episode')!r}"
    )


def handle_replay_ready(worker_id, agent_config, llm_client, producer, msg,
                        state_path=None, coding_backend=None):
    """Follower -> director: mark a worker ready for a duet airing. Any role
    handles it (the director simply is the worker addressed).

    Union/replace rule: a ready file already holding the SAME airing_id
    gets the sender unioned into its worker list; a different, missing, or
    corrupt file is replaced with a fresh single-sender entry for this
    airing. Sender identity always comes from the message envelope `from`,
    never the payload.
    """
    payload = msg.get("payload", {})
    airing_id = payload.get("airing_id")
    sender = msg.get("from")

    ready_file = _resolve_replay_ready_file()
    existing = _read_json_file(ready_file)
    if isinstance(existing, dict) and existing.get("airing_id") == airing_id:
        workers = list(existing.get("workers") or [])
        if sender is not None and sender not in workers:
            workers.append(sender)
    else:
        workers = [sender] if sender is not None else []

    ready = {"airing_id": airing_id, "workers": workers}
    try:
        _atomic_write_json(ready_file, ready)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to write replay_ready state: {exc}")
        return

    print(f"[agent:{worker_id}] replay_ready from {sender!r} for airing {airing_id!r} (workers={workers})")


def handle_replay_cue(worker_id, agent_config, llm_client, producer, msg,
                      state_path=None, coding_backend=None):
    """Director -> follower: authorize performing scenes up to scene_index
    for a duet airing (cue ratchet — followers may perform any scene at or
    below the latest cue). Any role handles it.

    Overwrite-latest semantics: no history is kept, and the file is written
    even when this worker doesn't currently know about a local show (a
    follower whose pane hasn't started polling yet still needs the freshest
    cue waiting for it).
    """
    payload = msg.get("payload", {})
    cue = {
        "airing_id": payload.get("airing_id"),
        "type": "cue",
        "scene_index": payload.get("scene_index"),
    }
    try:
        _atomic_write_json(_resolve_replay_cue_file(), cue)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to write replay_cue: {exc}")
        return

    print(f"[agent:{worker_id}] cue: airing {cue['airing_id']!r} scene_index={cue['scene_index']}")


def handle_replay_end(worker_id, agent_config, llm_client, producer, msg,
                      state_path=None, coding_backend=None):
    """Director -> follower: end a duet airing (finished / ready_timeout /
    aborted), via the same cue file the ratchet loop polls. Any role
    handles it. Overwrite-latest semantics, same as handle_replay_cue.
    """
    payload = msg.get("payload", {})
    end = {
        "airing_id": payload.get("airing_id"),
        "type": "end",
        "reason": payload.get("reason"),
    }
    try:
        _atomic_write_json(_resolve_replay_cue_file(), end)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to write replay_end: {exc}")
        return

    print(f"[agent:{worker_id}] end: airing {end['airing_id']!r} reason={end['reason']!r}")


# Dispatch table — the 8 message types from docs/VTuber_AI_Dev_Team_Concept.md
# §3.4 (status_update is send-only heartbeat traffic; operator_message is
# message-api's default type standing in for direct operator chat), plus the
# viewer-join rerun trigger and the 5 duet replay relay types.
MESSAGE_HANDLERS = {
    "task_assignment": handle_task_assignment,
    "commit_notification": handle_commit_notification,
    "retest_request": handle_retest_request,
    "bug_report": handle_bug_report,
    "test_passed": handle_test_passed,
    "task_complete": handle_task_complete,
    "clarification_request": handle_clarification_request,
    "operator_message": handle_operator_message,
    "replay_request": handle_replay_request,
    "viewer_joined": handle_viewer_joined,
    "replay_invite": handle_replay_invite,
    "replay_ready": handle_replay_ready,
    "replay_cue": handle_replay_cue,
    "replay_end": handle_replay_end,
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

    # A broken coding-backend setup (missing tool, unwritable volume) must
    # not take the worker down — it degrades to narration-only and the
    # operator sees why in the logs.
    try:
        coding_backend = build_coding_backend(config, llm_client)
    except Exception as exc:
        print(f"[agent] WARN coding backend unavailable, running narration-only: {exc}")
        coding_backend = None

    print(f"[agent] {worker_id} started. Config: {args.config}")
    print(f"[agent] Kafka bootstrap={bootstrap_servers} topic={topic}")
    print(f"[agent] LLM provider={config.get('llm', {}).get('provider', 'ollama')}")
    print(f"[agent] coding backend={coding_backend.name if coding_backend else 'none'}")
    print(f"[agent] avatar state file={state_path}")

    write_state(state_path, "idle", action="starting up")

    producer = MessageProducer(bootstrap_servers, topic)
    consumer = MessageConsumer(bootstrap_servers, topic, group_id=f"vtuber-agent-{worker_id}", worker_id=worker_id)
    control = WorkerControl.from_config(config)

    i = 0
    while True:
        if not control.is_enabled(worker_id):
            write_state(state_path, "idle", action="disabled by operator")
            time.sleep(tick_rate_s)
            continue

        for msg in consumer.poll_new():
            print(f"[agent:{worker_id}] received {msg['type']} from {msg['from']}: {msg['payload']}")
            handler = MESSAGE_HANDLERS.get(msg["type"])
            if handler:
                handler(worker_id, agent_config, llm_client, producer, msg, state_path,
                        coding_backend=coding_backend)

        heartbeat = build_message(worker_id, "broadcast", "status_update", {"text": f"heartbeat #{i}"})
        producer.send(heartbeat)
        print(f"[agent:{worker_id}] {heartbeat['type']} #{i}")

        i += 1
        time.sleep(tick_rate_s)


if __name__ == "__main__":
    main()
