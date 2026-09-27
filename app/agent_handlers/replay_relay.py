"""
agent_handlers/replay_relay.py
Any-role replay handlers (split out of app/agent.py): the operator levers
`replay_request` / `replay_stop`, and the duet director/follower relay
types `replay_invite` / `replay_ready` / `replay_cue` / `replay_end`. None
of them calls the LLM; they only write relay files for app/replay_pane.py.
"""
import time

import relay_io
from message_bus import build_message
from agent_state import write_state

from .relay_files import (
    _atomic_write_json,
    _read_json_file,
    _resolve_replay_cue_file,
    _resolve_replay_ready_file,
    _resolve_replay_request_file,
    _resolve_replay_stop_file,
    _write_replay_request,
)


def _is_valid_cast(cast):
    """True when `cast` is a non-empty dict mapping non-empty string speaker
    names to non-empty string worker ids (duet replay contract). Anything
    else — not a dict, empty dict, non-string/blank keys or values — is
    invalid and must be rejected by handle_replay_request rather than
    forwarded to the replay pane."""
    if not isinstance(cast, dict) or not cast:
        return False
    for key, value in cast.items():
        if not isinstance(key, str) or not key.strip():
            return False
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def handle_replay_request(worker_id, agent_config, llm_client, producer, msg,
                          state_path=None, coding_backend=None):
    """Operator lever: queue a "Rerun Theater" episode for this worker's
    replay pane (docs/replay_pane.md). Any role handles it.

    Deliberately NO LLM call and NO episode-name validation here beyond
    non-empty: the agent only writes the request file; replay_pane.py owns
    resolution (basename-only, against the episode store) so a hostile
    payload can only ever name an episode an operator already uploaded.
    Always answers the operator so a bad episode name doesn't just vanish.

    Optional payload.cast (duet replay contract): a speaker -> worker_id
    map. When present it must be a non-empty dict of non-empty strings
    (see _is_valid_cast) — valid casts are forwarded verbatim into the
    request file; an invalid cast is rejected with an operator_reply error
    and NOTHING is written (a half-formed duet must never reach the pane).
    Absent cast leaves solo-request behavior byte-for-byte unchanged.
    """
    payload = msg.get("payload", {})
    episode = payload.get("episode")
    if not episode or not str(episode).strip():
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"error": "replay_request needs payload.episode (episode script name)"},
        ))
        return

    cast = payload.get("cast")
    if cast is not None and not _is_valid_cast(cast):
        print(f"[agent:{worker_id}] rejected replay_request: invalid cast {cast!r}")
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"error": "replay_request payload.cast must be a non-empty dict mapping "
                       "non-empty speaker names to non-empty worker ids"},
        ))
        return

    request = {"episode": str(episode).strip()}
    if payload.get("speed") is not None:
        request["speed"] = payload["speed"]
    if payload.get("worker_name"):
        request["worker_name"] = str(payload["worker_name"])
    # voice/narration are interpreted entirely by replay_pane.py (see
    # docs/operator_commands.md) — the agent just forwards them verbatim.
    if isinstance(payload.get("voice"), bool):
        request["voice"] = payload["voice"]
    if payload.get("narration"):
        request["narration"] = str(payload["narration"])
    if cast is not None:
        # Already validated above — forwarded verbatim, per contract.
        request["cast"] = cast

    try:
        _write_replay_request(request)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to queue replay request: {exc}")
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"error": f"could not queue replay: {exc}"},
        ))
        return

    print(f"[agent:{worker_id}] queued replay episode {request['episode']!r}")
    if state_path:
        write_state(state_path, "happy", action="rerun time!",
                    bubble=f"Time for a rerun: {request['episode']}")
    producer.send(build_message(
        worker_id, "operator", "operator_reply",
        {"narration": f"Queued rerun episode {request['episode']!r} - rolling it in the theater pane."},
    ))


def handle_replay_stop(worker_id, agent_config, llm_client, producer, msg,
                       state_path=None, coding_backend=None):
    """Operator lever: interrupt whatever this worker's replay pane is
    doing right now (docs/replay_pane.md). Any role handles it, no LLM
    call — the counterpart to handle_replay_request.

    Two independent effects, since a request can be in either state when
    the operator wants it stopped:

    1. A still-QUEUED request (written but not yet picked up by the pane's
       poll loop) is cancelled outright — REPLAY_REQUEST_FILE is deleted
       before the pane ever sees it.
    2. A currently PLAYING show is signalled to abort: REPLAY_STOP_FILE is
       written, and the pane's Pacer.should_stop hook (docs/replay.md
       ReplayStopped) picks it up on its very next sleep/typed character —
       sub-second, not just at the next scene boundary — and unwinds
       cleanly (avatar -> idle, "stopped" banner, no crash).

    Both are best-effort against "nothing was actually happening": an idle
    pane just leaves the stop file sitting there until the next request's
    own stale-state cleanup consumes it, so a stop can never bleed into a
    later, unrelated airing. Always answers the operator so a stop sent to
    a genuinely idle worker doesn't look like it vanished.
    """
    request_file = _resolve_replay_request_file()
    cancelled_queued = False
    try:
        # A single unlink, not exists-then-remove: if the pane claims the
        # request in between, it simply isn't queued any more (the stop
        # file below catches it playing) — not a "failed to cancel".
        cancelled_queued = relay_io.remove_file(request_file)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to cancel queued replay request: {exc}")

    try:
        _atomic_write_json(_resolve_replay_stop_file(), {
            "stopped_by": msg.get("from"),
            "stopped_at": time.time(),
        })
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to signal replay stop: {exc}")
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"error": f"could not signal replay stop: {exc}"},
        ))
        return

    if cancelled_queued:
        print(f"[agent:{worker_id}] cancelled queued replay request and signalled stop")
        narration = "Cancelled the queued rerun and stopped whatever's currently playing."
    else:
        print(f"[agent:{worker_id}] signalled replay stop")
        narration = "Stopping the rerun."
    producer.send(build_message(
        worker_id, "operator", "operator_reply", {"narration": narration},
    ))


# ── Duet replay: director/follower relay handlers ──────────────────────────
# These four handle the bus side of the "duet replay" feature (multi-worker
# Rerun Theater airings). Every one of them is any-role, makes NO LLM call,
# sends NOTHING back onto the bus, and only ever relays the inbound payload
# into a small local JSON file for replay_pane.py to poll — so a bad/partial
# write must log and return, never raise out of the tick loop.

def handle_replay_invite(worker_id, agent_config, llm_client, producer, msg,
                         state_path=None, coding_backend=None):
    """Director -> follower: queue this worker's replay pane into "follow"
    mode for a duet airing. Any role handles it.

    Mirrors handle_viewer_joined's "don't clobber a pending request" rule:
    if a request file is already sitting there (an operator queue, another
    pending invite, ...) the invite is dropped — log and move on. The
    director's own replay_ready timeout is what surfaces this as a refusal;
    this handler does not report anything back itself.
    """
    payload = msg.get("payload", {})

    # Fields copied verbatim from the invite payload, plus the follower-mode
    # marker replay_pane.py switches on.
    request = dict(payload)
    request["mode"] = "follow"

    # if_absent: the "already pending?" check and the write are one atomic
    # step (relay_io.atomic_create_json), so there is no window in which a
    # request landing after the check gets clobbered.
    try:
        written = _write_replay_request(request, if_absent=True)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to write follower request for replay_invite: {exc}")
        return
    if written is None:
        print(f"[agent:{worker_id}] dropped replay_invite — a replay request is already pending")
        return

    print(
        f"[agent:{worker_id}] queued as follower for airing {request.get('airing_id')!r} "
        f"episode={request.get('episode')!r}"
    )


def handle_replay_ready(worker_id, agent_config, llm_client, producer, msg,
                        state_path=None, coding_backend=None):
    """Follower -> director: mark a worker ready for a duet airing. Any role
    handles it (the director simply is the worker addressed).

    Union/replace rule: a ready file already holding the SAME airing_id
    gets the sender unioned into its worker list; a different, missing, or
    corrupt file is replaced with a fresh single-sender entry for this
    airing. Sender identity always comes from the message envelope `from`,
    never the payload.
    """
    payload = msg.get("payload", {})
    airing_id = payload.get("airing_id")
    sender = msg.get("from")

    ready_file = _resolve_replay_ready_file()
    existing = _read_json_file(ready_file)
    if isinstance(existing, dict) and existing.get("airing_id") == airing_id:
        workers = list(existing.get("workers") or [])
        if sender is not None and sender not in workers:
            workers.append(sender)
    else:
        workers = [sender] if sender is not None else []

    ready = {"airing_id": airing_id, "workers": workers}
    try:
        _atomic_write_json(ready_file, ready)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to write replay_ready state: {exc}")
        return

    print(f"[agent:{worker_id}] replay_ready from {sender!r} for airing {airing_id!r} (workers={workers})")


def handle_replay_cue(worker_id, agent_config, llm_client, producer, msg,
                      state_path=None, coding_backend=None):
    """Director -> follower: authorize performing scenes up to scene_index
    for a duet airing (cue ratchet — followers may perform any scene at or
    below the latest cue). Any role handles it.

    Overwrite-latest semantics: no history is kept, and the file is written
    even when this worker doesn't currently know about a local show (a
    follower whose pane hasn't started polling yet still needs the freshest
    cue waiting for it).
    """
    payload = msg.get("payload", {})
    cue = {
        "airing_id": payload.get("airing_id"),
        "type": "cue",
        "scene_index": payload.get("scene_index"),
    }
    try:
        _atomic_write_json(_resolve_replay_cue_file(), cue)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to write replay_cue: {exc}")
        return

    print(f"[agent:{worker_id}] cue: airing {cue['airing_id']!r} scene_index={cue['scene_index']}")


def handle_replay_end(worker_id, agent_config, llm_client, producer, msg,
                      state_path=None, coding_backend=None):
    """Director -> follower: end a duet airing (finished / ready_timeout /
    aborted), via the same cue file the ratchet loop polls. Any role
    handles it. Overwrite-latest semantics, same as handle_replay_cue.
    """
    payload = msg.get("payload", {})
    end = {
        "airing_id": payload.get("airing_id"),
        "type": "end",
        "reason": payload.get("reason"),
    }
    try:
        _atomic_write_json(_resolve_replay_cue_file(), end)
    except OSError as exc:
        print(f"[agent:{worker_id}] failed to write replay_end: {exc}")
        return

    print(f"[agent:{worker_id}] end: airing {end['airing_id']!r} reason={end['reason']!r}")
