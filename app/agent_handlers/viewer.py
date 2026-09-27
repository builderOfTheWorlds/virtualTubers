"""
agent_handlers/viewer.py
Any-role `viewer_joined` handler (split out of app/agent.py): queue a
Rerun Theater episode for a newly arrived Twitch viewer, then greet them.
Narration-only — nothing is sent back onto the bus.
"""
import random

import episode_store
from agent_state import write_state

from .common import _complete_with_emotion
from .relay_files import _write_replay_request


def _pick_rerun_episode(payload):
    """Which episode should a viewer-join rerun play? payload.episode wins
    (manual/test injections); otherwise a random pick from the episode
    library in Postgres (docs/episode_store.md) — every arrival gets "a
    rerun", not one hardcoded show.

    Returns None when no episode is available: an empty library, or a store
    that can't be reached. handle_viewer_joined treats None as "skip the
    rerun and just greet them", so a Postgres outage costs the viewer their
    rerun but never their welcome."""
    requested = payload.get("episode")
    if requested and str(requested).strip():
        return str(requested).strip()
    if not episode_store.available():
        return None
    try:
        episodes = sorted(episode_store.list_episodes())
    except Exception as exc:
        print(f"[agent] episode library unreachable, skipping rerun: "
              f"{type(exc).__name__}: {exc}")
        return None
    if not episodes:
        return None
    return random.choice(episodes)


def handle_viewer_joined(worker_id, agent_config, llm_client, producer, msg,
                         state_path=None, coding_backend=None):
    """A viewer just showed up in this worker's Twitch chat — sent by the
    twitch-presence service via message-api (docs/twitch_presence.md). Any
    role handles it, in two steps:

    1. Start a rerun for them: queue a Rerun Theater episode for the replay
       pane (payload.episode override, else a random library pick) — the
       rerun is queued FIRST, before the LLM call, so a slow/dead LLM can
       never keep the show from starting. If a request is already pending
       (file exists), it is left alone: a viewer join must not stomp an
       operator's queued episode, and the pane only holds one request anyway.
    2. Greet them: an LLM-written welcome on the console output and avatar
       speech bubble, mentioning the rerun when one was queued.

    Everything is narration-only BY DESIGN: NOTHING is sent back onto the
    bus — viewer arrivals are outside-world events, not pipeline traffic,
    and a burst of joins must never fan out into a burst of bus messages.
    Failures (no episodes, unwritable request file, LLM down) likewise just
    log: a missed hello or rerun is not worth an error message anywhere.
    """
    payload = msg.get("payload", {})
    username = payload.get("username", "someone")

    episode = _pick_rerun_episode(payload)
    queued = False
    if episode is None:
        print(f"[agent:{worker_id}] no replay episodes available — greeting {username!r} only")
    else:
        request = {"episode": episode}
        # voice/narration ride along verbatim, same as handle_replay_request
        # (useful for manual/test injections; twitch-presence never sets them).
        if isinstance(payload.get("voice"), bool):
            request["voice"] = payload["voice"]
        if payload.get("narration"):
            request["narration"] = str(payload["narration"])
        # if_absent: "already pending?" and the write are one atomic step
        # (relay_io.atomic_create_json) — no check-then-write window.
        try:
            if _write_replay_request(request, if_absent=True) is None:
                print(f"[agent:{worker_id}] a replay request is already pending — "
                      f"greeting {username!r} only")
            else:
                queued = True
                print(f"[agent:{worker_id}] viewer {username!r} arrived — queued rerun {episode!r}")
        except OSError as exc:
            print(f"[agent:{worker_id}] failed to queue viewer-join rerun: {exc}")

    if queued:
        prompt = (
            f"A viewer named '{username}' just started watching your stream, and "
            "you're firing up a rerun of one of your past coding sessions for them. "
            "Welcome them and introduce the rerun in 1-2 sentences, in character, "
            "as if speaking to the stream."
        )
    else:
        prompt = (
            f"A viewer named '{username}' just started watching your stream. "
            "Give them a short, warm welcome in 1-2 sentences, in character, "
            "as if speaking to the stream."
        )

    if state_path:
        write_state(state_path, "thinking", action=f"greeting {username}")

    try:
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed greeting {username!r}: {exc}")
        if state_path:
            write_state(state_path, "idle", action=f"missed greeting {username}")
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "happy", action=f"welcomed {username}", bubble=narration, emotion=emotion)
