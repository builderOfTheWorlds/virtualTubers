"""Tests for app/office/protocol.py (OB-05)."""
import pytest

import office.protocol as protocol
from agent_handlers import MESSAGE_HANDLERS
from message_bus import BROADCAST, build_message
from office.protocol import (
    OFFICE_MESSAGE_TYPES,
    ProtocolError,
    build_day_end,
    build_day_start,
    build_directive,
    build_functional_plan,
    build_phase_change,
    build_status_report,
    build_technical_plan,
    build_test_request,
    is_office_message,
    validate_message,
)
from office.roles import OfficeRole as R

DAY = "2026-09-28"


def test_message_types_registered_except_clock_edges():
    # Every office type is handled except the day_start/day_end clock edges
    # (nothing reacts to them); wrap_up is handled by handle_wrap_up.
    assert set(OFFICE_MESSAGE_TYPES) - set(MESSAGE_HANDLERS) == {"day_start", "day_end"}
    assert MESSAGE_HANDLERS["wrap_up"].__name__ == "handle_wrap_up"
    assert len(OFFICE_MESSAGE_TYPES) == 9


@pytest.mark.parametrize("recipient,seat", [
    (R.TECH_LEAD, "tuber_1"), (R.ANALYST, "tuber_2"),
    (R.MARKETING, "tuber_5"), (R.OFFICE_MANAGER, "tuber_6"),
])
def test_build_directive_ceo_to_direct_reports(recipient, seat):
    msg = build_directive(R.CEO, recipient, "Ship the rules engine", issue=12, day=DAY)
    assert msg["from"] == "tuber_0" and msg["to"] == seat
    assert msg["type"] == "directive"
    assert msg["payload"] == {"text": "Ship the rules engine", "issue": 12, "day": DAY}
    assert msg["correlation_id"] == msg["id"] and msg["causation_id"] is None


@pytest.mark.parametrize("sender,recipient", [
    (R.CEO, R.ENGINEER), (R.CEO, R.TESTER), (R.CEO, R.PARTY_MEMBER),
    (R.TECH_LEAD, R.CEO), (R.ANALYST, R.TECH_LEAD), (R.ENGINEER, R.TESTER),
    (R.PARTY_MEMBER, R.CEO), (R.TESTER, R.ENGINEER), (R.MARKETING, R.OFFICE_MANAGER),
])
def test_build_directive_rank_violation_raises(sender, recipient):
    with pytest.raises(ProtocolError):
        build_directive(sender, recipient, "do it")


def test_build_directive_tech_lead_to_engineer_ok():
    assert build_directive("tuber_1", "tuber_3", "implement /verdict")["to"] == "tuber_3"


@pytest.mark.parametrize("kwargs", [
    {"text": ""}, {"text": "   "}, {"text": None}, {"text": 5},
    {"text": "ok", "issue": "12"}, {"text": "ok", "issue": True},
    {"text": "ok", "day": "tomorrow"},
])
def test_build_directive_malformed_payload_raises(kwargs):
    with pytest.raises(ProtocolError):
        build_directive(R.CEO, R.TECH_LEAD, **kwargs)


def test_unknown_role_raises_protocol_error():
    with pytest.raises(ProtocolError):
        build_directive("tuber_99", R.TECH_LEAD, "x")


def test_build_functional_plan_goes_to_tech_lead_cc_ceo():
    msg = build_functional_plan("FR-1: score txns", directive_id="d-1")
    assert (msg["from"], msg["to"]) == ("tuber_2", "tuber_1")
    assert msg["payload"]["cc"] == ["tuber_0"]
    assert msg["payload"]["directive_id"] == "d-1"


def test_build_functional_plan_wrong_sender_raises():
    with pytest.raises(ProtocolError):
        build_functional_plan("plan", sender=R.MARKETING)


def test_build_technical_plan_tech_lead_to_ceo():
    msg = build_technical_plan("plan", tasks=["api", "rules"])
    assert (msg["from"], msg["to"], msg["type"]) == ("tuber_1", "tuber_0", "technical_plan")
    assert msg["payload"]["tasks"] == ["api", "rules"]


@pytest.mark.parametrize("tasks", [["ok", ""], ["ok", 3], "api"])
def test_build_technical_plan_bad_tasks_raises(tasks):
    with pytest.raises(ProtocolError):
        build_technical_plan("plan", tasks=tasks)


@pytest.mark.parametrize("sender", [R.ENGINEER, R.TECH_LEAD])
def test_build_test_request_to_tester(sender):
    msg = build_test_request("verify rules", sender=sender, branch="feat/rules", paths=["src/r.py"])
    assert msg["to"] == "tuber_4" and msg["type"] == "test_request"


@pytest.mark.parametrize("sender", [R.CEO, R.ANALYST, R.TESTER, R.PARTY_MEMBER])
def test_build_test_request_wrong_sender_raises(sender):
    with pytest.raises(ProtocolError):
        build_test_request("verify", sender=sender)


@pytest.mark.parametrize("sender,to", [
    (R.ENGINEER, "tuber_1"), (R.TESTER, "tuber_1"), (R.TECH_LEAD, "tuber_0"),
    (R.ANALYST, "tuber_0"), (R.MARKETING, "tuber_0"), (R.OFFICE_MANAGER, "tuber_0"),
])
def test_build_status_report_goes_to_superior(sender, to):
    msg = build_status_report(sender, "all good", status="on_track", day=DAY)
    assert msg["to"] == to


@pytest.mark.parametrize("sender", [R.CEO, R.PARTY_MEMBER])
def test_build_status_report_without_superior_raises(sender):
    with pytest.raises(ProtocolError):
        build_status_report(sender, "hi")


def test_build_status_report_bad_status_raises():
    with pytest.raises(ProtocolError):
        build_status_report(R.ENGINEER, "hi", status="vibing")


def test_reply_to_threads_correlation_ids():
    directive = build_directive(R.CEO, R.ANALYST, "requirements for v2")
    plan = build_functional_plan("FR list", reply_to=directive)
    assert plan["correlation_id"] == directive["correlation_id"]
    assert plan["causation_id"] == directive["id"]
    report = build_status_report(R.TECH_LEAD, "planned", reply_to=plan)
    assert report["correlation_id"] == directive["id"]
    assert report["causation_id"] == plan["id"]


def test_explicit_correlation_ids_pass_through():
    msg = build_day_start(DAY, correlation_id="c-1", causation_id="m-0")
    assert (msg["correlation_id"], msg["causation_id"]) == ("c-1", "m-0")


@pytest.mark.parametrize("builder,args,kwargs,type_", [
    (build_phase_change, ("morning", DAY), {"previous": "off", "segment": 1}, "phase_change"),
    (build_day_start, (DAY,), {}, "day_start"),
    (build_day_end, (DAY,), {"summary": "shipped"}, "day_end"),
    (build_day_start, (DAY,), {"sender": "tuber_0"}, "day_start"),
])
def test_clock_messages_broadcast(builder, args, kwargs, type_):
    msg = builder(*args, **kwargs)
    assert msg["to"] == BROADCAST and msg["type"] == type_


@pytest.mark.parametrize("builder,args,kwargs", [
    (build_phase_change, ("lunch", DAY), {}),
    (build_phase_change, ("build", DAY), {"previous": "nap"}),
    (build_phase_change, ("build", "28/09/2026"), {}),
    (build_phase_change, ("build", DAY), {"segment": "2"}),
    (build_day_start, ("",), {}),
    (build_day_start, (DAY,), {"sender": "tuber_3"}),
    (build_day_end, (DAY,), {"sender": "tuber_7"}),
])
def test_clock_messages_invalid_raise(builder, args, kwargs):
    with pytest.raises(ProtocolError):
        builder(*args, **kwargs)


def test_clock_message_not_broadcast_rejected():
    msg = build_message("office_clock", "tuber_1", "day_start", {"day": DAY})
    with pytest.raises(ProtocolError):
        validate_message(msg)


@pytest.mark.parametrize("msg", [
    "not a dict",
    build_message("tuber_0", "tuber_1", "task_assignment", {"text": "x"}),
    {"type": "directive", "from": "tuber_0", "to": "tuber_1", "payload": {"text": "x"}},
    build_message("tuber_0", "tuber_1", "directive", None),
    {**build_message("tuber_0", "tuber_1", "directive", {"text": "x"}), "payload": ["x"]},
    build_message("tuber_2", "tuber_1", "functional_plan", {"plan": "p", "cc": ["tuber_42"]}),
    build_message("tuber_3", "tuber_4", "directive", {"text": "test it"}),
])
def test_validate_message_rejects_bad_envelopes(msg):
    with pytest.raises(ProtocolError):
        validate_message(msg)


def test_validate_message_accepts_bus_roundtrip():
    msg = build_directive(R.CEO, R.MARKETING, "draft launch copy")
    assert validate_message(dict(msg)) == msg


@pytest.mark.parametrize("msg,expected", [
    ({"type": "directive"}, True), ({"type": "bug_report"}, False), (None, False),
])
def test_is_office_message(msg, expected):
    assert is_office_message(msg) is expected


def test_protocol_error_is_value_error():
    assert issubclass(ProtocolError, ValueError)
    assert protocol.CLOCK_SENDER in protocol.CLOCK_SENDERS


# ── wrap_up (23:45 clock broadcast) ──────────────────────────────────────────
def test_build_wrap_up_is_clock_broadcast():
    directives = [{"title": "Velocity rule", "issue": 3, "status": "done"}]
    msg = protocol.build_wrap_up(DAY, directives=directives, sender="tuber_0")
    assert msg["type"] == "wrap_up" and msg["to"] == BROADCAST and msg["from"] == "tuber_0"
    assert msg["payload"] == {"day": DAY, "directives": directives, "request": "status_report"}
    assert is_office_message(msg)


@pytest.mark.parametrize("sender,to,payload", [
    ("tuber_2", BROADCAST, {"day": DAY}),            # not the clock
    ("office_clock", "tuber_1", {"day": DAY}),       # not a broadcast
    ("office_clock", BROADCAST, {}),                 # day missing
    ("office_clock", BROADCAST, {"day": DAY, "directives": "all"}),
])
def test_wrap_up_rejects_bad_sender_addressing_or_payload(sender, to, payload):
    with pytest.raises(ProtocolError):
        validate_message(build_message(sender, to, "wrap_up", payload))


@pytest.mark.parametrize("sender,superior", [
    (R.ENGINEER, "tuber_1"), (R.TESTER, "tuber_1"), (R.TECH_LEAD, "tuber_0"),
    (R.ANALYST, "tuber_0"), (R.MARKETING, "tuber_0"), (R.OFFICE_MANAGER, "tuber_0"),
])
def test_wrap_up_report_lines_are_rank_legal(sender, superior):
    # The wrap_up handler relies on these: every seat with a superior may report to it.
    assert build_status_report(sender, "end of day", status="on_track", day=DAY)["to"] == superior
