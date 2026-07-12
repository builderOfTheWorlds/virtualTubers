import pytest

import agent
from agent import (
    MESSAGE_HANDLERS,
    demo_editor_note,
    demo_filetree_ls,
    handle_bug_report,
    handle_clarification_request,
    handle_commit_notification,
    handle_operator_message,
    handle_retest_request,
    handle_task_assignment,
    handle_task_complete,
    handle_test_passed,
)
from agent_state import read_state
from tmux_control import TmuxError


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def complete(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if self.error:
            raise self.error
        return self.response


def test_handle_task_assignment_success_sends_task_complete():
    producer = FakeProducer()
    llm = FakeLLM(response="Digging into the login bug now.")
    msg = {"from": "manager", "type": "task_assignment", "payload": {"task": "fix the login bug"}}

    handle_task_assignment("coder", {"system_prompt": "You are KODI-7."}, llm, producer, msg)

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["from"] == "coder"
    assert sent["to"] == "manager"
    assert sent["type"] == "task_complete"
    assert sent["payload"]["narration"] == "Digging into the login bug now."
    assert llm.calls[0][0] == "You are KODI-7."


def test_handle_task_assignment_llm_failure_sends_clarification_request():
    producer = FakeProducer()
    llm = FakeLLM(error=RuntimeError("connection refused"))
    msg = {"from": "manager", "type": "task_assignment", "payload": {"task": "fix the login bug"}}

    handle_task_assignment("coder", {"system_prompt": "You are KODI-7."}, llm, producer, msg)

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["type"] == "clarification_request"
    assert "connection refused" in sent["payload"]["error"]


def test_handle_task_assignment_defaults_reply_to_broadcast_when_from_missing():
    producer = FakeProducer()
    llm = FakeLLM(response="On it.")
    msg = {"type": "task_assignment", "payload": {"task": "say hello"}}

    handle_task_assignment("coder", {"system_prompt": ""}, llm, producer, msg)

    assert producer.sent[0]["to"] == "broadcast"


def test_handle_task_assignment_defaults_task_description_when_missing():
    producer = FakeProducer()
    llm = FakeLLM(response="Sure.")
    msg = {"from": "manager", "type": "task_assignment", "payload": {}}

    handle_task_assignment("coder", {"system_prompt": ""}, llm, producer, msg)

    assert "no task description" in producer.sent[0]["payload"]["task"]


def test_handle_task_assignment_success_writes_speaking_state_with_bubble(tmp_path):
    state_path = str(tmp_path / "state.json")
    producer = FakeProducer()
    llm = FakeLLM(response="Digging into the login bug now.")
    msg = {"from": "manager", "type": "task_assignment", "payload": {"task": "fix the login bug"}}

    handle_task_assignment("coder", {"system_prompt": ""}, llm, producer, msg, state_path)

    state = read_state(state_path)
    assert state["expression"] == "speaking"
    assert state["bubble"] == "Digging into the login bug now."


def test_handle_task_assignment_failure_writes_frustrated_state_with_bubble(tmp_path):
    state_path = str(tmp_path / "state.json")
    producer = FakeProducer()
    llm = FakeLLM(error=RuntimeError("connection refused"))
    msg = {"from": "manager", "type": "task_assignment", "payload": {"task": "fix the login bug"}}

    handle_task_assignment("coder", {"system_prompt": ""}, llm, producer, msg, state_path)

    state = read_state(state_path)
    assert state["expression"] == "frustrated"
    assert "connection refused" in state["bubble"]


def test_handle_task_assignment_without_state_path_does_not_raise():
    producer = FakeProducer()
    llm = FakeLLM(response="On it.")
    msg = {"from": "manager", "type": "task_assignment", "payload": {"task": "say hello"}}

    handle_task_assignment("coder", {"system_prompt": ""}, llm, producer, msg)

    assert producer.sent[0]["type"] == "task_complete"


def test_demo_editor_note_selects_pane_and_enters_then_leaves_insert_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "select_pane", lambda name: calls.append(("select_pane", name)))
    monkeypatch.setattr(agent, "send_raw", lambda name, *keys: calls.append(("send_raw", name, keys)))
    monkeypatch.setattr(agent, "send_keys", lambda name, text, **kw: calls.append(("send_keys", name, text)))

    demo_editor_note("coder", "fix   the   login   bug")

    assert calls == [
        ("select_pane", "editor"),
        ("send_raw", "editor", ("i",)),
        ("send_keys", "editor", "# TODO: fix the login bug"),
        ("send_raw", "editor", ("Escape",)),
    ]


def test_demo_editor_note_swallows_tmux_errors(monkeypatch, capsys):
    monkeypatch.setattr(agent, "select_pane", lambda name: (_ for _ in ()).throw(TmuxError("no session")))

    demo_editor_note("coder", "fix the login bug")  # must not raise

    assert "tmux editor demo skipped" in capsys.readouterr().out


def test_handle_task_assignment_invokes_demo_editor_note(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "demo_editor_note", lambda worker_id, task: calls.append((worker_id, task)))
    producer = FakeProducer()
    llm = FakeLLM(response="On it.")
    msg = {"from": "manager", "type": "task_assignment", "payload": {"task": "fix the login bug"}}

    handle_task_assignment("coder", {"system_prompt": ""}, llm, producer, msg)

    assert calls == [("coder", "fix the login bug")]


def test_demo_filetree_ls_selects_pane_runs_ls_and_returns_to_editor(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "select_pane", lambda name: calls.append(("select_pane", name)))
    monkeypatch.setattr(agent, "send_command", lambda name, cmd: calls.append(("send_command", name, cmd)))

    demo_filetree_ls("coder")

    assert calls == [
        ("select_pane", "filetree"),
        ("send_command", "filetree", "ls"),
        ("select_pane", "editor"),
    ]


def test_demo_filetree_ls_swallows_tmux_errors(monkeypatch, capsys):
    monkeypatch.setattr(agent, "select_pane", lambda name: (_ for _ in ()).throw(TmuxError("no session")))

    demo_filetree_ls("coder")  # must not raise

    assert "tmux filetree demo skipped" in capsys.readouterr().out


def test_handle_task_assignment_invokes_demo_filetree_ls(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "demo_filetree_ls", lambda worker_id: calls.append(worker_id))
    producer = FakeProducer()
    llm = FakeLLM(response="On it.")
    msg = {"from": "manager", "type": "task_assignment", "payload": {"task": "fix the login bug"}}

    handle_task_assignment("coder", {"system_prompt": ""}, llm, producer, msg)

    assert calls == ["coder"]


# ── Coder → tester handoff ────────────────────────────────────────────────────

def test_handle_task_assignment_coder_role_also_sends_commit_notification_to_tester():
    producer = FakeProducer()
    llm = FakeLLM(response="Shipping it now.")
    msg = {"from": "manager", "type": "task_assignment", "payload": {"task": "fix the login bug"}}

    handle_task_assignment("coder", {"role": "coder", "system_prompt": ""}, llm, producer, msg)

    assert len(producer.sent) == 2
    complete, commit = producer.sent
    assert complete["type"] == "task_complete"
    assert complete["to"] == "manager"
    assert commit["type"] == "commit_notification"
    assert commit["to"] == "tester"
    assert commit["payload"]["task"] == "fix the login bug"
    assert commit["payload"]["commit_message"] == "Implement: fix the login bug"
    assert commit["payload"]["narration"] == "Shipping it now."


def test_handle_task_assignment_non_coder_role_does_not_notify_tester():
    producer = FakeProducer()
    llm = FakeLLM(response="On it.")
    msg = {"from": "operator", "type": "task_assignment", "payload": {"task": "plan the sprint"}}

    handle_task_assignment("manager", {"role": "manager", "system_prompt": ""}, llm, producer, msg)

    assert [m["type"] for m in producer.sent] == ["task_complete"]


# ── Role-mismatch no-ops ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "handler, wrong_role, msg_type",
    [
        (handle_commit_notification, "coder", "commit_notification"),
        (handle_retest_request, "coder", "retest_request"),
        (handle_bug_report, "tester", "bug_report"),
        (handle_test_passed, "coder", "test_passed"),
        (handle_task_complete, "tester", "task_complete"),
        (handle_clarification_request, "coder", "clarification_request"),
    ],
)
def test_role_mismatch_is_a_noop(handler, wrong_role, msg_type):
    producer = FakeProducer()
    llm = FakeLLM(response="Should never be called.")
    msg = {"from": "someone", "type": msg_type, "payload": {"task": "anything"}}

    handler("worker", {"role": wrong_role, "system_prompt": ""}, llm, producer, msg)

    assert llm.calls == []
    assert producer.sent == []


# ── Tester: commit_notification / retest_request ──────────────────────────────

def test_handle_commit_notification_pass_sends_test_passed_to_manager(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_decide_test_outcome", lambda: (True, None))
    state_path = str(tmp_path / "state.json")
    producer = FakeProducer()
    llm = FakeLLM(response="Suite is green across the board.")
    msg = {"from": "coder", "type": "commit_notification", "payload": {"task": "fix the login bug"}}

    handle_commit_notification("tester", {"role": "tester", "system_prompt": ""}, llm, producer, msg, state_path)

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["from"] == "tester"
    assert sent["to"] == "manager"
    assert sent["type"] == "test_passed"
    assert sent["payload"]["task"] == "fix the login bug"
    assert sent["payload"]["narration"] == "Suite is green across the board."
    assert read_state(state_path)["expression"] == "happy"


def test_handle_commit_notification_failure_sends_bug_report_with_severity_and_repro(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_decide_test_outcome", lambda: (False, "high"))
    state_path = str(tmp_path / "state.json")
    producer = FakeProducer()
    llm = FakeLLM(response="Found something juicy.")
    msg = {"from": "coder", "type": "commit_notification", "payload": {"task": "fix the login bug"}}

    handle_commit_notification("tester", {"role": "tester", "system_prompt": ""}, llm, producer, msg, state_path)

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["to"] == "manager"
    assert sent["type"] == "bug_report"
    assert sent["payload"]["severity"] == "high"
    assert sent["payload"]["repro"]
    assert read_state(state_path)["expression"] == "speaking"


def test_handle_retest_request_behaves_like_commit_notification(monkeypatch):
    monkeypatch.setattr(agent, "_decide_test_outcome", lambda: (True, None))
    producer = FakeProducer()
    llm = FakeLLM(response="Re-ran the suite, all green.")
    msg = {"from": "manager", "type": "retest_request", "payload": {"task": "fix the login bug"}}

    handle_retest_request("tester", {"role": "tester", "system_prompt": ""}, llm, producer, msg)

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["to"] == "manager"
    assert sent["type"] == "test_passed"
    assert sent["payload"]["task"] == "fix the login bug"


def test_handle_commit_notification_llm_failure_sends_clarification_request_to_manager(tmp_path):
    state_path = str(tmp_path / "state.json")
    producer = FakeProducer()
    llm = FakeLLM(error=RuntimeError("connection refused"))
    msg = {"from": "coder", "type": "commit_notification", "payload": {"task": "fix the login bug"}}

    handle_commit_notification("tester", {"role": "tester", "system_prompt": ""}, llm, producer, msg, state_path)

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["to"] == "manager"
    assert sent["type"] == "clarification_request"
    assert "connection refused" in sent["payload"]["error"]
    assert read_state(state_path)["expression"] == "frustrated"


# ── Manager: bug_report ───────────────────────────────────────────────────────

def test_handle_bug_report_redelegates_to_coder_with_incremented_retry_count():
    producer = FakeProducer()
    llm = FakeLLM(response="Back to the coder it goes.")
    msg = {
        "from": "tester",
        "type": "bug_report",
        "payload": {"task": "fix the login bug", "severity": "high", "repro": "login as admin", "retry_count": 1},
    }

    handle_bug_report("manager", {"role": "manager", "system_prompt": ""}, llm, producer, msg)

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["to"] == "coder"
    assert sent["type"] == "task_assignment"
    assert sent["payload"]["retry_count"] == 2
    assert "Fix bug (high): fix the login bug" in sent["payload"]["task"]
    assert "login as admin" in sent["payload"]["task"]


def test_handle_bug_report_escalates_to_operator_at_retry_cap(tmp_path):
    state_path = str(tmp_path / "state.json")
    producer = FakeProducer()
    llm = FakeLLM(response="This one goes upstairs.")
    msg = {
        "from": "tester",
        "type": "bug_report",
        "payload": {
            "task": "fix the login bug",
            "severity": "critical",
            "repro": "login as admin",
            "retry_count": agent.MAX_BUG_RETRIES,
        },
    }

    handle_bug_report("manager", {"role": "manager", "system_prompt": ""}, llm, producer, msg, state_path)

    assert [m["type"] for m in producer.sent] == ["manager_report"]
    sent = producer.sent[0]
    assert sent["to"] == "operator"
    assert sent["payload"]["report_type"] == "escalation"
    assert read_state(state_path)["expression"] == "frustrated"


def test_handle_bug_report_escalates_when_llm_fails():
    producer = FakeProducer()
    llm = FakeLLM(error=RuntimeError("connection refused"))
    msg = {
        "from": "tester",
        "type": "bug_report",
        "payload": {"task": "fix the login bug", "severity": "high", "repro": "login as admin", "retry_count": 0},
    }

    handle_bug_report("manager", {"role": "manager", "system_prompt": ""}, llm, producer, msg)

    assert [m["type"] for m in producer.sent] == ["manager_report"]
    sent = producer.sent[0]
    assert sent["to"] == "operator"
    assert sent["payload"]["report_type"] == "escalation"
    assert "connection refused" in sent["payload"]["narration"]


# ── Manager: test_passed / task_complete / clarification_request ─────────────

def test_handle_test_passed_sends_milestone_manager_report(tmp_path):
    state_path = str(tmp_path / "state.json")
    producer = FakeProducer()
    llm = FakeLLM(response="Team win! Drinks on KODI-7.")
    msg = {"from": "tester", "type": "test_passed", "payload": {"task": "fix the login bug"}}

    handle_test_passed("manager", {"role": "manager", "system_prompt": ""}, llm, producer, msg, state_path)

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["to"] == "operator"
    assert sent["type"] == "manager_report"
    assert sent["payload"]["report_type"] == "milestone"
    assert sent["payload"]["narration"] == "Team win! Drinks on KODI-7."
    assert read_state(state_path)["expression"] == "happy"


def test_handle_task_complete_narrates_without_sending(tmp_path):
    state_path = str(tmp_path / "state.json")
    producer = FakeProducer()
    llm = FakeLLM(response="Nice progress, keep it moving.")
    msg = {"from": "coder", "type": "task_complete", "payload": {"task": "fix the login bug"}}

    handle_task_complete("manager", {"role": "manager", "system_prompt": ""}, llm, producer, msg, state_path)

    assert producer.sent == []
    assert len(llm.calls) == 1
    assert read_state(state_path)["expression"] == "speaking"


def test_handle_clarification_request_sends_exactly_one_blocker_report():
    producer = FakeProducer()
    llm = FakeLLM(response="Flagging this to the boss.")
    msg = {
        "from": "coder",
        "type": "clarification_request",
        "payload": {"task": "fix the login bug", "error": "connection refused"},
    }

    handle_clarification_request("manager", {"role": "manager", "system_prompt": ""}, llm, producer, msg)

    assert [m["type"] for m in producer.sent] == ["manager_report"]
    sent = producer.sent[0]
    assert sent["to"] == "operator"
    assert sent["payload"]["report_type"] == "blocker"


def test_handle_clarification_request_still_escalates_when_llm_fails(tmp_path):
    state_path = str(tmp_path / "state.json")
    producer = FakeProducer()
    llm = FakeLLM(error=RuntimeError("connection refused"))
    msg = {
        "from": "coder",
        "type": "clarification_request",
        "payload": {"task": "fix the login bug", "error": "timeout"},
    }

    handle_clarification_request("manager", {"role": "manager", "system_prompt": ""}, llm, producer, msg, state_path)

    assert [m["type"] for m in producer.sent] == ["manager_report"]
    sent = producer.sent[0]
    assert sent["payload"]["report_type"] == "blocker"
    assert "connection refused" in sent["payload"]["narration"]
    assert read_state(state_path)["expression"] == "frustrated"


# ── Operator channel ──────────────────────────────────────────────────────────

def test_handle_operator_message_replies_operator_reply_without_demo_helpers(monkeypatch):
    demo_calls = []
    monkeypatch.setattr(agent, "demo_editor_note", lambda *a, **kw: demo_calls.append("editor"))
    monkeypatch.setattr(agent, "demo_filetree_ls", lambda *a, **kw: demo_calls.append("filetree"))
    producer = FakeProducer()
    llm = FakeLLM(response="All quiet on my end, boss.")
    msg = {"from": "operator", "type": "operator_message", "payload": {"message": "status?"}}

    handle_operator_message("tester", {"role": "tester", "system_prompt": ""}, llm, producer, msg)

    assert demo_calls == []
    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["to"] == "operator"
    assert sent["type"] == "operator_reply"
    assert sent["payload"]["narration"] == "All quiet on my end, boss."


def test_handle_operator_message_llm_failure_replies_with_error():
    producer = FakeProducer()
    llm = FakeLLM(error=RuntimeError("connection refused"))
    msg = {"from": "operator", "type": "operator_message", "payload": {"message": "status?"}}

    handle_operator_message("coder", {"role": "coder", "system_prompt": ""}, llm, producer, msg)

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["to"] == "operator"
    assert sent["type"] == "operator_reply"
    assert "connection refused" in sent["payload"]["error"]


# ── Retry-count round trip (coder → tester → manager) ─────────────────────────

def test_retry_count_survives_coder_tester_manager_round_trip(monkeypatch):
    monkeypatch.setattr(agent, "_decide_test_outcome", lambda: (False, "high"))
    llm = FakeLLM(response="Working it.")

    # Coder: task_assignment (retry_count=1, i.e. a re-delegated fix) →
    # commit_notification must carry the same retry_count.
    coder_producer = FakeProducer()
    assignment = {"from": "manager", "type": "task_assignment",
                  "payload": {"task": "fix the login bug", "retry_count": 1}}
    handle_task_assignment("coder", {"role": "coder", "system_prompt": ""}, llm, coder_producer, assignment)
    commit = coder_producer.sent[1]
    assert commit["type"] == "commit_notification"
    assert commit["payload"]["retry_count"] == 1

    # Tester: the bug_report must carry the retry_count from the commit.
    tester_producer = FakeProducer()
    handle_commit_notification("tester", {"role": "tester", "system_prompt": ""}, llm, tester_producer, commit)
    bug = tester_producer.sent[0]
    assert bug["type"] == "bug_report"
    assert bug["payload"]["retry_count"] == 1

    # Manager: re-delegates with the incoming count incremented.
    manager_producer = FakeProducer()
    handle_bug_report("manager", {"role": "manager", "system_prompt": ""}, llm, manager_producer, bug)
    redelegated = manager_producer.sent[0]
    assert redelegated["type"] == "task_assignment"
    assert redelegated["payload"]["retry_count"] == 2


# ── Dispatch table ────────────────────────────────────────────────────────────

def test_message_handlers_covers_all_documented_types():
    assert set(MESSAGE_HANDLERS) == {
        "task_assignment",
        "commit_notification",
        "retest_request",
        "bug_report",
        "test_passed",
        "task_complete",
        "clarification_request",
        "operator_message",
        "replay_request",
    }
