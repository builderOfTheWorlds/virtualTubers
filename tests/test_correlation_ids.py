"""
test_correlation_ids.py
Correlation/causation IDs across a dev-team task chain (docs/message_bus.md
"Correlation IDs"): build_message defaults, reply_ids/correlation_of, and
every dev-team handler (coder, tester, manager, operator, manager_report)
propagating the incoming message's chain — including a full
coder -> tester -> manager -> coder bug-retry loop staying one chain.
No Kafka, no LLM, no tmux: fakes throughout.
"""
import pytest

from message_bus import build_message, correlation_of, reply_ids
from agent_handlers import coder, tester
from agent_handlers.coder import handle_task_assignment
from agent_handlers.common import _send_manager_report
from agent_handlers.manager import (
    MAX_BUG_RETRIES,
    handle_bug_report,
    handle_clarification_request,
    handle_task_complete,
    handle_test_passed,
)
from agent_handlers.operator import handle_operator_message
from agent_handlers.tester import handle_commit_notification, handle_retest_request
from coding_backend import TaskResult


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message

    def by_type(self, type_):
        return [m for m in self.sent if m["type"] == type_]

    def one(self, type_):
        found = self.by_type(type_)
        assert len(found) == 1, f"expected one {type_}, got {[m['type'] for m in self.sent]}"
        return found[0]


class FakeLLM:
    def __init__(self, error=None):
        self.error = error

    def complete(self, system_prompt, messages):
        if self.error:
            raise self.error
        return '{"line": "narration", "emotion": "neutral"}'


class FakeBackend:
    name = "fake"
    workspace = "/data/repo"

    def __init__(self, success=True):
        self.success = success

    def run_task(self, task):
        return TaskResult(
            backend="fake", success=self.success, commit="abc1234def" if self.success else None,
            committed=self.success, files_changed=1, insertions=2, deletions=0,
            duration_s=1.0, output="ok", error=None if self.success else "boom",
        )


CODER = {"role": "coder", "system_prompt": "coder"}
TESTER = {"role": "tester", "system_prompt": "tester"}
MANAGER = {"role": "manager", "system_prompt": "manager"}


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    """No tmux and a deterministic, unreachable-workspace test path."""
    monkeypatch.setattr(coder, "demo_editor_note", lambda *a, **k: None)
    monkeypatch.setattr(coder, "demo_filetree_ls", lambda *a, **k: None)
    monkeypatch.setattr(coder, "show_commit_in_filetree", lambda *a, **k: None)
    monkeypatch.setattr(tester, "workspace_testable", lambda path: False)


def _assert_caused_by(sent, cause, correlation_id):
    assert sent["correlation_id"] == correlation_id
    assert sent["causation_id"] == cause["id"]


# ── build_message / helpers ───────────────────────────────────────────────────

def test_build_message_new_chain_correlation_defaults_to_own_id():
    msg = build_message("operator", "coder", "task_assignment", {"task": "t"})
    assert msg["correlation_id"] == msg["id"]
    assert msg["causation_id"] is None


def test_build_message_keeps_explicit_correlation_and_causation():
    msg = build_message("coder", "tester", "commit_notification", {},
                        correlation_id="corr-1", causation_id="cause-1")
    assert msg["correlation_id"] == "corr-1"
    assert msg["causation_id"] == "cause-1"
    assert msg["id"] not in ("corr-1", "cause-1")


def test_build_message_positional_signature_unchanged():
    msg = build_message("a", "b", "t", {"k": 1})
    assert (msg["from"], msg["to"], msg["type"], msg["payload"]) == ("a", "b", "t", {"k": 1})
    assert {"id", "timestamp"} <= set(msg)


@pytest.mark.parametrize("msg, expected", [
    ({"id": "m1", "correlation_id": "c1"}, "c1"),
    ({"id": "m1"}, "m1"),                       # older sender: fall back to own id
    ({"id": "m1", "correlation_id": None}, "m1"),
    ({}, None),
    (None, None),
])
def test_correlation_of_variants(msg, expected):
    assert correlation_of(msg) == expected


@pytest.mark.parametrize("msg, expected", [
    ({"id": "m1", "correlation_id": "c1"}, {"correlation_id": "c1", "causation_id": "m1"}),
    ({"id": "m1"}, {"correlation_id": "m1", "causation_id": "m1"}),
    ({}, {"correlation_id": None, "causation_id": None}),
    (None, {"correlation_id": None, "causation_id": None}),
])
def test_reply_ids_variants(msg, expected):
    assert reply_ids(msg) == expected


def test_reply_ids_without_source_starts_new_chain():
    msg = build_message("x", "y", "t", **reply_ids(None))
    assert msg["correlation_id"] == msg["id"]
    assert msg["causation_id"] is None


# ── per-handler propagation ───────────────────────────────────────────────────

def test_coder_narration_only_propagates_to_task_complete_and_commit():
    producer = FakeProducer()
    assignment = build_message("manager", "coder", "task_assignment", {"task": "t"})
    handle_task_assignment("coder", CODER, FakeLLM(), producer, assignment)

    for type_ in ("task_complete", "commit_notification"):
        _assert_caused_by(producer.one(type_), assignment, assignment["id"])


def test_coder_backend_run_report_carries_chain():
    producer = FakeProducer()
    assignment = build_message("manager", "coder", "task_assignment", {"task": "t"})
    handle_task_assignment("coder", CODER, FakeLLM(), producer, assignment,
                           coding_backend=FakeBackend())

    for type_ in ("coding_run_report", "task_complete", "commit_notification"):
        _assert_caused_by(producer.one(type_), assignment, assignment["id"])


def test_coder_backend_failure_clarification_carries_chain():
    producer = FakeProducer()
    assignment = build_message("manager", "coder", "task_assignment", {"task": "t"})
    handle_task_assignment("coder", CODER, FakeLLM(), producer, assignment,
                           coding_backend=FakeBackend(success=False))

    _assert_caused_by(producer.one("clarification_request"), assignment, assignment["id"])
    _assert_caused_by(producer.one("coding_run_report"), assignment, assignment["id"])


def test_coder_llm_failure_clarification_carries_chain():
    producer = FakeProducer()
    assignment = build_message("manager", "coder", "task_assignment", {"task": "t"})
    handle_task_assignment("coder", CODER, FakeLLM(error=RuntimeError("down")), producer, assignment)

    _assert_caused_by(producer.one("clarification_request"), assignment, assignment["id"])


def test_coder_legacy_message_without_correlation_uses_its_id():
    producer = FakeProducer()
    legacy = {"id": "legacy-1", "from": "manager", "type": "task_assignment", "payload": {"task": "t"}}
    handle_task_assignment("coder", CODER, FakeLLM(), producer, legacy)
    sent = producer.one("task_complete")
    assert sent["correlation_id"] == "legacy-1"
    assert sent["causation_id"] == "legacy-1"


def test_coder_message_without_id_starts_new_chain():
    producer = FakeProducer()
    bare = {"from": "manager", "type": "task_assignment", "payload": {"task": "t"}}
    handle_task_assignment("coder", CODER, FakeLLM(), producer, bare)
    sent = producer.one("task_complete")
    assert sent["correlation_id"] == sent["id"]
    assert sent["causation_id"] is None


@pytest.mark.parametrize("passed, expected_type", [(True, "test_passed"), (False, "bug_report")])
@pytest.mark.parametrize("handler", [handle_commit_notification, handle_retest_request])
def test_tester_verdict_carries_chain(monkeypatch, handler, passed, expected_type):
    monkeypatch.setattr(tester, "_decide_test_outcome", lambda: (passed, None if passed else "high"))
    producer = FakeProducer()
    commit = build_message("coder", "tester", "commit_notification", {"task": "t", "coder_id": "coder"},
                           correlation_id="chain-1", causation_id="x")
    handler("tester", TESTER, FakeLLM(), producer, commit)
    _assert_caused_by(producer.one(expected_type), commit, "chain-1")


def test_tester_llm_failure_clarification_carries_chain(monkeypatch):
    monkeypatch.setattr(tester, "_decide_test_outcome", lambda: (True, None))
    producer = FakeProducer()
    commit = build_message("coder", "tester", "commit_notification", {"task": "t"}, correlation_id="chain-1")
    handle_commit_notification("tester", TESTER, FakeLLM(error=RuntimeError("x")), producer, commit)
    _assert_caused_by(producer.one("clarification_request"), commit, "chain-1")


def test_manager_test_passed_milestone_carries_chain():
    producer = FakeProducer()
    passed = build_message("tester", "manager", "test_passed", {"task": "t"}, correlation_id="chain-1")
    handle_test_passed("manager", MANAGER, FakeLLM(), producer, passed)
    report = producer.one("manager_report")
    assert report["payload"]["report_type"] == "milestone"
    _assert_caused_by(report, passed, "chain-1")


@pytest.mark.parametrize("llm_error", [None, RuntimeError("down")])
def test_manager_blocker_report_carries_chain(llm_error):
    producer = FakeProducer()
    blocked = build_message("coder", "manager", "clarification_request",
                            {"task": "t", "error": "e"}, correlation_id="chain-1")
    handle_clarification_request("manager", MANAGER, FakeLLM(error=llm_error), producer, blocked)
    report = producer.one("manager_report")
    assert report["payload"]["report_type"] == "blocker"
    _assert_caused_by(report, blocked, "chain-1")


@pytest.mark.parametrize("retry_count, llm_error, expected_type", [
    (0, None, "task_assignment"),
    (MAX_BUG_RETRIES, None, "manager_report"),
    (0, RuntimeError("down"), "manager_report"),
])
def test_manager_bug_report_outcomes_carry_chain(retry_count, llm_error, expected_type):
    producer = FakeProducer()
    bug = build_message("tester", "manager", "bug_report",
                        {"task": "t", "severity": "high", "retry_count": retry_count, "coder_id": "coder"},
                        correlation_id="chain-1")
    handle_bug_report("manager", MANAGER, FakeLLM(error=llm_error), producer, bug)
    _assert_caused_by(producer.one(expected_type), bug, "chain-1")


def test_manager_task_complete_still_sends_nothing():
    producer = FakeProducer()
    done = build_message("coder", "manager", "task_complete", {"task": "t"}, correlation_id="chain-1")
    handle_task_complete("manager", MANAGER, FakeLLM(), producer, done)
    assert producer.sent == []


@pytest.mark.parametrize("llm_error", [None, RuntimeError("down")])
def test_operator_reply_carries_chain(llm_error):
    producer = FakeProducer()
    chat = build_message("operator", "coder", "operator_message", {"message": "hi"})
    handle_operator_message("coder", CODER, FakeLLM(error=llm_error), producer, chat)
    _assert_caused_by(producer.one("operator_reply"), chat, chat["id"])


def test_send_manager_report_without_cause_starts_new_chain():
    producer = FakeProducer()
    report = _send_manager_report("manager", producer, "milestone", "t", "n")
    assert report["correlation_id"] == report["id"]
    assert report["causation_id"] is None


# ── full chain ────────────────────────────────────────────────────────────────

def _route(producer, type_):
    """Pop the single outgoing message of `type_` (as if the bus delivered it)."""
    return producer.one(type_)


def test_full_dev_chain_with_bug_retry_is_one_correlation(monkeypatch):
    """operator task -> coder -> tester (bug) -> manager -> coder (fix) ->
    tester (pass) -> manager (milestone): every message shares the original
    task_assignment's correlation_id, and each causation_id points at the
    message that triggered it."""
    outcomes = iter([(False, "high"), (True, None)])
    monkeypatch.setattr(tester, "_decide_test_outcome", lambda: next(outcomes))
    llm = FakeLLM()
    all_sent = []

    def step(handler, worker_id, config, msg, **kwargs):
        producer = FakeProducer()
        handler(worker_id, config, llm, producer, msg, **kwargs)
        all_sent.extend(producer.sent)
        return producer

    origin = build_message("operator", "coder", "task_assignment", {"task": "add divide"})
    chain = origin["id"]

    p1 = step(handle_task_assignment, "coder", CODER, origin, coding_backend=FakeBackend())
    commit1 = _route(p1, "commit_notification")
    _assert_caused_by(commit1, origin, chain)
    step(handle_task_complete, "manager", MANAGER, _route(p1, "task_complete"))

    p2 = step(handle_commit_notification, "tester", TESTER, commit1)
    bug = _route(p2, "bug_report")
    _assert_caused_by(bug, commit1, chain)

    p3 = step(handle_bug_report, "manager", MANAGER, bug)
    fix = _route(p3, "task_assignment")
    _assert_caused_by(fix, bug, chain)
    assert fix["payload"]["retry_count"] == 1

    p4 = step(handle_task_assignment, "coder", CODER, fix, coding_backend=FakeBackend())
    commit2 = _route(p4, "commit_notification")
    _assert_caused_by(commit2, fix, chain)
    assert commit2["payload"]["retry_count"] == 1

    p5 = step(handle_commit_notification, "tester", TESTER, commit2)
    passed = _route(p5, "test_passed")
    _assert_caused_by(passed, commit2, chain)

    p6 = step(handle_test_passed, "manager", MANAGER, passed)
    milestone = _route(p6, "manager_report")
    _assert_caused_by(milestone, passed, chain)

    assert len(all_sent) == 10  # 3 coder msgs x2, bug, fix, test_passed, milestone
    assert {m["correlation_id"] for m in all_sent} == {chain}
    ids = {m["id"] for m in all_sent} | {origin["id"]}
    assert all(m["causation_id"] in ids for m in all_sent)


def test_bug_retries_up_to_escalation_stay_one_chain(monkeypatch):
    """Every retry fails until MAX_BUG_RETRIES: the escalation manager_report
    still carries the original task's correlation_id."""
    monkeypatch.setattr(tester, "_decide_test_outcome", lambda: (False, "medium"))
    llm = FakeLLM()
    origin = build_message("operator", "coder", "task_assignment", {"task": "t"})
    msg = origin
    for _ in range(MAX_BUG_RETRIES + 1):
        p = FakeProducer()
        handle_task_assignment("coder", CODER, llm, p, msg)
        commit = p.one("commit_notification")
        p = FakeProducer()
        handle_commit_notification("tester", TESTER, llm, p, commit)
        bug = p.one("bug_report")
        p = FakeProducer()
        handle_bug_report("manager", MANAGER, llm, p, bug)
        if p.by_type("manager_report"):
            escalation = p.one("manager_report")
            break
        msg = p.one("task_assignment")
        assert msg["correlation_id"] == origin["id"]
    else:
        pytest.fail("manager never escalated")

    assert escalation["payload"]["report_type"] == "escalation"
    assert escalation["payload"]["retry_count"] == MAX_BUG_RETRIES
    assert escalation["correlation_id"] == origin["id"]
    assert escalation["causation_id"] == bug["id"]
