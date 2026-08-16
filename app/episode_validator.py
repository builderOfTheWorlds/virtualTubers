"""
episode_validator.py
The gate every episode passes before it enters the library
(docs/episode_validator.md). message-api's POST /replays runs this on an
uploaded episode script and refuses to store anything that fails.

Episodes used to reach the workers by being copied onto the deploy host,
which meant nothing between "a file appeared in replays/" and "it plays on
a live Twitch stream" ever checked the file. Four stages now do:

  1. shape       — the canonical key set session_log_parser.parse_session
                   produces, and per-event-type required fields.
  2. name        — basename-only, character-restricted. Preserves the
                   traversal-safety property replay_pane.resolve_episode
                   used to get for free from being a filesystem lookup.
  3. leak audit  — session_log_parser.audit, the SAME strict regex
                   scripts/build_replay_library.py applies locally. The
                   server, not the dev box, is now the last line of defense.
  4. dry run     — actually render the whole episode through replay.Performer
                   into a throwaway buffer with pacing disabled, and group it
                   with revoice.plan_scenes. This is the "won't have issues
                   replaying it" check: an episode that crashes the renderer
                   is rejected here rather than on air.

SECURITY: a leak-audit failure must never echo the matched text — it is by
construction the secret. EpisodeInvalid carries the rule that fired and
where, never the match itself.
"""
import io
import json
import re

from session_log_parser import audit

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

    _dry_run(script)

    return {
        "name": resolved,
        "event_count": len(script["events"]),
        "byte_size": byte_size,
    }
