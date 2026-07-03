"""Tests for agent.py's coding-backend integration: the coder's real-work
flow around task_assignment, the tester's real-pytest verdict path, and
coder_id routing through the bug/fix loop."""
import pytest

import agent
from agent import (
    _resolve_workspace,
    _run_tests_and_report,
    _severity_from_failures,
    handle_bug_report,
    handle_task_assignment,
)
from coding_backend import TaskResult
from test_runner import TestRunResult


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message

    def by_type(self, type_):
        return [m for m in self.sent if m["type"] == type_]


class FakeLLM:
    def __init__(self, response="narration", error=None):
        self.response = response
        self.error = error

    def complete(self, system_prompt, messages):
        if self.error:
            raise self.error
        return self.response


class FakeBackend:
    name = "fake"
    workspace = "/data/repo"

    def __init__(self, result):
        self.result = result
        self.tasks = []

    def run_task(self, task):
        self.tasks.append(task)
        return self.result


def _success_result(**overrides):
    fields = dict(
        backend="fake", success=True, commit="abc1234def", committed=True,
        files_changed=1, insertions=5, deletions=1, duration_s=2.0, output="ok",
    )
    fields.update(overrides)
    return TaskResult(**fields)


CODER_CONFIG = {"role": "coder", "system_prompt": "You are NYX-1."}
TASK_MSG = {"from": "manager", "type": "task_assignment", "payload": {"task": "fix divide"}}


def test_coder_with_backend_reports_run_and_hands_off_commit():
    producer = FakeProducer()
    backend = FakeBackend(_success_result())

    handle_task_assignment("coder-native", CODER_CONFIG, FakeLLM(), producer,
                           TASK_MSG, coding_backend=backend)

    assert backend.tasks == ["fix divide"]
    reports = producer.by_type("coding_run_report")
    assert len(reports) == 1
    assert reports[0]["payload"]["backend"] == "fake"
    assert reports[0]["payload"]["success"] is True

    commits = producer.by_type("commit_notification")
    assert len(commits) == 1
    assert commits[0]["payload"]["coder_id"] == "coder-native"
    assert commits[0]["payload"]["commit"] == "abc1234def"
    assert producer.by_type("task_complete")


def test_coder_backend_failure_sends_clarification_not_commit():
    producer = FakeProducer()
    backend = FakeBackend(_success_result(success=False, commit=None, error="tool exploded"))

    handle_task_assignment("coder-native", CODER_CONFIG, FakeLLM(), producer,
                           TASK_MSG, coding_backend=backend)

    clarifications = producer.by_type("clarification_request")
    assert len(clarifications) == 1
    assert clarifications[0]["to"] == "manager"
    assert "tool exploded" in clarifications[0]["payload"]["error"]
    assert not producer.by_type("commit_notification")
    assert not producer.by_type("task_complete")
    # The failed run is still durably reported for the A/B record.
    assert producer.by_type("coding_run_report")


def test_coder_narration_failure_still_hands_off_successful_commit():
    producer = FakeProducer()
    backend = FakeBackend(_success_result())

    handle_task_assignment("coder-native", CODER_CONFIG,
                           FakeLLM(error=RuntimeError("llm down")), producer,
                           TASK_MSG, coding_backend=backend)

    commits = producer.by_type("commit_notification")
    assert len(commits) == 1
    assert "narration unavailable" in commits[0]["payload"]["narration"]


def test_coder_without_backend_keeps_legacy_flow():
    producer = FakeProducer()

    handle_task_assignment("coder", CODER_CONFIG, FakeLLM(), producer, TASK_MSG,
                           coding_backend=None)

    assert not producer.by_type("coding_run_report")
    commits = producer.by_type("commit_notification")
    assert len(commits) == 1
    assert commits[0]["payload"]["coder_id"] == "coder"
    assert "commit" not in commits[0]["payload"]


def test_resolve_workspace_prefers_config_map_over_convention():
    config = {"workspaces": {"coder-native": "/custom/path"}}
    assert _resolve_workspace(config, "coder-native") == "/custom/path"
    assert _resolve_workspace(config, "coder-aider") == "/data/repos/coder-aider"
    assert _resolve_workspace({}, "coder-x") == "/data/repos/coder-x"


@pytest.mark.parametrize("failed,expected", [
    (["t1"], "low"),
    (["t1", "t2"], "medium"),
    (["t1", "t2", "t3"], "high"),
    (["t1", "t2", "t3", "t4"], "high"),
])
def test_severity_from_failures_scales_with_count(failed, expected):
    assert _severity_from_failures(failed) == expected


TESTER_CONFIG = {"role": "tester", "system_prompt": "You are TESS-3."}
COMMIT_MSG = {
    "from": "coder-native", "type": "commit_notification",
    "payload": {"task": "fix divide", "coder_id": "coder-native", "retry_count": 1},
}


def test_tester_real_pass_reports_test_passed_with_coder_id(monkeypatch):
    monkeypatch.setattr(agent, "workspace_testable", lambda ws: True)
    monkeypatch.setattr(agent, "run_pytest", lambda ws: TestRunResult(
        ran=True, passed=True, exit_code=0, summary="5 passed"))
    producer = FakeProducer()

    _run_tests_and_report("tester", TESTER_CONFIG, FakeLLM(), producer, COMMIT_MSG)

    passed = producer.by_type("test_passed")
    assert len(passed) == 1
    assert passed[0]["payload"]["coder_id"] == "coder-native"
    assert passed[0]["payload"]["real_run"] is True
    assert passed[0]["payload"]["retry_count"] == 1


def test_tester_real_failure_reports_bug_with_failed_tests(monkeypatch):
    monkeypatch.setattr(agent, "workspace_testable", lambda ws: True)
    monkeypatch.setattr(agent, "run_pytest", lambda ws: TestRunResult(
        ran=True, passed=False, exit_code=1,
        failed_tests=["tests/test_calculator.py::test_divide_by_zero_raises"],
        summary="1 failed"))
    producer = FakeProducer()

    _run_tests_and_report("tester", TESTER_CONFIG, FakeLLM(), producer, COMMIT_MSG)

    bugs = producer.by_type("bug_report")
    assert len(bugs) == 1
    assert bugs[0]["payload"]["severity"] == "low"
    assert "test_divide_by_zero_raises" in bugs[0]["payload"]["repro"]
    assert bugs[0]["payload"]["real_run"] is True


def test_tester_unreachable_workspace_falls_back_to_stub(monkeypatch):
    monkeypatch.setattr(agent, "workspace_testable", lambda ws: False)
    monkeypatch.setattr(agent, "_decide_test_outcome", lambda: (True, None))
    producer = FakeProducer()

    _run_tests_and_report("tester", TESTER_CONFIG, FakeLLM(), producer, COMMIT_MSG)

    passed = producer.by_type("test_passed")
    assert len(passed) == 1
    assert passed[0]["payload"]["real_run"] is False


def test_manager_redelegates_fix_to_originating_coder():
    producer = FakeProducer()
    msg = {
        "from": "tester", "type": "bug_report",
        "payload": {"task": "fix divide", "severity": "low", "repro": "r",
                     "retry_count": 0, "coder_id": "coder-aider"},
    }

    handle_bug_report("manager", {"role": "manager", "system_prompt": ""},
                      FakeLLM(), producer, msg)

    assignments = producer.by_type("task_assignment")
    assert len(assignments) == 1
    assert assignments[0]["to"] == "coder-aider"
    assert assignments[0]["payload"]["retry_count"] == 1


def test_manager_redelegation_defaults_to_legacy_coder_without_coder_id():
    producer = FakeProducer()
    msg = {
        "from": "tester", "type": "bug_report",
        "payload": {"task": "t", "severity": "low", "repro": "r", "retry_count": 0},
    }

    handle_bug_report("manager", {"role": "manager", "system_prompt": ""},
                      FakeLLM(), producer, msg)

    assert producer.by_type("task_assignment")[0]["to"] == "coder"
