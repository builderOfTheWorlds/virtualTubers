"""
office/protocol.py
Builders and validators for the ashiorid_office message types. Every builder
wraps message_bus.build_message (so each message carries `correlation_id` /
`causation_id`) and validates the result before returning it; a rank
violation or malformed payload raises ProtocolError.

Pure module: no Kafka connection, no LLM, no filesystem (message_bus is only
imported for its envelope helpers). Chain of command comes from office.roles.
See docs/office_protocol.md.
"""
import logging
from datetime import date

from message_bus import BROADCAST, build_message, reply_ids
from office.roles import (
    REPORTS_TO,
    SEAT,
    OfficeRole,
    as_role,
    can_direct,
    role_for_seat,
)

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


class ProtocolError(ValueError):
    """A message breaks the office protocol (rank, addressing, or payload)."""


DIRECTIVE = "directive"
FUNCTIONAL_PLAN = "functional_plan"
TECHNICAL_PLAN = "technical_plan"
TEST_REQUEST = "test_request"
STATUS_REPORT = "status_report"
PHASE_CHANGE = "phase_change"
DAY_START = "day_start"
DAY_END = "day_end"
#: The CEO's 23:45 broadcast asking every seat for its end-of-day status_report.
WRAP_UP = "wrap_up"

OFFICE_MESSAGE_TYPES = (DIRECTIVE, FUNCTIONAL_PLAN, TECHNICAL_PLAN, TEST_REQUEST,
                        STATUS_REPORT, PHASE_CHANGE, DAY_START, DAY_END, WRAP_UP)
CLOCK_TYPES = frozenset({PHASE_CHANGE, DAY_START, DAY_END, WRAP_UP})

#: Default sender id for clock broadcasts (not a seat; the clock is not a character).
CLOCK_SENDER = "office_clock"
#: Who may emit clock broadcasts: the clock service, or the CEO seat if the
#: clock is hosted inside the CEO worker.
CLOCK_SENDERS = frozenset({CLOCK_SENDER, SEAT[OfficeRole.CEO]})

#: Day phases (office_campaign_plan.md §4): s0 off, s1 morning, s2 build, s3 ship.
PHASES = ("off", "morning", "build", "ship")
STATUS_VALUES = ("on_track", "blocked", "done")

# type -> (required non-empty str fields, optional fields -> allowed type(s))
_PAYLOAD_SCHEMA = {
    DIRECTIVE: (("text",), {"issue": int, "day": str, "title": str}),
    FUNCTIONAL_PLAN: (("plan",), {"cc": list, "directive_id": str}),
    TECHNICAL_PLAN: (("plan",), {"tasks": list, "directive_id": str}),
    TEST_REQUEST: (("description",), {"branch": str, "commit": str, "paths": list}),
    STATUS_REPORT: (("summary",), {"status": str, "day": str}),
    PHASE_CHANGE: (("phase", "day"), {"previous": str, "segment": int}),
    DAY_START: (("day",), {}),
    DAY_END: (("day",), {"summary": str}),
    WRAP_UP: (("day",), {"directives": list, "request": str}),
}


def _seat(value, what):
    try:
        return SEAT[as_role(value)]
    except ValueError as exc:
        log.error("protocol unknown %s value=%r", what, value)
        raise ProtocolError(f"unknown {what}: {value!r}") from exc


def _role_of(seat, what, msg_type):
    role = role_for_seat(seat)
    if role is None:
        log.debug("protocol not an office seat type=%s %s=%r", msg_type, what, seat)
        raise ProtocolError(f"{msg_type}: {what} {seat!r} is not an office seat")
    return role


def _check_rank(msg_type, sender_seat, recipient_seat):
    """Raise ProtocolError unless `sender_seat` may send `msg_type` to `recipient_seat`."""
    _trace("_check_rank enter type=%s from=%s to=%s", msg_type, sender_seat, recipient_seat)
    if msg_type in CLOCK_TYPES:
        if sender_seat not in CLOCK_SENDERS:
            raise ProtocolError(f"{msg_type}: sender {sender_seat!r} is not the clock")
        if recipient_seat != BROADCAST:
            raise ProtocolError(f"{msg_type}: must be broadcast, got to={recipient_seat!r}")
        return

    sender = _role_of(sender_seat, "sender", msg_type)
    recipient = _role_of(recipient_seat, "recipient", msg_type)
    ok = False
    if msg_type == DIRECTIVE:
        # Directives flow down the chain; Engineer -> Tester is test_request only.
        ok = sender is not OfficeRole.ENGINEER and can_direct(sender, recipient)
    elif msg_type == FUNCTIONAL_PLAN:
        ok = sender is OfficeRole.ANALYST and recipient is OfficeRole.TECH_LEAD
    elif msg_type == TECHNICAL_PLAN:
        ok = sender is OfficeRole.TECH_LEAD and recipient is OfficeRole.CEO
    elif msg_type == TEST_REQUEST:
        ok = recipient is OfficeRole.TESTER and sender in (OfficeRole.ENGINEER, OfficeRole.TECH_LEAD)
    elif msg_type == STATUS_REPORT:
        superior = REPORTS_TO[sender]
        ok = superior is not None and recipient is superior
    log.debug("protocol rank check type=%s sender=%s recipient=%s ok=%s",
              msg_type, sender.value, recipient.value, ok)
    if not ok:
        raise ProtocolError(
            f"{msg_type}: {sender.value} ({sender_seat}) may not send to "
            f"{recipient.value} ({recipient_seat})")


def _check_payload(msg_type, payload):
    _trace("_check_payload enter type=%s keys=%s",
           msg_type, sorted(payload) if isinstance(payload, dict) else None)
    if not isinstance(payload, dict):
        raise ProtocolError(f"{msg_type}: payload must be a dict")
    required, optional = _PAYLOAD_SCHEMA[msg_type]
    for key in required:
        val = payload.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ProtocolError(f"{msg_type}: payload.{key} must be a non-empty string")
    for key, typ in optional.items():
        if key in payload and payload[key] is not None:
            val = payload[key]
            if not isinstance(val, typ) or (typ is int and isinstance(val, bool)):
                raise ProtocolError(f"{msg_type}: payload.{key} must be {typ.__name__}")
    for key in ("day",):
        if key in payload and payload[key] is not None:
            try:
                date.fromisoformat(payload[key])
            except (TypeError, ValueError) as exc:
                raise ProtocolError(f"{msg_type}: payload.day must be YYYY-MM-DD") from exc
    if msg_type == PHASE_CHANGE:
        for key in ("phase", "previous"):
            if payload.get(key) is not None and payload[key] not in PHASES:
                raise ProtocolError(f"{msg_type}: payload.{key} must be one of {PHASES}")
    if msg_type == STATUS_REPORT and payload.get("status") is not None \
            and payload["status"] not in STATUS_VALUES:
        raise ProtocolError(f"{msg_type}: payload.status must be one of {STATUS_VALUES}")
    for key in ("cc", "tasks", "paths"):
        if isinstance(payload.get(key), list) and not all(
                isinstance(x, str) and x for x in payload[key]):
            raise ProtocolError(f"{msg_type}: payload.{key} must be a list of non-empty strings")
    if msg_type == FUNCTIONAL_PLAN and payload.get("cc"):
        for seat in payload["cc"]:
            if role_for_seat(seat) is None:
                raise ProtocolError(f"{msg_type}: cc {seat!r} is not an office seat")


def is_office_message(msg):
    """True when `msg` is a dict whose type is one of OFFICE_MESSAGE_TYPES."""
    return isinstance(msg, dict) and msg.get("type") in OFFICE_MESSAGE_TYPES


def validate_message(msg):
    """Validate an office envelope (as built here or received off the bus).
    Returns `msg` unchanged; raises ProtocolError on any violation."""
    _trace("validate_message enter id=%s type=%s",
           msg.get("id") if isinstance(msg, dict) else None,
           msg.get("type") if isinstance(msg, dict) else None)
    try:
        if not isinstance(msg, dict):
            raise ProtocolError("message must be a dict")
        msg_type = msg.get("type")
        if msg_type not in OFFICE_MESSAGE_TYPES:
            raise ProtocolError(f"not an office message type: {msg_type!r}")
        for key in ("id", "from", "to", "correlation_id"):
            if not isinstance(msg.get(key), str) or not msg[key]:
                raise ProtocolError(f"{msg_type}: envelope field {key!r} missing")
        _check_rank(msg_type, msg["from"], msg["to"])
        _check_payload(msg_type, msg.get("payload"))
    except ProtocolError as exc:
        log.error("protocol validation failed id=%s correlation_id=%s error=%s",
                  msg.get("id") if isinstance(msg, dict) else None,
                  msg.get("correlation_id") if isinstance(msg, dict) else None, exc)
        raise
    log.debug("protocol message valid id=%s type=%s from=%s to=%s correlation_id=%s",
              msg["id"], msg_type, msg["from"], msg["to"], msg["correlation_id"])
    return msg


def _build(from_, to, type_, payload, reply_to=None, correlation_id=None, causation_id=None):
    """Build + validate. `reply_to` (a received message) sets the chain ids
    via message_bus.reply_ids; explicit correlation/causation ids win over it."""
    _trace("_build enter type=%s from=%s to=%s", type_, from_, to)
    ids = reply_ids(reply_to) if reply_to is not None else {}
    msg = build_message(
        from_, to, type_,
        {k: v for k, v in payload.items() if v is not None},
        correlation_id=correlation_id or ids.get("correlation_id"),
        causation_id=causation_id or ids.get("causation_id"),
    )
    validate_message(msg)
    log.info("office message built id=%s type=%s from=%s to=%s correlation_id=%s",
             msg["id"], type_, from_, to, msg["correlation_id"])
    return msg


def build_directive(sender, recipient, text, *, issue=None, title=None, day=None, **ids):
    """CEO -> Tech Lead / Analyst / Marketing / Office Manager (or Tech Lead -> Engineer/Tester)."""
    return _build(_seat(sender, "sender"), _seat(recipient, "recipient"), DIRECTIVE,
                  {"text": text, "issue": issue, "title": title, "day": day}, **ids)


def build_functional_plan(plan, *, sender=OfficeRole.ANALYST, directive_id=None, **ids):
    """Analyst -> Tech Lead, cc CEO (payload.cc lists the CEO seat)."""
    return _build(_seat(sender, "sender"), SEAT[OfficeRole.TECH_LEAD], FUNCTIONAL_PLAN,
                  {"plan": plan, "cc": [SEAT[OfficeRole.CEO]], "directive_id": directive_id},
                  **ids)


def build_technical_plan(plan, *, sender=OfficeRole.TECH_LEAD, tasks=None, directive_id=None,
                         **ids):
    """Tech Lead -> CEO."""
    return _build(_seat(sender, "sender"), SEAT[OfficeRole.CEO], TECHNICAL_PLAN,
                  {"plan": plan, "tasks": tasks, "directive_id": directive_id}, **ids)


def build_test_request(description, *, sender=OfficeRole.ENGINEER, branch=None, commit=None,
                       paths=None, **ids):
    """Engineer (or Tech Lead) -> Tester."""
    return _build(_seat(sender, "sender"), SEAT[OfficeRole.TESTER], TEST_REQUEST,
                  {"description": description, "branch": branch, "commit": commit,
                   "paths": paths}, **ids)


def build_status_report(sender, summary, *, status=None, day=None, **ids):
    """`sender` -> its superior (REPORTS_TO). Raises ProtocolError for roles
    that report to nobody (CEO, Party Member)."""
    role = as_role(sender) if not isinstance(sender, OfficeRole) else sender
    superior = REPORTS_TO.get(role)
    if superior is None:
        log.error("status_report sender has no superior sender=%s", role.value)
        raise ProtocolError(f"status_report: {role.value} reports to nobody")
    return _build(SEAT[role], SEAT[superior], STATUS_REPORT,
                  {"summary": summary, "status": status, "day": day}, **ids)


def build_phase_change(phase, day, *, previous=None, segment=None, sender=CLOCK_SENDER, **ids):
    """Clock broadcast: the day moved into `phase` (one of PHASES)."""
    return _build(sender, BROADCAST, PHASE_CHANGE,
                  {"phase": phase, "day": day, "previous": previous, "segment": segment}, **ids)


def build_day_start(day, *, sender=CLOCK_SENDER, **ids):
    """Clock broadcast at 06:00: work day `day` (YYYY-MM-DD) begins."""
    return _build(sender, BROADCAST, DAY_START, {"day": day}, **ids)


def build_wrap_up(day, *, directives=None, request=STATUS_REPORT, sender=CLOCK_SENDER, **ids):
    """Clock broadcast at 23:45: every seat with a superior sends it one
    end-of-day status_report. `directives` is the day runner's summary list
    ({title, issue, status, source, kind} dicts)."""
    return _build(sender, BROADCAST, WRAP_UP,
                  {"day": day, "directives": list(directives or []), "request": request}, **ids)


def build_day_end(day, *, summary=None, sender=CLOCK_SENDER, **ids):
    """Clock broadcast at 00:00: work day `day` (YYYY-MM-DD) ends."""
    return _build(sender, BROADCAST, DAY_END, {"day": day, "summary": summary}, **ids)
