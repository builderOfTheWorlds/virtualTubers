"""
office/character_say.py
The v4 `character_say` bus message for every line an office character
speaks (character_generator_updater_v4.md §3.2, the WP-15 contract in
tools/qwen_worker/specs/character_wp15_bus_contracts.yaml). The v4 ingest
consumer turns each one into an experience row for the speaker and for
every character present.

    {"type": "character_say", "from": "tuber_3", "to": "broadcast",
     "payload": {"campaign": "ashiorid_office",
                 "scene_id": "office-2026-09-28-build",
                 "character": "engineer",
                 "addressees": ["tester"],
                 "present": ["ceo", "tech_lead", "analyst", "engineer", "tester",
                             "marketing", "office_manager", "party_member"],
                 "text": "..."}}

Rules (docs/office_character_say.md):
  - sender: the seat worker id (`tuber_N`, office.roles.SEAT), not
    `char:<slug>`: v4 ingest resolves the seat through character_agents (OB-41).
  - scene_id: `office-<YYYY-MM-DD>-<phase>`, the office's local calendar date
    and the 6 h segment's phase (off | morning | build | ship) at the moment
    the line is spoken (office.clock grid). Stable for a whole segment.
  - character: the speaker's office slug (agent.office_role).
  - addressees: office slugs of the recipients of the protocol message the
    line goes with; [] for a line to the room (broadcast, phase reactions,
    chores). Never the speaker; unknown ids and `broadcast` are dropped.
  - present: the whole office roster, all 8 slugs in office.roles order,
    Party Member included: he is in the room even though he never speaks.
  - Gated by `agent.office.character_say: true` (off when absent); empty
    text and the Party Member never publish.

The message is built with message_bus.build_message (id, UTC timestamp,
correlation_id). live_pane.publish_office_line is the ONE caller: the live
transcript line and the character_say share one text, so they never diverge.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from message_bus import BROADCAST, build_message
from office.clock import PHASES, SEGMENT_HOURS, configured_epoch_and_tz
from office.roles import OfficeRole, as_role

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

CHARACTER_SAY = "character_say"
CAMPAIGN = "ashiorid_office"
#: The WP-15 payload keys, in contract order (exactly these, nothing else).
PAYLOAD_KEYS = ("campaign", "scene_id", "character", "addressees", "present", "text")
SCENE_ID_FMT = "office-{day}-{phase}"
#: Every office character, office.roles order. All are in the room all day.
ROSTER = tuple(role.value for role in OfficeRole)
#: agent.office flag gating the message.
FLAG = "character_say"


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


class CharacterSayError(ValueError):
    """A character_say could not be built (no speaker slug, empty text)."""


def _office_block(agent_config):
    block = (agent_config or {}).get("office")
    return block if isinstance(block, dict) else {}


def character_say_enabled(agent_config):
    """Seat opt-in: `agent.office.character_say: true`."""
    return _office_block(agent_config).get(FLAG) is True


def office_slug(value):
    """The office slug for a role, a slug or a seat id ('tuber_3' ->
    'engineer'), or None for anything else (broadcast, roundtable, ...)."""
    try:
        return as_role(value).value
    except ValueError:
        return None


def addressee_slugs(to, speaker=None):
    """Office slugs of `to` (one id or an iterable of ids), de-duplicated in
    order; the speaker, `broadcast` and non-office ids are dropped."""
    if to is None:
        return []
    items = [to] if isinstance(to, (str, OfficeRole)) else list(to)
    out = []
    for item in items:
        slug = office_slug(item)
        if slug is None:
            log.debug("character_say addressee dropped value=%r", item)
            continue
        if slug != speaker and slug not in out:
            out.append(slug)
    return out


def present_slugs():
    """Who is in the room: the whole office roster (Party Member included)."""
    return list(ROSTER)


def scene_id_for(now=None, tz=None):
    """`office-<local date>-<phase>` for the instant `now` (tz-aware; default
    the current UTC instant) on the office wall clock `tz` (default NY).
    Uses the office.clock segment grid but no epoch, so it never raises for
    an instant before the loop starts."""
    now = now or datetime.now(timezone.utc)
    zone = ZoneInfo(tz or "America/New_York")
    local = now.astimezone(zone)
    phase = PHASES[local.hour // SEGMENT_HOURS]
    return SCENE_ID_FMT.format(day=local.date().isoformat(), phase=phase)


def build_character_say(worker_id, character, text, *, scene_id, addressees=(), present=None,
                        campaign=CAMPAIGN, correlation_id=None, causation_id=None):
    """The v4 character_say envelope from seat `worker_id`. Raises
    CharacterSayError for a missing character slug or blank text."""
    _trace("build_character_say enter worker=%s character=%s", worker_id, character)
    if not isinstance(character, str) or not character:
        raise CharacterSayError(f"character must be an office slug, got {character!r}")
    if not isinstance(text, str) or not text.strip():
        raise CharacterSayError("character_say text must be non-empty")
    payload = {
        "campaign": campaign,
        "scene_id": scene_id,
        "character": character,
        "addressees": list(addressees),
        "present": list(present if present is not None else present_slugs()),
        "text": text,
    }
    msg = build_message(worker_id, BROADCAST, CHARACTER_SAY, payload,
                        correlation_id=correlation_id, causation_id=causation_id)
    log.debug("character_say built type=%s from=%s scene_id=%s addressees=%d",
              CHARACTER_SAY, worker_id, scene_id, len(payload["addressees"]))
    return msg


def publish_character_say(worker_id, agent_config, producer, text, *, addressees=None,
                          correlation_id=None, now=None):
    """Send one character_say for a line seat `worker_id` just spoke, when
    the seat opted in. Returns the message or None (flag off, not an office
    character, Party Member, empty text, bus failure). Never raises."""
    _trace("publish_character_say enter worker=%s", worker_id)
    try:
        if not character_say_enabled(agent_config):
            return None
        character = office_slug((agent_config or {}).get("office_role"))
        if character is None:
            log.debug("character_say skipped worker=%s reason=not_an_office_character", worker_id)
            return None
        if character == OfficeRole.PARTY_MEMBER.value:
            log.debug("character_say skipped worker=%s reason=party_member", worker_id)
            return None
        if not isinstance(text, str) or not text.strip():
            log.debug("character_say skipped worker=%s reason=empty", worker_id)
            return None
        _, tz = configured_epoch_and_tz(agent_config)
        msg = build_character_say(
            worker_id, character, text, scene_id=scene_id_for(now, tz),
            addressees=addressee_slugs(addressees, speaker=character),
            correlation_id=correlation_id)
        producer.send(msg)
        log.debug("character_say published worker=%s character=%s scene_id=%s",
                  worker_id, character, msg["payload"]["scene_id"])
        return msg
    except Exception as exc:  # noqa: BLE001 — never break the office chain
        print(f"[agent:{worker_id}] ERROR event=character_say_publish_failed error='{exc}'")
        log.error("character_say publish failed worker=%s error=%s", worker_id, exc)
        return None


__all__ = [
    "CAMPAIGN", "CHARACTER_SAY", "FLAG", "PAYLOAD_KEYS", "ROSTER", "SCENE_ID_FMT",
    "CharacterSayError", "addressee_slugs", "build_character_say", "character_say_enabled",
    "office_slug", "present_slugs", "publish_character_say", "scene_id_for",
]
