"""End-to-end dev-team loop (docs/agent_flow_reference.md §5.1) across
simulated coder / tester / manager workers on an in-memory bus.

Real handler code runs end to end; only the edges are faked (LLM, coding
backend, pytest, tmux). See tests/e2e_harness.py and docs/e2e_tests.md.
"""
import pytest

from agent_handlers.manager import MAX_BUG_RETRIES
from e2e_harness import (
    OPERATOR,
    E2EHarness,
    FakeTestRunner,
    ScriptedLLM,
    failed_result,
)

pytestmark = pytest.mark.integration

TASK = "fix divide by zero"


def make_harness(tmp_path, monkeypatch, outcomes=(), default=True):
    return E2EHarness(tmp_path, monkeypatch,
                      test_runner=FakeTestRunner(outcomes=outcomes, default=default))


def dev_cycle(coder="coder", task_complete_to=OPERATOR):
    """The (from, to, type) triples one coder run + passing/failing test
    produces up to (and including) the tester's verdict."""
    return [
        (coder, "broadcast", "coding_run_report"),
        (coder, task_complete_to, "task_complete"),
        (coder, "tester", "commit_notification"),
    ]


# ── happy path ───────────────────────────────────────────────────────────────
def test_happy_path_task_reaches_milestone_on_one_chain(tmp_path, monkeypatch):
    h = make_harness(tmp_path, monkeypatch, outcomes=[True])
    team = h.dev_team()
    root = h.assign("coder", TASK)

    h.bus.run_until_quiet()

    assert h.bus.triples() == [
        (OPERATOR, "coder", "task_assignment"),
        *dev_cycle(),
        ("tester", "manager", "test_passed"),
        ("manager", OPERATOR, "manager_report"),
    ]
    chain = h.bus.assert_chain_consistent(root)
    assert chain == h.bus.log  # every message belongs to the task's chain

    report = h.bus.operator_inbox[-1]
    assert report["type"] == "manager_report"
    assert report["payload"]["report_type"] == "milestone"
    assert report["payload"]["task"] == TASK
    assert h.bus.lineage(report) == [
        "task_assignment", "commit_notification", "test_passed", "manager_report"]

    commit = h.bus.of_type("commit_notification")[0]["payload"]
    assert commit["coder_id"] == "coder"
    assert commit["retry_count"] == 0
    assert commit["commit"] == "coder-c1"
    passed = h.bus.of_type("test_passed")[0]["payload"]
    assert passed["real_run"] is True and passed["coder_id"] == "coder"

    # Faked edges were actually exercised.
    assert team["coder"].coding_backend.tasks == [TASK]
    assert h.test_runner.runs == ["/data/repos/coder"]
    assert any(name == "send_keys" for name, _ in h.tmux_calls)
    # The broadcast coding_run_report reaches every consumer (sender included)
    # and no handler reacts to it.
    run_report_recipients = sorted(w for w, m in h.bus.deliveries if m["type"] == "coding_run_report")
    assert run_report_recipients == ["coder", "manager", "tester"]
    assert h.bus.undelivered == []


def test_task_complete_to_manager_triggers_no_send(tmp_path, monkeypatch):
    """Invariant (§8): task_complete is acknowledged, never answered. When the
    manager is the assigner, the coder's task_complete goes to the manager,
    who narrates (one LLM call) and sends nothing."""
    h = make_harness(tmp_path, monkeypatch)
    team = h.dev_team()
    msg = h.bus.inject("coder", "manager", "task_complete", {"task": TASK, "narration": "done"})

    h.bus.run_until_quiet()

    assert h.bus.log == [msg]
    assert len(team["manager"].llm.prompts) == 1
    assert "just finished the task" in team["manager"].llm.prompts[0]


# ── one failure, then pass ───────────────────────────────────────────────────
def test_one_bug_then_fix_passes_on_same_chain(tmp_path, monkeypatch):
    h = make_harness(tmp_path, monkeypatch, outcomes=[False, True])
    team = h.dev_team()
    root = h.assign("coder", TASK)

    h.bus.run_until_quiet()

    assert h.bus.triples() == [
        (OPERATOR, "coder", "task_assignment"),
        *dev_cycle(),
        ("tester", "manager", "bug_report"),
        ("manager", "coder", "task_assignment"),
        # The fix came from the manager, so task_complete goes back to it
        # (reply-to-sender) -- and the manager sends nothing for it.
        *dev_cycle(task_complete_to="manager"),
        ("tester", "manager", "test_passed"),
        ("manager", OPERATOR, "manager_report"),
    ]
    h.bus.assert_chain_consistent(root)
    assert all(m["correlation_id"] == root["id"] for m in h.bus.log)

    bug = h.bus.of_type("bug_report")[0]
    assert bug["payload"]["retry_count"] == 0
    assert bug["payload"]["severity"] == "low"  # one failed test
    fix = h.bus.of_type("task_assignment")[1]
    assert fix["causation_id"] == bug["id"]
    assert fix["payload"]["retry_count"] == 1
    assert fix["payload"]["task"].startswith(f"Fix bug (low): {TASK}")

    second_commit = h.bus.of_type("commit_notification")[1]["payload"]
    assert second_commit["retry_count"] == 1
    assert second_commit["commit"] == "coder-c2"
    assert h.bus.of_type("test_passed")[0]["payload"]["retry_count"] == 1

    report = h.bus.operator_inbox[-1]
    assert report["payload"]["report_type"] == "milestone"
    assert h.bus.lineage(report) == [
        "task_assignment", "commit_notification", "bug_report", "task_assignment",
        "commit_notification", "test_passed", "manager_report"]
    assert len(team["coder"].coding_backend.tasks) == 2


# ── retry cap ────────────────────────────────────────────────────────────────
def test_retry_cap_escalates_and_stops_reassigning(tmp_path, monkeypatch):
    h = make_harness(tmp_path, monkeypatch, default=False)  # tests never pass
    team = h.dev_team()
    root = h.assign("coder", TASK)

    h.bus.run_until_quiet()

    assignments = h.bus.of_type("task_assignment")
    assert [m["payload"].get("retry_count", 0) for m in assignments] == list(range(MAX_BUG_RETRIES + 1))
    assert [m["from"] for m in assignments] == [OPERATOR] + ["manager"] * MAX_BUG_RETRIES
    bugs = h.bus.of_type("bug_report")
    assert [m["payload"]["retry_count"] for m in bugs] == list(range(MAX_BUG_RETRIES + 1))

    # Last word is the escalation; no task_assignment after the capped bug.
    assert h.bus.triples()[-2:] == [
        ("tester", "manager", "bug_report"),
        ("manager", OPERATOR, "manager_report"),
    ]
    reports = h.bus.of_type("manager_report")
    assert len(reports) == 1
    assert reports[0]["payload"]["report_type"] == "escalation"
    assert reports[0]["payload"]["retry_count"] == MAX_BUG_RETRIES
    assert reports[0]["causation_id"] == bugs[-1]["id"]
    assert h.bus.of_type("test_passed") == []

    h.bus.assert_chain_consistent(root)
    assert {m["correlation_id"] for m in h.bus.log} == {root["id"]}
    assert len(team["coder"].coding_backend.tasks) == MAX_BUG_RETRIES + 1


# ── coding backend failure ───────────────────────────────────────────────────
def test_backend_failure_becomes_blocker_without_reassign(tmp_path, monkeypatch):
    h = make_harness(tmp_path, monkeypatch)
    h.add_coder("coder", results=[failed_result("aider exited 1")])
    h.add_tester()
    h.add_manager()
    root = h.assign("coder", TASK)

    h.bus.run_until_quiet()

    assert h.bus.triples() == [
        (OPERATOR, "coder", "task_assignment"),
        ("coder", "broadcast", "coding_run_report"),
        ("coder", "manager", "clarification_request"),
        ("manager", OPERATOR, "manager_report"),
    ]
    h.bus.assert_chain_consistent(root)
    run_report = h.bus.of_type("coding_run_report")[0]["payload"]
    assert run_report["success"] is False and run_report["error"] == "aider exited 1"
    clarification = h.bus.of_type("clarification_request")[0]["payload"]
    assert clarification["error"] == "coding backend failed: aider exited 1"

    report = h.bus.operator_inbox[-1]["payload"]
    assert report["report_type"] == "blocker"
    assert report["blocked_worker"] == "coder"
    assert report["error"] == "coding backend failed: aider exited 1"
    assert len(h.bus.of_type("task_assignment")) == 1  # no auto-reassign
    assert h.test_runner.runs == []


# ── manager LLM failures still report ────────────────────────────────────────
def test_manager_llm_down_on_bug_report_escalates_with_fallback(tmp_path, monkeypatch):
    h = make_harness(tmp_path, monkeypatch, outcomes=[False])
    h.dev_team(manager_llm=ScriptedLLM("manager", down=True))
    root = h.assign("coder", TASK)

    h.bus.run_until_quiet()

    assert h.bus.triples()[-2:] == [
        ("tester", "manager", "bug_report"),
        ("manager", OPERATOR, "manager_report"),
    ]
    report = h.bus.operator_inbox[-1]
    assert report["payload"]["report_type"] == "escalation"
    assert report["payload"]["narration"].startswith("(narration unavailable:")
    assert report["payload"]["retry_count"] == 0
    assert len(h.bus.of_type("task_assignment")) == 1  # escalated, not re-delegated
    h.bus.assert_chain_consistent(root)


def test_manager_llm_down_on_blocker_still_reports(tmp_path, monkeypatch):
    h = make_harness(tmp_path, monkeypatch)
    h.add_coder("coder", results=[failed_result("tool crashed")])
    h.add_tester()
    h.add_manager(llm=ScriptedLLM("manager", down=True))
    root = h.assign("coder", TASK)

    h.bus.run_until_quiet()

    report = h.bus.operator_inbox[-1]
    assert report["type"] == "manager_report"
    assert report["payload"]["report_type"] == "blocker"
    assert report["payload"]["narration"].startswith("(narration unavailable:")
    assert "tool crashed" in report["payload"]["narration"]
    assert h.bus.lineage(report) == ["task_assignment", "clarification_request", "manager_report"]
    h.bus.assert_chain_consistent(root)


# Regression: handle_test_passed used to return without reporting when the
# manager's LLM failed; it now falls back like the other manager handlers.
def test_manager_llm_down_on_test_passed_still_reports_milestone(tmp_path, monkeypatch):
    h = make_harness(tmp_path, monkeypatch, outcomes=[True])
    h.dev_team(manager_llm=ScriptedLLM("manager", down=True))
    h.assign("coder", TASK)

    h.bus.run_until_quiet()

    reports = [m for m in h.bus.operator_inbox if m["type"] == "manager_report"]
    assert len(reports) == 1
    assert reports[0]["payload"]["report_type"] == "milestone"


# ── other LLM failure paths on the chain ─────────────────────────────────────
def test_tester_llm_down_still_reports_real_verdict(tmp_path, monkeypatch):
    """A real pytest verdict is never dropped over a narration failure."""
    h = make_harness(tmp_path, monkeypatch, outcomes=[True])
    h.dev_team(tester_llm=ScriptedLLM("tester", down=True))
    root = h.assign("coder", TASK)

    h.bus.run_until_quiet()

    passed = h.bus.of_type("test_passed")
    assert len(passed) == 1
    assert passed[0]["payload"]["narration"].startswith("(narration unavailable:")
    assert h.bus.operator_inbox[-1]["payload"]["report_type"] == "milestone"
    h.bus.assert_chain_consistent(root)


def test_coder_llm_down_after_real_commit_still_hands_over(tmp_path, monkeypatch):
    h = make_harness(tmp_path, monkeypatch, outcomes=[True])
    h.dev_team(coder_llm=ScriptedLLM("coder", down=True))
    h.assign("coder", TASK)

    h.bus.run_until_quiet()

    commit = h.bus.of_type("commit_notification")
    assert len(commit) == 1
    assert commit[0]["payload"]["narration"].startswith("(narration unavailable:")
    assert h.bus.operator_inbox[-1]["payload"]["report_type"] == "milestone"


@pytest.mark.xfail(strict=True, reason=(
    "Known quirk app/agent_handlers/coder.py:149: a narration-only coder "
    "(no coding backend) whose LLM fails sends clarification_request to the "
    "task's SENDER (reply_to), not to the manager. For an operator-assigned "
    "task it goes straight to 'operator', so the manager never sees the "
    "blocker and no manager_report is produced -- unlike the backend-failure "
    "path (coder.py:116, always 'manager'). Documented as current behaviour "
    "in docs/agent_flow_reference.md §3 and docs/e2e_tests.md."))
def test_narration_only_coder_llm_failure_reaches_manager(tmp_path, monkeypatch):
    h = make_harness(tmp_path, monkeypatch)
    h.add_worker("coder", "coder", llm=ScriptedLLM("coder", down=True))  # no backend
    h.add_tester()
    h.add_manager()
    h.assign("coder", TASK)

    h.bus.run_until_quiet()

    clarifications = h.bus.of_type("clarification_request")
    assert [m["to"] for m in clarifications] == ["manager"]
    assert h.bus.operator_inbox[-1]["type"] == "manager_report"


# ── concurrency ──────────────────────────────────────────────────────────────
def test_two_concurrent_tasks_keep_distinct_chains(tmp_path, monkeypatch):
    # coder-aider's first run fails once; coder-native passes straight away.
    runner = FakeTestRunner(outcomes={"/data/repos/coder-aider": [False, True]}, default=True)
    h = E2EHarness(tmp_path, monkeypatch, test_runner=runner)
    team = h.dev_team(coders=("coder-native", "coder-aider"))
    task_a, task_b = "add modulo", "fix divide"
    root_a = h.assign("coder-native", task_a)
    root_b = h.assign("coder-aider", task_b)

    h.bus.run_until_quiet()

    assert root_a["correlation_id"] != root_b["correlation_id"]
    chain_a = h.bus.assert_chain_consistent(root_a)
    chain_b = h.bus.assert_chain_consistent(root_b)
    assert len(chain_a) + len(chain_b) == len(h.bus.log)  # nothing orphaned

    # Messages were interleaved on the bus but never crossed chains.
    first_b = h.bus.log.index(root_b)
    assert any(h.bus.log.index(m) > first_b for m in chain_a[1:])
    for chain, coder, task in ((chain_a, "coder-native", task_a), (chain_b, "coder-aider", task_b)):
        for m in chain:
            if m["type"] in ("commit_notification", "coding_run_report", "task_complete"):
                assert m["from"] == coder
            if m["type"] in ("test_passed", "bug_report", "commit_notification"):
                assert m["payload"]["coder_id"] == coder
            assert task in m["payload"].get("task", task)

    assert [m["type"] for m in chain_a if m["type"] in ("bug_report", "test_passed")] == ["test_passed"]
    assert [m["type"] for m in chain_b if m["type"] in ("bug_report", "test_passed")] == [
        "bug_report", "test_passed"]
    fix = [m for m in chain_b if m["type"] == "task_assignment"][1]
    assert fix["to"] == "coder-aider" and fix["payload"]["retry_count"] == 1

    reports = {m["correlation_id"]: m for m in h.bus.operator_inbox if m["type"] == "manager_report"}
    assert set(reports) == {root_a["id"], root_b["id"]}
    assert reports[root_a["id"]]["payload"]["task"] == task_a
    assert all(r["payload"]["report_type"] == "milestone" for r in reports.values())

    assert team["coder-native"].coding_backend.tasks == [task_a]
    assert len(team["coder-aider"].coding_backend.tasks) == 2
    assert sorted(runner.runs) == sorted(
        ["/data/repos/coder-native", "/data/repos/coder-aider", "/data/repos/coder-aider"])
