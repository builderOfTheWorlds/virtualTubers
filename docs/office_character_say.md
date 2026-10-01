# office.character_say — spoken office lines as v4 `character_say`

## Overview

`app/office/character_say.py` turns every line an ashiorid_office character speaks into a v4
`character_say` bus message, the WP-15 contract
(`tools/qwen_worker/specs/character_wp15_bus_contracts.yaml`,
`character_generator_updater_v4.md` §3.2). The v4 ingest consumer (`character-ingest`, WP-16)
turns each one into experience rows for the speaker and for every character present, so the
office cast remembers its week.

It has **one caller**: `live_pane.publish_office_line`, the single point where a seat's spoken
line leaves the seat. That call sends the live-transcript `office_line` and the `character_say`
from the same stripped text, so the roundtable and the characters' memory never diverge. The
office handlers reach it through `agent_handlers.office._publish_line`; the CEO's day runner
calls it directly for the 23:45 wrap-up line.

```json
{"type": "character_say", "from": "tuber_3", "to": "broadcast",
 "payload": {"campaign": "ashiorid_office",
             "scene_id": "office-2026-09-28-build",
             "character": "engineer",
             "addressees": ["tester"],
             "present": ["ceo", "tech_lead", "analyst", "engineer", "tester",
                         "marketing", "office_manager", "party_member"],
             "text": "Pushed the velocity rule; over to you."}}
```

The envelope comes from `message_bus.build_message`, so it carries `id`, a UTC `timestamp`
(ingest derives the loop week/day from it) and the office chain's `correlation_id`.

### Rules

| Field | Rule |
|---|---|
| `from` | The seat worker id (`tuber_N`, `office.roles.SEAT`), not `char:<slug>`: v4 ingest resolves seats through `character_agents` (OB-41). |
| `campaign` | Always `ashiorid_office`. |
| `scene_id` | `office-<YYYY-MM-DD>-<phase>`: the office's local calendar date and the 6 h segment phase (`off` / `morning` / `build` / `ship`, the `office.clock` grid) at the moment the line is spoken. One scene per segment of the office day, stable across seats and restarts. The zone is the worker's office tz (`office.clock.configured_epoch_and_tz`, default America/New_York); no epoch is needed, so it never fails before the loop starts. |
| `character` | The speaker's office slug (`agent.office_role`, e.g. `engineer`). |
| `addressees` | Office slugs of the recipients of the protocol message the line goes with (a status report → the superior, a directive → its four recipients, ...). `[]` for a line to the room: broadcasts, phase-change reactions, Office Manager chores, the CEO's wrap-up line. The speaker, `broadcast` and non-office ids are dropped; duplicates removed, order kept. The per-handler list is in [agent_handlers.md](agent_handlers.md) "Spoken lines → character_say". |
| `present` | The whole office roster, all 8 slugs in `office.roles.OfficeRole` order, **Party Member included**: on a normal office day everyone is in the room, and he watches even though he never speaks. |
| `text` | The line, stripped and capped at `live_pane.MAX_TEXT_CHARS` — exactly the transcript text. |

### Gates

- `agent.office.character_say: true` (absent or anything else = off). The seven speaking seat
  configs (`config/workers/office/{ceo,tech_lead,analyst,engineer,tester,marketing,office_manager}.yaml`)
  set it; the Party Member's config does not.
- Never for empty / whitespace text, never for the Party Member (`office_role: party_member`),
  never for a worker without an `office_role`.
- It is independent of `agent.office.live_transcript`: either can be on alone.

## Signature

```python
CHARACTER_SAY = "character_say"
CAMPAIGN = "ashiorid_office"
PAYLOAD_KEYS = ("campaign", "scene_id", "character", "addressees", "present", "text")
SCENE_ID_FMT = "office-{day}-{phase}"
ROSTER = ("ceo", "tech_lead", "analyst", "engineer", "tester", "marketing",
          "office_manager", "party_member")
FLAG = "character_say"

class CharacterSayError(ValueError)

def character_say_enabled(agent_config) -> bool
def office_slug(value) -> str | None
def addressee_slugs(to, speaker=None) -> list[str]
def present_slugs() -> list[str]
def scene_id_for(now=None, tz=None) -> str
def build_character_say(worker_id, character, text, *, scene_id, addressees=(), present=None,
                        campaign=CAMPAIGN, correlation_id=None, causation_id=None) -> dict
def publish_character_say(worker_id, agent_config, producer, text, *, addressees=None,
                          correlation_id=None, now=None) -> dict | None
```

## Parameters

| Name | Type | Required | Notes |
|---|---|---|---|
| `worker_id` | `str` | yes | The seat id; becomes `from`. |
| `agent_config` | `dict` | yes | The worker's `agent` block (`office_role`, `office.character_say`, `office.tz`). |
| `producer` | object with `send(msg)` | yes | e.g. `MessageProducer`. |
| `text` | `str` | yes | Non-blank; otherwise nothing is sent (`publish_*`) or `CharacterSayError` (`build_*`). |
| `addressees` / `to` | id, role, slug or an iterable of them | no | Mapped through `addressee_slugs`. |
| `correlation_id` | `str` | no | The office chain id. |
| `now` | aware `datetime` | no | The scene_id instant; default the current UTC time. |
| `scene_id` | `str` | yes (`build_*`) | Normally `scene_id_for(now, tz)`. |
| `present` | iterable of slugs | no | Default `present_slugs()`. |

## Return Value

- `publish_character_say` → the sent message, or `None` (flag off, not an office character,
  Party Member, empty text, or a send/build failure).
- `build_character_say` → the message dict; the payload has exactly `PAYLOAD_KEYS`, in order.
- `scene_id_for` → `office-<date>-<phase>`; `addressee_slugs` / `present_slugs` → lists of slugs.

## Dependencies

- `message_bus`: `build_message`, `BROADCAST`
- `office.clock`: `PHASES`, `SEGMENT_HOURS`, `configured_epoch_and_tz`
- `office.roles`: `OfficeRole`, `as_role`
- stdlib: `logging`, `datetime`, `zoneinfo`
- Caller: `live_pane.publish_office_line` (and through it `agent_handlers.office._publish_line`,
  `office.day_runner`)

## Usage Examples

```python
import live_pane
# A handler line: the transcript line (if live_transcript) + one character_say.
live_pane.publish_office_line("tuber_3", agent_config, producer, "Pushed it.", None,
                              correlation_id, addressees=["tuber_4"])
```

```python
from datetime import datetime, timezone
from office import character_say as cs

msg = cs.build_character_say(
    "tuber_1", "tech_lead", "Good work, both of you.",
    scene_id=cs.scene_id_for(datetime(2026, 9, 28, 16, tzinfo=timezone.utc)),   # office-2026-09-28-build
    addressees=cs.addressee_slugs(["tuber_3", "tuber_4"], speaker="tech_lead"))  # ["engineer", "tester"]
producer.send(msg)
```

## Error Handling

- `publish_character_say` never raises: a bad tz, a build error or a bus failure is logged at
  ERROR (`event=character_say_publish_failed` on the console) and returns `None`; the office
  chain carries on.
- `build_character_say` raises `CharacterSayError` for a missing character slug or blank text.
- Logging: DEBUG for built / published / skipped (type, sender, scene_id, counts — never the
  text), TRACE on entry.

## Testing

`tests/test_office_character_say.py`: the WP-15 key list and types, the scene_id / addressees /
present rules, every speaking office handler emitting exactly one `character_say` with the right
payload, the flag off / absent, the Party Member, empty lines, a bus failure, and the transcript
and `character_say` sharing one text. `tests/test_office_day_runner.py` covers the CEO's
wrap-up line; the fake-bus e2e in `tests/test_agent_handlers_office.py` checks every
`character_say` of a whole office day; `tests/test_office_configs.py` checks the seat flags.

## Changelog

- **v1.0.0** (2026-09-28): first version. Every spoken office line is also a v4
  `character_say` (seat sender, `office-<date>-<phase>` scenes, recipients as addressees, the
  whole roster present), published from `live_pane.publish_office_line`, gated by
  `agent.office.character_say`.
