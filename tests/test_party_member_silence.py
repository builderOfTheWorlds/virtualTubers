"""Decision U6: the ashiorid_office Party Member never speaks.

The any-role handlers (agent_handlers/operator.py handle_operator_message,
agent_handlers/viewer.py handle_viewer_joined) skip the LLM for a worker
whose agent.office_role is party_member: the operator gets a non-text
acknowledgement, the viewer greeting is omitted (the rerun still queues).
"""
import json

import pytest

from agent_handlers import viewer
from agent_handlers.operator import handle_operator_message
from agent_handlers.viewer import handle_viewer_joined
from agent_state import read_state

PARTY = {"role": "observer", "office_role": "party_member", "system_prompt": "You are silent."}


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class FakeLLM:
    def __init__(self, response="Hello there."):
        self.response = response
        self.calls = []

    def complete(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        return self.response


@pytest.fixture
def replay_env(tmp_path, monkeypatch):
    episodes = []
    request_file = tmp_path / "replay_request.json"
    monkeypatch.setattr(viewer.episode_store, "available", lambda: True)
    monkeypatch.setattr(viewer.episode_store, "list_episodes", lambda: list(episodes))
    monkeypatch.setenv("REPLAY_REQUEST_FILE", str(request_file))
    return episodes, request_file


def operator_msg():
    return {"id": "m-1", "correlation_id": "c-1", "from": "operator",
            "type": "operator_message", "payload": {"message": "Say something, Penhale."}}


def test_party_member_operator_message_skips_llm_and_acks_silently(tmp_path):
    state_path = str(tmp_path / "state.json")
    producer, llm = FakeProducer(), FakeLLM()

    handle_operator_message("tuber_7", PARTY, llm, producer, operator_msg(), state_path)

    assert llm.calls == []
    assert len(producer.sent) == 1
    reply = producer.sent[0]
    assert (reply["to"], reply["type"]) == ("operator", "operator_reply")
    assert reply["payload"] == {"silent": True, "office_role": "party_member"}
    assert "narration" not in reply["payload"]
    assert reply["correlation_id"] == "c-1" and reply["causation_id"] == "m-1"
    state = read_state(state_path)
    assert state["expression"] == "idle" and not state.get("bubble")


def test_party_member_viewer_joined_queues_rerun_but_omits_greeting(tmp_path, replay_env):
    episodes, request_file = replay_env
    episodes.append("office-claude_code-sess-001")
    state_path = str(tmp_path / "state.json")
    producer, llm = FakeProducer(), FakeLLM()
    msg = {"from": "operator", "type": "viewer_joined", "payload": {"username": "phil"}}

    handle_viewer_joined("tuber_7", PARTY, llm, producer, msg, state_path)

    assert json.loads(request_file.read_text()) == {"episode": "office-claude_code-sess-001"}
    assert llm.calls == []
    assert producer.sent == []
    state = read_state(state_path)
    assert not (state or {}).get("bubble")


def test_party_member_viewer_joined_with_empty_library_is_silent(replay_env):
    _, request_file = replay_env
    producer, llm = FakeProducer(), FakeLLM()
    msg = {"from": "operator", "type": "viewer_joined", "payload": {"username": "phil"}}

    handle_viewer_joined("tuber_7", PARTY, llm, producer, msg)

    assert not request_file.exists()
    assert llm.calls == [] and producer.sent == []


@pytest.mark.parametrize("cfg", [
    {"role": "ceo", "office_role": "ceo", "system_prompt": "CEO"},
    {"role": "coder", "system_prompt": ""},                          # not an office worker
    {"role": "observer", "office_role": "not_a_role", "system_prompt": ""},
])
def test_other_roles_still_speak(cfg, replay_env):
    producer, llm = FakeProducer(), FakeLLM("Morning, all.")
    handle_operator_message("w", cfg, llm, producer, operator_msg())
    assert producer.sent[0]["payload"] == {"narration": "Morning, all."}
    handle_viewer_joined("w", cfg, llm, FakeProducer(),
                         {"type": "viewer_joined", "payload": {"username": "phil"}})
    assert len(llm.calls) == 2
