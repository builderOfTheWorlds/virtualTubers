"""P3.1 frozen tests for app/table/protocol.py (live agent table, build plan P3.1).

Message types: agent_dnd_architecture.md §4.2, plus the two THINK types the
two-pass round adds (plan §1 row 4): think_request, think_done.

Invariants tested here (code-enforced, never prompt-only):
- unknown types are rejected;
- every type has a required payload shape (missing field / wrong type -> error);
- enums are closed (adjudication verdict, operator_override action, transcript kind);
- U6: private intent / reasoning NEVER travels on the bus. think_done and
  character_reply reject any payload key that could carry it;
- committed_transcript is passed BY VALUE as a list of {speaker, text, kind}.
"""
import copy

import pytest

from pending import require

protocol = require("table.protocol", "app/table/protocol.py", wp="P3.1")

SCENE = "seg03.leaf2.slot1"
LINE = {"speaker": "gm", "text": "The vault door groans open.", "kind": "direction"}

VALID = {
    "scene_start": {"scene_id": SCENE, "contract": {"canon_goal": "open the vault"},
                    "round": 1, "max_rounds": 4, "seats": ["tuber_1", "tuber_2"]},
    "scene_direction": {"scene_id": SCENE, "round": 1, "speaker": "gm",
                        "text": "The vault door groans open.",
                        "expects": ["tuber_1", "tuber_2"], "must_resolve": "the door opens"},
    "think_request": {"scene_id": SCENE, "round": 1, "seats": ["tuber_1", "tuber_2"],
                      "committed_transcript": [LINE], "deadline_s": 60},
    "think_done": {"scene_id": SCENE, "round": 1, "seat": "tuber_1", "ok": True},
    "turn_assignment": {"scene_id": SCENE, "round": 1, "seat": "tuber_1", "order_pos": 0,
                        "committed_transcript": [LINE], "instruction": "React.",
                        "deadline_s": 45, "max_retries": 2},
    "character_reply": {"scene_id": SCENE, "round": 1, "seat": "tuber_1",
                        "text": "Oh, that's not good.", "took": True, "reason": ""},
    "retake": {"scene_id": SCENE, "round": 1, "seat": "tuber_1",
               "reason": "forbidden_leak: vault key", "retry": 1, "max": 2},
    "gm_overrule": {"scene_id": SCENE, "round": 1, "seat": "tuber_1",
                    "committed_text": "Chadwick stays silent.", "note": "auto-overruled"},
    "adjudication": {"scene_id": SCENE, "round": 1, "verdict": "pass", "notes": "ok"},
    "scene_resolve": {"scene_id": SCENE, "resolved": True, "state_delta": {},
                      "record_id": None},
    "scene_stuck": {"scene_id": SCENE, "reason": "deadline", "state": {}},
    "operator_override": {"scene_id": SCENE, "action": "accept_all", "note": ""},
}


def test_message_types_are_exactly_the_twelve():
    assert set(protocol.MESSAGE_TYPES) == set(VALID)
    assert len(protocol.MESSAGE_TYPES) == 12


@pytest.mark.parametrize("type_", sorted(VALID))
def test_round_trip_every_type(type_):
    payload = copy.deepcopy(VALID[type_])
    msg = protocol.build(type_, "tuber_0", "broadcast", payload, turn="r1.t0")
    assert msg["type"] == type_
    assert msg["from"] == "tuber_0" and msg["to"] == "broadcast"
    assert msg["scene"] == SCENE            # envelope carries scene + turn for audit
    assert msg["turn"] == "r1.t0"
    assert msg["id"] and msg["timestamp"] and msg["correlation_id"]
    assert protocol.parse(msg) == payload
    assert payload == VALID[type_]          # build never mutates the caller's dict


def test_build_passes_correlation_and_causation():
    msg = protocol.build("adjudication", "tuber_0", "broadcast", VALID["adjudication"],
                         correlation_id="corr-1", causation_id="cause-1")
    assert msg["correlation_id"] == "corr-1" and msg["causation_id"] == "cause-1"


def test_unknown_type_rejected():
    with pytest.raises(protocol.ProtocolError) as caught:
        protocol.build("seat_to_seat_whisper", "tuber_1", "tuber_2", {"scene_id": SCENE})
    assert caught.value.type_ == "seat_to_seat_whisper"
    with pytest.raises(protocol.ProtocolError):
        protocol.parse({"type": "chat", "payload": {}})


@pytest.mark.parametrize("type_", sorted(VALID))
def test_missing_required_field_rejected_and_named(type_):
    for field in protocol.REQUIRED[type_]:
        payload = copy.deepcopy(VALID[type_])
        del payload[field]
        with pytest.raises(protocol.ProtocolError) as caught:
            protocol.build(type_, "tuber_0", "broadcast", payload)
        assert caught.value.field == field
        assert field in str(caught.value)


@pytest.mark.parametrize("type_,field,bad", [
    ("turn_assignment", "round", "1"),
    ("turn_assignment", "deadline_s", -1),
    ("turn_assignment", "committed_transcript", "GM: hi"),
    ("character_reply", "took", "yes"),
    ("character_reply", "text", None),
    ("scene_start", "seats", "tuber_1"),
    ("scene_start", "max_rounds", 0),
    ("retake", "retry", 3),                 # retry may not exceed max
    ("think_request", "seats", []),
])
def test_wrong_type_or_range_rejected(type_, field, bad):
    payload = copy.deepcopy(VALID[type_])
    payload[field] = bad
    with pytest.raises(protocol.ProtocolError) as caught:
        protocol.build(type_, "tuber_0", "broadcast", payload)
    assert caught.value.field == field


@pytest.mark.parametrize("type_,field,bad", [
    ("adjudication", "verdict", "maybe"),
    ("operator_override", "action", "delete_everything"),
])
def test_closed_enums(type_, field, bad):
    payload = copy.deepcopy(VALID[type_])
    payload[field] = bad
    with pytest.raises(protocol.ProtocolError):
        protocol.build(type_, "tuber_0", "broadcast", payload)


def test_enum_values_are_the_spec_values():
    assert set(protocol.VERDICTS) == {"pass", "reject"}
    assert set(protocol.OPERATOR_ACTIONS) == {"accept_all", "skip_scene", "abort_session"}
    assert set(protocol.TRANSCRIPT_KINDS) == {"direction", "reply", "overrule"}


@pytest.mark.parametrize("bad_line", [
    {"speaker": "gm", "text": "x"},                          # kind missing
    {"speaker": "gm", "text": "x", "kind": "whisper"},       # kind not allowed
    {"speaker": "", "text": "x", "kind": "reply"},           # empty speaker
    {"speaker": "gm", "text": 3, "kind": "reply"},           # text not str
    "GM: hi",                                               # not a dict
])
def test_transcript_entries_validated(bad_line):
    payload = copy.deepcopy(VALID["turn_assignment"])
    payload["committed_transcript"] = [LINE, bad_line]
    with pytest.raises(protocol.ProtocolError) as caught:
        protocol.build("turn_assignment", "tuber_0", "tuber_1", payload)
    assert caught.value.field == "committed_transcript"


@pytest.mark.parametrize("type_", ["think_done", "character_reply"])
@pytest.mark.parametrize("key", sorted(["intent", "private_intent", "reasoning",
                                        "reasoning_content", "thinking"]))
def test_private_intent_never_on_the_bus(type_, key):
    """U6: a seat's private intent/reasoning stays in that seat (Thinking pane
    via agent_thinking only). The table protocol refuses to carry it."""
    payload = copy.deepcopy(VALID[type_])
    payload[key] = "I secretly want the amulet"
    with pytest.raises(protocol.ProtocolError) as caught:
        protocol.build(type_, "tuber_1", "tuber_0", payload)
    assert caught.value.field == key
    assert set(protocol.PRIVATE_KEYS) >= {"intent", "private_intent", "reasoning",
                                         "reasoning_content", "thinking"}


def test_private_keys_rejected_inside_transcript_entries():
    payload = copy.deepcopy(VALID["turn_assignment"])
    payload["committed_transcript"] = [dict(LINE, reasoning="hidden")]
    with pytest.raises(protocol.ProtocolError):
        protocol.build("turn_assignment", "tuber_0", "tuber_1", payload)


def test_extra_non_private_keys_are_allowed_forward_compat():
    payload = dict(VALID["character_reply"], emotion="nervous")
    msg = protocol.build("character_reply", "tuber_1", "tuber_0", payload)
    assert protocol.parse(msg)["emotion"] == "nervous"


def test_parse_rejects_tampered_message():
    msg = protocol.build("character_reply", "tuber_1", "tuber_0", VALID["character_reply"])
    msg["payload"]["took"] = "true"
    with pytest.raises(protocol.ProtocolError):
        protocol.parse(msg)


def test_parse_returns_a_copy():
    msg = protocol.build("turn_assignment", "tuber_0", "tuber_1", VALID["turn_assignment"])
    out = protocol.parse(msg)
    out["committed_transcript"].append({"speaker": "x", "text": "y", "kind": "reply"})
    assert len(msg["payload"]["committed_transcript"]) == 1


def test_is_table_message():
    msg = protocol.build("adjudication", "tuber_0", "broadcast", VALID["adjudication"])
    assert protocol.is_table_message(msg)
    assert not protocol.is_table_message({"type": "agent_thinking", "payload": {}})
    assert not protocol.is_table_message("not a dict")
