# office.protocol

## Overview

`app/office/protocol.py` builds and validates the ashiorid_office bus messages. Every
builder wraps `message_bus.build_message`, so each envelope carries `id`, `timestamp`,
`correlation_id` and `causation_id` (see [message_bus.md](message_bus.md)). Each builder
validates the result before returning it. Rank violations, bad addressing, and malformed
payloads raise `ProtocolError`. It is a pure module: `message_bus` is imported only for its
envelope helpers, and nothing here connects to Kafka, calls an LLM, or touches the filesystem.

Every type except the `day_start` / `day_end` clock edges has a handler in `agent_handlers.MESSAGE_HANDLERS` (docs/agent_handlers.md); a test checks this.

| Type | From → To | Required payload | Optional payload |
|---|---|---|---|
| `directive` | CEO → Tech Lead / Analyst / Marketing / Office Manager; Tech Lead → Engineer / Tester | `text` | `issue` (int), `title`, `day` |
| `functional_plan` | Analyst → Tech Lead, `cc: ["tuber_0"]` | `plan` | `cc` (seats), `directive_id` |
| `technical_plan` | Tech Lead → CEO | `plan` | `tasks` (list[str]), `directive_id` |
| `test_request` | Engineer or Tech Lead → Tester | `description` | `branch`, `commit`, `paths` (list[str]) |
| `status_report` | sender → `REPORTS_TO[sender]` | `summary` | `status` (`on_track`/`blocked`/`done`), `day` |
| `phase_change` | clock → `broadcast` | `phase`, `day` | `previous`, `segment` (int) |
| `day_start` | clock → `broadcast` | `day` | — |
| `day_end` | clock → `broadcast` | `day` | `summary` |
| `wrap_up` | clock → `broadcast` (23:45) | `day` | `directives` (list of `{title, issue, status, source, kind}`), `request` (default `status_report`) |

`day` is always ISO `YYYY-MM-DD`. `phase`/`previous` are in `PHASES = ("off", "morning", "build", "ship")`
(segments s0–s3 of the 18/6 day). Clock messages may come from `CLOCK_SENDER = "office_clock"` or the
CEO seat `tuber_0` (`CLOCK_SENDERS`). Payload keys whose value is `None` are omitted.

## Signature

```python
class ProtocolError(ValueError)

OFFICE_MESSAGE_TYPES: tuple[str, ...]
PHASES, STATUS_VALUES, CLOCK_SENDER, CLOCK_SENDERS

def build_directive(sender, recipient, text, *, issue=None, title=None, day=None, **ids) -> dict
def build_functional_plan(plan, *, sender=OfficeRole.ANALYST, directive_id=None, **ids) -> dict
def build_technical_plan(plan, *, sender=OfficeRole.TECH_LEAD, tasks=None, directive_id=None, **ids) -> dict
def build_test_request(description, *, sender=OfficeRole.ENGINEER, branch=None, commit=None, paths=None, **ids) -> dict
def build_status_report(sender, summary, *, status=None, day=None, **ids) -> dict
def build_phase_change(phase, day, *, previous=None, segment=None, sender=CLOCK_SENDER, **ids) -> dict
def build_day_start(day, *, sender=CLOCK_SENDER, **ids) -> dict
def build_day_end(day, *, summary=None, sender=CLOCK_SENDER, **ids) -> dict
def build_wrap_up(day, *, directives=None, request="status_report", sender=CLOCK_SENDER, **ids) -> dict
def validate_message(msg: dict) -> dict
def is_office_message(msg) -> bool
```

## Parameters

- `sender` / `recipient` — `OfficeRole`, role value, or seat id (see [office_roles.md](office_roles.md)).
  Recipients of plans, test requests and status reports are fixed by the protocol.
- `**ids` — any of:
  - `reply_to=<received msg>` — continue that chain (`message_bus.reply_ids`).
  - `correlation_id=`, `causation_id=` — explicit ids; these override `reply_to`.
  - With no ids, the message starts a new chain (`correlation_id == id`).
- `msg` (validate) — a full envelope, e.g. one consumed off the bus.

## Return Value

Builders return the validated envelope dict, ready for `MessageProducer.send`.
`validate_message` returns `msg` unchanged. `is_office_message` returns a bool.

## Dependencies

- `message_bus.build_message`, `reply_ids`, `BROADCAST`
- `office.roles` (`SEAT`, `REPORTS_TO`, `can_direct`, `as_role`, `role_for_seat`)
- stdlib `datetime.date`, `logging`

## Usage Examples

```python
from office.protocol import build_directive, build_functional_plan, build_technical_plan
from office.roles import OfficeRole as R

d = build_directive(R.CEO, R.ANALYST, "Requirements for velocity checks", issue=7, day="2026-09-28")
fp = build_functional_plan("FR-1 ... FR-4", reply_to=d, directive_id=d["id"])
tp = build_technical_plan("Add velocity rule module", tasks=["rule", "api"], reply_to=fp)
assert tp["correlation_id"] == d["id"]
```

```python
from office.protocol import build_phase_change, validate_message, ProtocolError, is_office_message

producer.send(build_phase_change("build", "2026-09-28", previous="morning", segment=2))

for msg in consumer:
    if is_office_message(msg):
        try:
            validate_message(msg)
        except ProtocolError:
            continue  # drop off-protocol traffic; the error is already logged
```

## Error Handling

`ProtocolError` (a `ValueError` subclass) is raised when:

- a sender or recipient is not an office role or seat;
- the sender is not allowed to send that type to that recipient. Examples: CEO → Engineer
  directive, an Engineer `directive` to the Tester (it must be a `test_request`), anything
  addressed to or from the Party Member, or a `status_report` from the CEO or Party Member;
- a clock message comes from a sender outside `CLOCK_SENDERS` or is not addressed to `broadcast`;
- the payload is not a dict, a required field is missing, empty or not a string, an optional
  field has the wrong type, `day` is not ISO, `phase` or `status` is not an allowed value, or
  `cc` contains a non-seat;
- the envelope is not a dict, has a non-office type, or lacks `id`/`from`/`to`/`correlation_id`.

Failures are logged at ERROR with the message `id` and `correlation_id`. Successful builds are
logged at INFO. Rank decisions are logged at DEBUG, and entry points at TRACE (level 5).

## Decisions

- `test_request` may also come from the Tech Lead, who directs the Tester.
- `directive` also covers Tech Lead → Engineer/Tester tasking. The Engineer → Tester edge
  is `test_request` only.
- Clock broadcasts use a non-seat sender, `office_clock`. OB-06 decides who actually emits them.

## Changelog

- **v1.0.0** (2026-09-27) — Initial version (OB-05): 8 message types, builders, validator,
  `ProtocolError`.
- **v1.1.0** (2026-09-28) — `wrap_up` becomes the 9th type: a clock broadcast
  (`CLOCK_TYPES`) with builder `build_wrap_up`, so the 23:45 request for status reports is
  rank-checked like `phase_change`.
