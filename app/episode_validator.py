"""
episode_validator.py
The gate every episode passes before it enters the library
(docs/episode_validator.md). message-api's POST /replays runs this on an
uploaded episode script and refuses to store anything that fails.

Episodes used to reach the workers by being copied onto the deploy host,
which meant nothing between "a file appeared in replays/" and "it plays on
a live Twitch stream" ever checked the file. Five stages now do:

  1. shape       — the canonical key set session_log_parser.parse_session
                   produces, and per-event-type required fields.
  2. name        — basename-only, character-restricted. Preserves the
                   traversal-safety property replay_pane.resolve_episode
                   used to get for free from being a filesystem lookup.
  3. leak audit  — session_log_parser.audit, the SAME strict regex
                   scripts/build_replay_library.py applies locally. The
                   server, not the dev box, is now the last line of defense.
  4. show header — the optional 'show' block (roundtable_stream_design.md v1.1
                   §7.1) that casts slots to personas and voices. V1 static
                   validation (§8.1): slot ids are well formed and in roster,
                   persona keys are cast slots, no platform detail leaks into
                   story data, and every symbolic voice name is one the
                   registry actually knows. This is the check v1.0 wrongly
                   believed the dry run performed — _dry_run never imports
                   tts_client, so a typo'd voice used to reach air and kill
                   the show on EVERY channel.
  5. dry run     — actually render the whole episode through replay.Performer
                   into a throwaway buffer with pacing disabled, and group it
                   with revoice.plan_scenes. This is the "won't have issues
                   replaying it" check: an episode that crashes the renderer
                   is rejected here rather than on air.

SECURITY: a leak-audit failure must never echo the matched text — it is by
construction the secret. EpisodeInvalid carries the rule that fired and
where, never the match itself. A voice NAME is not a secret and is echoed
deliberately (§8.1 rule 4) — naming it is the entire point of that check.

CAPABILITY: this module runs inside message-api, which has NO /data/voices
mount (docker-compose.yml:317-334). The show-header stage therefore never
stats, opens or otherwise touches a voice asset: registry membership is a
pure config lookup through voice_registry.is_known.
"""
import io
import json
import re

import voice_registry
from session_log_parser import audit
from voice_registry import PLATFORM_ONLY_KEYS

# Minimum performable events, matching build_replay_library.py's
# --min-events default: fewer than this is nothing watchable.
MIN_EVENTS = 5

# Cap on the serialized script. The largest real episode in the library is
# ~680 KB; this leaves a wide margin while keeping a hostile upload from
# pinning the dry-run render.
MAX_BYTES = 8 * 1024 * 1024

REQUIRED_KEYS = ("source", "project", "session_id", "date", "events")

# Every event type replay.Performer._perform_events dispatches on, with the
# fields that path reads. Unknown types are silently skipped by the
# performer, so allowing one through here would store an episode that airs
# as dead air — reject instead.
REQUIRED_EVENT_FIELDS = {
    "user_message": ("text",),
    "assistant_text": ("text",),
    "tool_call": ("tool",),
}

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# The global tuber roster size. A show may cast a subset of it; slots outside
# it do not exist on any channel, so casting one is an authoring error. One
# constant so widening the roster is a one-line change here.
ROSTER_SIZE = 7

# Slot ids are positional, never persona names (§7.1): 'speaker' on an event
# references the SLOT, and the persona name is display data only.
SLOT_RE = re.compile(r"^tuber_([0-9]+)$")



class EpisodeInvalid(ValueError):
    """An uploaded episode that must not enter the library. The message is
    operator-facing (it becomes an HTTP 400 detail), so it never contains
    episode content."""


def _check_shape(script):
    if not isinstance(script, dict):
        raise EpisodeInvalid(
            f"expected a JSON object, got {type(script).__name__}")
    missing = [key for key in REQUIRED_KEYS if key not in script]
    if missing:
        raise EpisodeInvalid(f"missing required key(s): {', '.join(missing)}")

    events = script["events"]
    if not isinstance(events, list):
        raise EpisodeInvalid(
            f"'events' must be a list, got {type(events).__name__}")
    if len(events) < MIN_EVENTS:
        raise EpisodeInvalid(
            f"only {len(events)} event(s) — at least {MIN_EVENTS} are needed "
            f"for a watchable episode")

    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise EpisodeInvalid(
                f"event {index} is a {type(event).__name__}, expected an object")
        kind = event.get("type")
        if kind not in REQUIRED_EVENT_FIELDS:
            raise EpisodeInvalid(
                f"event {index} has unsupported type {kind!r} — expected one of "
                f"{', '.join(sorted(REQUIRED_EVENT_FIELDS))}")
        for field in REQUIRED_EVENT_FIELDS[kind]:
            if field not in event:
                raise EpisodeInvalid(
                    f"event {index} ({kind}) is missing required field {field!r}")


def resolve_name(script, override=None):
    """The library key for an episode: the caller's override, else the
    script's own 'source' (which equals the old filename stem). Raises
    EpisodeInvalid when it isn't a safe basename."""
    name = str(override if override else (script or {}).get("source") or "").strip()
    if not name:
        raise EpisodeInvalid(
            "no episode name — pass ?name= or set 'source' in the script")
    if not NAME_RE.match(name):
        raise EpisodeInvalid(
            "episode name must be 1-128 characters of letters, digits, dot, "
            "dash or underscore (no path separators)")
    return name


def _dry_run(script):
    """Render the whole episode into a throwaway buffer with pacing off, and
    run the narration planner over it. Anything that raises means this
    episode would break on air."""
    # Imported here, not at module scope: this pulls in the renderer, which
    # a caller only validating shape shouldn't have to have present.
    from replay import Pacer, Performer
    from revoice import plan_scenes

    try:
        performer = Performer(
            out=io.StringIO(),
            pacer=Pacer(enabled=False),
            state_path=None,   # no avatar state writes during a dry run
        )
        performer.perform(script)
        plan_scenes(script["events"])
    except Exception as exc:
        raise EpisodeInvalid(
            f"episode failed a dry-run performance: "
            f"{type(exc).__name__}: {exc}") from exc


def _check_show(script):
    """V1 static validation of the optional 'show' header
    (roundtable_stream_design.md v1.1 §8.1).

    An episode with no 'show' key is untouched — that is the §7.4
    backwards-compat contract, and every recorded-session replay relies on it.

    Needs no voice files: the voice check is registry MEMBERSHIP via
    voice_registry.is_known, a pure config lookup, because message-api has no
    /data/voices mount. Whether the named asset actually loads is V2's job
    (§8.2, `python3 app/voice_registry.py --verify`).
    """
    if "show" not in script:
        return

    show = script["show"]
    if not isinstance(show, dict):
        raise EpisodeInvalid(
            f"'show' must be an object, got {type(show).__name__} — see the "
            f"show header format in roundtable_stream_design.md §7.1")

    slots = show.get("slots")
    if slots is None:
        raise EpisodeInvalid(
            "'show' is present but has no 'slots' — list the slot ids this "
            "show casts, e.g. [\"tuber_0\", \"tuber_1\"]")
    if not isinstance(slots, list):
        raise EpisodeInvalid(
            f"'show.slots' must be a list, got {type(slots).__name__}")
    if not slots:
        raise EpisodeInvalid(
            "'show.slots' is empty — a show must cast at least one slot")

    for index, slot in enumerate(slots):
        if not isinstance(slot, str):
            raise EpisodeInvalid(
                f"'show.slots[{index}]' must be a string slot id, got "
                f"{type(slot).__name__}")
        match = SLOT_RE.match(slot)
        if not match:
            raise EpisodeInvalid(
                f"'show.slots[{index}]' is {slot!r}, which is not a slot id — "
                f"expected tuber_N (0 to {ROSTER_SIZE - 1})")
        if int(match.group(1)) >= ROSTER_SIZE:
            raise EpisodeInvalid(
                f"'show.slots[{index}]' is {slot!r}, outside the roster of "
                f"{ROSTER_SIZE} — valid slots are tuber_0 to "
                f"tuber_{ROSTER_SIZE - 1}")

    cast = set(slots)

    persona = show.get("persona")
    if persona is not None:
        if not isinstance(persona, dict):
            raise EpisodeInvalid(
                f"'show.persona' must be an object mapping slot id to "
                f"{{name, voice}}, got {type(persona).__name__}")
        for slot, block in persona.items():
            if slot not in cast:
                raise EpisodeInvalid(
                    f"'show.persona' casts {slot!r}, which is not in "
                    f"'show.slots' ({', '.join(sorted(cast))}) — add the slot "
                    f"to the roster or drop the persona")
            if not isinstance(block, dict):
                raise EpisodeInvalid(
                    f"'show.persona.{slot}' must be an object with 'name' and "
                    f"'voice', got {type(block).__name__}")
            leaked = [key for key in PLATFORM_ONLY_KEYS if key in block]
            if leaked:
                raise EpisodeInvalid(
                    f"'show.persona.{slot}' carries platform detail "
                    f"({', '.join(leaked)}) — story data references the "
                    f"symbolic voice registry only. Use "
                    f"'voice': '<name from config/voices.yaml>'.")
            voice = block.get("voice")
            if voice is not None and not voice_registry.is_known(voice):
                # A voice NAME is not a secret (unlike a leak-audit match), and
                # naming it is the whole point: v1.0 shipped a failure mode
                # where an unknown voice killed the show without naming itself.
                raise EpisodeInvalid(
                    f"'show.persona.{slot}' names voice {voice!r}, which is "
                    f"not in the voice registry. Known voices: "
                    f"{', '.join(voice_registry.names())}")

    for index, event in enumerate(script.get("events") or []):
        if not isinstance(event, dict):
            continue
        speaker = event.get("speaker")
        # A speaker that does not LOOK like a slot id is allowed: it renders in
        # the show log with the raw id (§7.4, OPEN-2 default). Only a
        # slot-shaped speaker naming an uncast slot is an authoring error.
        if isinstance(speaker, str) and SLOT_RE.match(speaker) and speaker not in cast:
            raise EpisodeInvalid(
                f"event {index} has speaker {speaker!r}, which is not in "
                f"'show.slots' ({', '.join(sorted(cast))}) — cast the slot or "
                f"reassign the line")

    # Optional audio block (docs/voice_gate.md): how many voices may sound at
    # once on this show (default 1 = strict serialization) and the deliberate
    # silence between lines. Checked statically here — the gate itself reads
    # the same key at air time, so a value that fails here can never reach a
    # live show, and a value that passes here is guaranteed sane.
    audio = show.get("audio")
    if audio is not None:
        if not isinstance(audio, dict):
            raise EpisodeInvalid(
                f"'show.audio' must be an object (e.g. "
                f"{{\"max_concurrent\": 1, \"line_gap_s\": 0.25}}), got "
                f"{type(audio).__name__}")
        max_concurrent = audio.get("max_concurrent")
        if max_concurrent is not None:
            if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int):
                raise EpisodeInvalid(
                    f"'show.audio.max_concurrent' must be an integer number of "
                    f"simultaneous voices, got {type(max_concurrent).__name__}")
            if not (1 <= max_concurrent <= ROSTER_SIZE):
                raise EpisodeInvalid(
                    f"'show.audio.max_concurrent' is {max_concurrent}, outside "
                    f"the valid range 1..{ROSTER_SIZE} — 1 means one voice at a "
                    f"time (the default); go higher for deliberate overlap, "
                    f"but never more than the roster can supply")
        line_gap_s = audio.get("line_gap_s")
        if line_gap_s is not None:
            if isinstance(line_gap_s, bool) or not isinstance(line_gap_s, (int, float)):
                raise EpisodeInvalid(
                    f"'show.audio.line_gap_s' must be a number of seconds, "
                    f"got {type(line_gap_s).__name__}")
            if not (0.0 <= float(line_gap_s) <= 60.0):
                raise EpisodeInvalid(
                    f"'show.audio.line_gap_s' is {line_gap_s}s, outside the "
                    f"valid range 0..60 — 0 means back-to-back lines")


def validate_episode(script, name=None):
    """Validate an uploaded episode end to end.

    Returns {"name", "event_count", "byte_size"} on success.
    Raises EpisodeInvalid — with an operator-safe message — on any failure.
    """
    _check_shape(script)
    resolved = resolve_name(script, name)

    payload = json.dumps(script, ensure_ascii=False)
    byte_size = len(payload.encode("utf-8"))
    if byte_size > MAX_BYTES:
        raise EpisodeInvalid(
            f"episode is {byte_size} bytes, over the {MAX_BYTES} byte limit")

    if audit(payload) is not None:
        # Deliberately NOT reporting the match — it is the secret.
        raise EpisodeInvalid(
            "episode failed the leak audit — it still contains credential-, "
            "token-, tailnet-IP- or username-shaped text. Rebuild it with "
            "scripts/build_replay_library.py so the parser's redaction runs.")

    _check_show(script)

    _dry_run(script)

    return {
        "name": resolved,
        "event_count": len(script["events"]),
        "byte_size": byte_size,
    }
