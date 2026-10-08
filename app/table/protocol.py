"""Live agent table message protocol (build plan P3.1; agent_dnd_architecture §4.2).

The arbiter and every seat build and parse table messages ONLY through this
module. Validation is strict: unknown type, missing field, wrong type,
out-of-range number or unknown enum value raises ProtocolError naming the field.

U6: a seat's private intent / reasoning never travels on the bus. Any payload
key in PRIVATE_KEYS (top level or inside a committed_transcript entry) is
rejected. Reasoning reaches the seat's Thinking pane via `agent_thinking`,
which is not a table message.
"""
import copy
import logging

from message_bus import build_message

log = logging.getLogger(__name__)

MESSAGE_TYPES = (
    "scene_start", "scene_direction", "think_request", "think_done",
    "turn_assignment", "character_reply", "retake", "gm_overrule",
    "adjudication", "scene_resolve", "scene_stuck", "operator_override",
)
VERDICTS = ("pass", "reject")
OPERATOR_ACTIONS = ("accept_all", "skip_scene", "abort_session")
TRANSCRIPT_KINDS = ("direction", "reply", "overrule")
PRIVATE_KEYS = ("intent", "private_intent", "reasoning", "reasoning_content", "thinking")

# Field kinds used by the per-field checker.
_NESTR = "nonempty_str"      # non-empty str
_STR = "str"
_DICT = "dict"
_BOOL = "bool"
_INT0 = "int>=0"
_INT1 = "int>=1"
_POS = "number>0"
_SEATS = "seats"             # non-empty list[str]
_STRLIST = "list[str]"
_TRANSCRIPT = "transcript"
_VERDICT = "verdict"
_ACTION = "action"
_OPT_STR = "str_or_none"

_SPEC = {
    "scene_start": (("scene_id", _NESTR), ("contract", _DICT), ("round", _INT1),
                    ("max_rounds", _INT1), ("seats", _SEATS)),
    "scene_direction": (("scene_id", _NESTR), ("round", _INT0), ("speaker", _NESTR),
                        ("text", _STR), ("expects", _STRLIST), ("must_resolve", _STR)),
    "think_request": (("scene_id", _NESTR), ("round", _INT0), ("seats", _SEATS),
                      ("committed_transcript", _TRANSCRIPT), ("deadline_s", _POS)),
    "think_done": (("scene_id", _NESTR), ("round", _INT0), ("seat", _NESTR), ("ok", _BOOL)),
    "turn_assignment": (("scene_id", _NESTR), ("round", _INT0), ("seat", _NESTR),
                        ("order_pos", _INT0), ("committed_transcript", _TRANSCRIPT),
                        ("instruction", _STR), ("deadline_s", _POS), ("max_retries", _INT0)),
    "character_reply": (("scene_id", _NESTR), ("round", _INT0), ("seat", _NESTR),
                        ("text", _STR), ("took", _BOOL), ("reason", _STR)),
    "retake": (("scene_id", _NESTR), ("round", _INT0), ("seat", _NESTR), ("reason", _STR),
               ("retry", _INT1), ("max", _INT1)),
    "gm_overrule": (("scene_id", _NESTR), ("round", _INT0), ("seat", _NESTR),
                    ("committed_text", _STR), ("note", _STR)),
    "adjudication": (("scene_id", _NESTR), ("round", _INT0), ("verdict", _VERDICT),
                     ("notes", _STR)),
    "scene_resolve": (("scene_id", _NESTR), ("resolved", _BOOL), ("state_delta", _DICT),
                      ("record_id", _OPT_STR)),
    "scene_stuck": (("scene_id", _NESTR), ("reason", _STR), ("state", _DICT)),
    "operator_override": (("scene_id", _NESTR), ("action", _ACTION), ("note", _STR)),
}

#: type -> tuple of required payload field names (in check order).
REQUIRED = {type_: tuple(name for name, _ in fields) for type_, fields in _SPEC.items()}


class ProtocolError(ValueError):
    """A table message failed validation. `.field` is None for an unknown type."""

    def __init__(self, type_, field, message):
        self.type_ = type_
        self.field = field
        self.message = message
        super().__init__(f"{type_}.{field}: {message}")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_transcript(type_, value):
    field = "committed_transcript"
    if not isinstance(value, list):
        raise ProtocolError(type_, field, "must be a list of {speaker, text, kind}")
    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ProtocolError(type_, field, f"entry {i} must be a dict")
        speaker = entry.get("speaker")
        if not isinstance(speaker, str) or not speaker:
            raise ProtocolError(type_, field, f"entry {i} speaker must be a non-empty str")
        if not isinstance(entry.get("text"), str):
            raise ProtocolError(type_, field, f"entry {i} text must be a str")
        if entry.get("kind") not in TRANSCRIPT_KINDS:
            raise ProtocolError(type_, field, f"entry {i} kind must be one of {TRANSCRIPT_KINDS}")


def _check_field(type_, name, kind, value):
    if kind == _NESTR:
        ok = isinstance(value, str) and bool(value)
        expect = "a non-empty str"
    elif kind == _STR:
        ok, expect = isinstance(value, str), "a str"
    elif kind == _OPT_STR:
        ok, expect = value is None or isinstance(value, str), "a str or None"
    elif kind == _DICT:
        ok, expect = isinstance(value, dict), "a dict"
    elif kind == _BOOL:
        ok, expect = isinstance(value, bool), "a bool"
    elif kind == _INT0:
        ok, expect = _is_int(value) and value >= 0, "an int >= 0"
    elif kind == _INT1:
        ok, expect = _is_int(value) and value >= 1, "an int >= 1"
    elif kind == _POS:
        ok, expect = _is_number(value) and value > 0, "a number > 0"
    elif kind == _SEATS:
        ok = (isinstance(value, list) and bool(value)
              and all(isinstance(s, str) and s for s in value))
        expect = "a non-empty list of non-empty str"
    elif kind == _STRLIST:
        ok = isinstance(value, list) and all(isinstance(s, str) for s in value)
        expect = "a list of str"
    elif kind == _VERDICT:
        ok, expect = value in VERDICTS, f"one of {VERDICTS}"
    elif kind == _ACTION:
        ok, expect = value in OPERATOR_ACTIONS, f"one of {OPERATOR_ACTIONS}"
    elif kind == _TRANSCRIPT:
        _check_transcript(type_, value)
        return
    else:  # pragma: no cover - spec table is static
        raise AssertionError(kind)
    if not ok:
        raise ProtocolError(type_, name, f"must be {expect}")


def _check_private(type_, payload):
    for key in PRIVATE_KEYS:
        if key in payload:
            raise ProtocolError(type_, key, "private intent/reasoning never travels on the bus")
    transcript = payload.get("committed_transcript")
    if isinstance(transcript, list):
        for entry in transcript:
            if isinstance(entry, dict):
                for key in PRIVATE_KEYS:
                    if key in entry:
                        raise ProtocolError(
                            type_, key,
                            "private intent/reasoning never travels on the bus "
                            "(committed_transcript entry)")


def _validate(type_, payload):
    if type_ not in _SPEC:
        raise ProtocolError(type_, None, "unknown table message type")
    if not isinstance(payload, dict):
        raise ProtocolError(type_, "payload", "must be a dict")
    _check_private(type_, payload)
    fields = _SPEC[type_]
    for name, _ in fields:
        if name not in payload:
            raise ProtocolError(type_, name, "required field missing")
    for name, kind in fields:
        _check_field(type_, name, kind, payload[name])
    if type_ == "retake" and payload["retry"] > payload["max"]:
        raise ProtocolError(type_, "retry", "retry may not exceed max")


def validate(type_, payload):
    """Raise ProtocolError if `payload` is not a valid `type_` table payload."""
    try:
        _validate(type_, payload)
    except ProtocolError as exc:
        log.debug("table protocol reject type=%s field=%s", exc.type_, exc.field)
        raise


def build(type_, from_, to, payload, *, turn=None, correlation_id=None, causation_id=None):
    """Validate a deep copy of `payload` and wrap it in a bus envelope with
    top-level audit keys `scene` and `turn`. Never mutates the caller's dict."""
    body = copy.deepcopy(payload)
    validate(type_, body)
    msg = build_message(from_, to, type_, body, correlation_id, causation_id)
    msg["scene"] = body["scene_id"]
    msg["turn"] = turn
    log.debug("table protocol build type=%s", type_)
    return msg


def parse(msg):
    """Validate a received table message and return a deep copy of its payload."""
    if not isinstance(msg, dict):
        raise ProtocolError(None, None, "message must be a dict")
    type_ = msg.get("type")
    payload = msg.get("payload")
    validate(type_, payload)
    return copy.deepcopy(payload)


def is_table_message(msg):
    """True when `msg` is a dict whose type is a table message type."""
    return isinstance(msg, dict) and msg.get("type") in MESSAGE_TYPES
