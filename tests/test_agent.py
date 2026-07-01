import agent
from agent import demo_editor_note, handle_task_assignment
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
