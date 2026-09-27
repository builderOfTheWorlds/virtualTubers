"""
agent_handlers/tester.py
Tester-side handlers (split out of app/agent.py): `commit_notification` and
`retest_request` both run the shared _run_tests_and_report flow (real pytest
against the coder's workspace mount, stub outcome as fallback) and report
`test_passed` / `bug_report` to the manager.
"""
import random

from message_bus import build_message, correlation_of, reply_ids
from agent_state import write_state
from test_runner import run_pytest, workspace_testable

from .common import _complete_with_emotion


# Stub test-outcome heuristic — fallback for workspaces the tester can't
# reach (e.g. the legacy narration-only coder never seeds its volume). Real
# runs go through test_runner.run_pytest. Module-level constants so tuning
# or replacing the heuristic is a one-edit change.
TEST_PASS_PROBABILITY = 0.7
BUG_SEVERITIES = ["low", "medium", "high", "critical"]
BUG_SEVERITY_WEIGHTS = [10, 40, 35, 15]

# Tester-side default: where each coder's workspace volume is mounted
# (read-only) inside the tester container; override per-coder via the tester
# config's `agent.workspaces` map.
WORKSPACE_MOUNT_PATTERN = "/data/repos/{coder_id}"


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


def _send(producer, message):
    """Send `message` and return it (regardless of what producer.send returns)."""
    producer.send(message)
    return message


def _run_tests_and_report(worker_id, agent_config, llm_client, producer, msg, state_path=None,
                          report_to="manager", extra=None):
    """Shared tester flow for commit_notification / retest_request: REALLY run
    pytest against the coder's workspace mount when it's reachable (see
    test_runner.py), falling back to the _decide_test_outcome() stub when it
    isn't (legacy narration-only coder). Reports `test_passed` or
    `bug_report` to the manager, threading coder_id through so the manager
    re-delegates fixes to the right coder. On the tester's own LLM failure,
    send `clarification_request` to the manager (same contract shape the
    coder uses, so one manager handler covers both origins).

    `report_to` is the manager's worker id (the office Tester reports to the
    Tech Lead seat); `extra` keys are added to the verdict payload (office
    chain context such as pr / issue). Returns the message sent (None when
    nothing was sent).
    """
    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    retry_count = payload.get("retry_count", 0)
    coder_id = payload.get("coder_id") or msg.get("from") or "coder"
    sender = msg.get("from") or "broadcast"
    ids = reply_ids(msg)
    correlation_id = correlation_of(msg)

    if state_path:
        write_state(state_path, "focused", action=f"testing: {task}")

    # Real run first — its outcome feeds the narration prompt.
    workspace = _resolve_workspace(agent_config, coder_id)
    run = None
    if workspace_testable(workspace):
        print(f"[agent:{worker_id}] running pytest against {workspace} "
              f"(coder={coder_id} correlation_id={correlation_id})")
        run = run_pytest(workspace)
        if run.ran:
            passed, severity = run.passed, (
                None if run.passed else _severity_from_failures(run.failed_tests)
            )
            print(f"[agent:{worker_id}] pytest verdict: passed={passed} failed={run.failed_tests} "
                  f"correlation_id={correlation_id}")
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
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        if run is not None and run.ran:
            # A real verdict exists — report it with fallback narration
            # rather than dropping it on the floor over a narration failure.
            narration, emotion = f"(narration unavailable: {exc})", "neutral"
        else:
            return _send(producer, build_message(
                worker_id, report_to, "clarification_request",
                {"task": task, "error": str(exc)},
                **ids,
            ))

    print(f"[agent:{worker_id}] {narration}")
    real_run = run is not None and run.ran
    if passed:
        if state_path:
            write_state(state_path, "happy", action=f"tests passed: {task}", bubble=narration, emotion=emotion)
        return _send(producer, build_message(
            worker_id, report_to, "test_passed",
            {
                "task": task,
                "narration": narration,
                "retry_count": retry_count,
                "coder_id": coder_id,
                "real_run": real_run,
                **{k: v for k, v in (extra or {}).items() if v is not None},
            },
            **ids,
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
            write_state(state_path, "speaking", action=f"found a bug: {task}", bubble=narration, emotion=emotion)
        return _send(producer, build_message(
            worker_id, report_to, "bug_report",
            {
                "task": task,
                "severity": severity,
                "repro": repro,
                "narration": narration,
                "retry_count": retry_count,
                "coder_id": coder_id,
                "real_run": real_run,
                **{k: v for k, v in (extra or {}).items() if v is not None},
            },
            **ids,
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
