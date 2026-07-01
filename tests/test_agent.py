from agent import handle_task_assignment


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
