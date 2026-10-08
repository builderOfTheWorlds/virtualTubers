"""P3.5 frozen tests for app/agent_handlers/table.py: the character-agent (seat) handler.

Build plan P3.5, decisions U4 (reasoning streams to the seat's own Thinking pane),
U6 (private intent never leaves the seat). The seat:
  think_request    -> one THINK call (reasoning ON, budgeted) -> think_done (NO intent)
                      and the intent kept in the seat's LOCAL memory file
  turn_assignment  -> one SPEAK call with brief + committed transcript (by value) +
                      ITS OWN intent -> character_reply
  retake           -> SPEAK again for the stored assignment, with the retake reason
It never reads other seats' lines from anywhere but committed_transcript.
"""
import json

import pytest

from pending import require

seat = require("agent_handlers.table", "app/agent_handlers/table.py", wp="P3.5")
from table import protocol  # noqa: E402

SCENE = "seg.leaf.slot1"
DIRECTION = {"speaker": "gm", "text": "The vault door groans open.", "kind": "direction"}


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


class FakeLLM:
    """complete_stream(system, messages, max_tokens=, reasoning_budget=) -> (reasoning, content)."""

    def __init__(self, replies=None, error=None):
        self.replies = list(replies or [])
        self.error = error
        self.calls = []

    def complete_stream(self, system_prompt, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, reasoning_budget=None):
        self.calls.append({"system": system_prompt, "messages": messages,
                           "max_tokens": max_tokens, "reasoning_budget": reasoning_budget})
        if self.error:
            raise self.error
        return "private reasoning", self.replies.pop(0)


def config(tmp_path, role="table_seat"):
    return {"role": role, "table": {
        "memory_dir": str(tmp_path),
        "seat_names": {"gm": "GM", "tuber_1": "Chadwick", "tuber_2": "Leena"},
        "think": {"max_tokens": 900, "reasoning_budget": 200},
        "speak": {"max_tokens": 150, "reasoning_budget": 40},
    }}


@pytest.fixture(autouse=True)
def fake_brief(monkeypatch):
    calls = []

    def brief_for(agent_config, worker_id, unlocked=()):
        calls.append((worker_id, tuple(unlocked)))
        return f"# Who you are\nYou are {worker_id}'s character."

    monkeypatch.setattr(seat, "brief_for", brief_for)
    return calls


def think_request(seats=("tuber_1", "tuber_2"), transcript=(DIRECTION,), round_=1):
    return protocol.build("think_request", "tuber_0", "broadcast", {
        "scene_id": SCENE, "round": round_, "seats": list(seats),
        "committed_transcript": list(transcript), "deadline_s": 60}, turn="r1.think.gm")


def assignment(seat_id="tuber_1", transcript=(DIRECTION,), round_=1):
    return protocol.build("turn_assignment", "tuber_0", seat_id, {
        "scene_id": SCENE, "round": round_, "seat": seat_id, "order_pos": 0,
        "committed_transcript": list(transcript), "instruction": "React to the GM.",
        "deadline_s": 45, "max_retries": 2}, turn=f"r1.speak.{seat_id}")


def retake(seat_id="tuber_1", reason="stage_direction: (aside)", round_=1):
    return protocol.build("retake", "tuber_0", seat_id, {
        "scene_id": SCENE, "round": round_, "seat": seat_id, "reason": reason,
        "retry": 1, "max": 2}, turn=f"r1.speak.{seat_id}")


def _handle(fn, tmp_path, llm, producer, msg, worker="tuber_1", role="table_seat"):
    return fn(worker, config(tmp_path, role), llm, producer, msg, None)


# ── THINK ───────────────────────────────────────────────────────────────────

def test_think_sends_think_done_without_intent_and_keeps_intent_locally(tmp_path):
    llm, prod = FakeLLM(["I want the ring. I will say the door is a trap."]), FakeProducer()
    req = think_request()
    _handle(seat.handle_think_request, tmp_path, llm, prod, req)
    assert len(prod.sent) == 1
    msg = prod.sent[0]
    assert msg["type"] == "think_done" and msg["to"] == "tuber_0" and msg["from"] == "tuber_1"
    assert protocol.parse(msg) == {"scene_id": SCENE, "round": 1, "seat": "tuber_1", "ok": True}
    assert "ring" not in json.dumps(msg)
    assert msg["causation_id"] == req["id"]
    mem = seat.SeatMemory.for_worker(config(tmp_path)["table"], "tuber_1")
    assert mem.intent(SCENE, 1) == "I want the ring. I will say the door is a trap."


def test_think_uses_think_budgets_and_brief_and_transcript(tmp_path, fake_brief):
    llm, prod = FakeLLM(["intent"]), FakeProducer()
    _handle(seat.handle_think_request, tmp_path, llm, prod, think_request())
    call = llm.calls[0]
    assert (call["max_tokens"], call["reasoning_budget"]) == (900, 200)
    assert "You are tuber_1's character." in call["system"]
    user = " ".join(m["content"] for m in call["messages"])
    assert "The vault door groans open." in user and "GM" in user
    assert fake_brief[0][0] == "tuber_1"


def test_think_ignored_when_not_listed_wrong_role_or_invalid(tmp_path):
    llm, prod = FakeLLM(["x", "y"]), FakeProducer()
    _handle(seat.handle_think_request, tmp_path, llm, prod, think_request(seats=("tuber_2",)))
    _handle(seat.handle_think_request, tmp_path, llm, prod, think_request(), role="coder")
    bad = think_request()
    bad["payload"]["seats"] = []
    _handle(seat.handle_think_request, tmp_path, llm, prod, bad)
    assert prod.sent == [] and llm.calls == []


def test_think_llm_failure_reports_ok_false(tmp_path):
    llm, prod = FakeLLM(error=RuntimeError("vllm down")), FakeProducer()
    _handle(seat.handle_think_request, tmp_path, llm, prod, think_request())
    assert protocol.parse(prod.sent[0])["ok"] is False


# ── SPEAK ───────────────────────────────────────────────────────────────────

def test_speak_uses_own_intent_and_committed_transcript_only(tmp_path):
    llm, prod = FakeLLM(["MY-INTENT-123", '"That door is a trap."']), FakeProducer()
    _handle(seat.handle_think_request, tmp_path, llm, prod, think_request())
    other = {"speaker": "tuber_2", "text": "Leena says it hums.", "kind": "reply"}
    _handle(seat.handle_turn_assignment, tmp_path, llm, prod, assignment(transcript=(DIRECTION, other)))
    speak_call = llm.calls[1]
    user = " ".join(m["content"] for m in speak_call["messages"])
    assert "MY-INTENT-123" in user
    assert "Leena says it hums." in user and "Leena" in user
    assert (speak_call["max_tokens"], speak_call["reasoning_budget"]) == (150, 40)
    reply = prod.sent[-1]
    assert reply["type"] == "character_reply" and reply["to"] == "tuber_0"
    payload = protocol.parse(reply)
    assert payload["text"] == "That door is a trap."      # one surrounding quote pair stripped
    assert payload["took"] is True and payload["reason"] == ""
    assert "MY-INTENT-123" not in json.dumps(prod.sent)    # U6: never on the bus


def test_speak_without_prior_think_still_speaks(tmp_path):
    llm, prod = FakeLLM(["A line."]), FakeProducer()
    _handle(seat.handle_turn_assignment, tmp_path, llm, prod, assignment())
    assert protocol.parse(prod.sent[0])["text"] == "A line."


def test_speak_ignores_other_seats_assignment(tmp_path):
    llm, prod = FakeLLM(["x"]), FakeProducer()
    _handle(seat.handle_turn_assignment, tmp_path, llm, prod, assignment(seat_id="tuber_2"))
    assert prod.sent == [] and llm.calls == []


def test_speak_llm_failure_replies_took_false(tmp_path):
    llm, prod = FakeLLM(error=RuntimeError("boom")), FakeProducer()
    _handle(seat.handle_turn_assignment, tmp_path, llm, prod, assignment())
    payload = protocol.parse(prod.sent[0])
    assert payload["took"] is False and payload["reason"].startswith("llm_error")
    assert payload["text"] == ""


def test_intent_of_another_seat_never_used(tmp_path):
    llm, prod = FakeLLM(["LEENA-SECRET", "line"]), FakeProducer()
    _handle(seat.handle_think_request, tmp_path, llm, prod, think_request(), worker="tuber_2")
    _handle(seat.handle_turn_assignment, tmp_path, llm, prod, assignment())   # tuber_1 speaks
    user = " ".join(m["content"] for m in llm.calls[1]["messages"])
    assert "LEENA-SECRET" not in user


# ── RETAKE ──────────────────────────────────────────────────────────────────

def test_retake_reuses_stored_assignment_with_reason(tmp_path):
    llm, prod = FakeLLM(["first (aside)", "second take"]), FakeProducer()
    _handle(seat.handle_turn_assignment, tmp_path, llm, prod, assignment())
    _handle(seat.handle_retake, tmp_path, llm, prod, retake())
    user = " ".join(m["content"] for m in llm.calls[1]["messages"])
    assert "stage_direction: (aside)" in user
    assert "The vault door groans open." in user           # same committed transcript
    assert protocol.parse(prod.sent[-1])["text"] == "second take"


def test_retake_without_stored_assignment_is_ignored(tmp_path):
    llm, prod = FakeLLM(["x"]), FakeProducer()
    _handle(seat.handle_retake, tmp_path, llm, prod, retake())
    assert prod.sent == [] and llm.calls == []


# ── memory ──────────────────────────────────────────────────────────────────

def test_memory_survives_reload_and_prunes_old_scenes(tmp_path):
    cfg = config(tmp_path)["table"]
    mem = seat.SeatMemory.for_worker(cfg, "tuber_1")
    for i in range(seat.MEMORY_MAX_SCENES + 3):
        mem.set_intent(f"scene{i}", 1, f"intent{i}")
    again = seat.SeatMemory.for_worker(cfg, "tuber_1")
    last = seat.MEMORY_MAX_SCENES + 2
    assert again.intent(f"scene{last}", 1) == f"intent{last}"
    assert again.intent("scene0", 1) is None


def test_every_sent_message_is_a_valid_table_message(tmp_path):
    llm, prod = FakeLLM(["i", "l", "r"]), FakeProducer()
    _handle(seat.handle_think_request, tmp_path, llm, prod, think_request())
    _handle(seat.handle_turn_assignment, tmp_path, llm, prod, assignment())
    _handle(seat.handle_retake, tmp_path, llm, prod, retake())
    for m in prod.sent:
        protocol.parse(m)
        assert m["correlation_id"] == SCENE



# ── review additions (orchestrator) ──────────────────────────────────────────

def test_memory_order_never_reuses_a_live_order_after_pruning(tmp_path):
    cfg = config(tmp_path)["table"]
    mem = seat.SeatMemory.for_worker(cfg, "tuber_1")
    for i in range(seat.MEMORY_MAX_SCENES + 5):
        mem.set_intent(f"s{i}", 1, "x")
        assert mem.intent(f"s{i}", 1) == "x", f"newest scene s{i} was pruned"
