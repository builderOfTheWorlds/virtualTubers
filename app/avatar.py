#!/usr/bin/env python3
"""
avatar.py
Thin dispatcher: polls the small local JSON state file `agent_state.py`
writes (see docs/VTuber_AI_Dev_Team_Concept.md §13.3), resolves the
current expression + speech bubble, and hands one frame off to a
pluggable AvatarProvider (avatar_providers/) each tick. Polls the state
file on a short timer instead of an inter-process socket.

Rendering behavior itself lives in avatar_providers/*.py — see
avatar_providers/__init__.py for provider selection/fallback.
"""
import json
import os
import sys
import time
import argparse
import textwrap

from message_bus import load_worker_config
from agent_state import resolve_state_path, read_state
from avatar_display import display_width  # re-exported for callers/tests

# Safety net: if the agent dies mid "thinking" (no bubble to time out), don't
# leave the avatar stuck mid-expression forever — settle back to idle.
STALE_AFTER_S = 30

# Fallback poll interval if a provider doesn't set tick_interval_s.
DEFAULT_POLL_INTERVAL_S = 0.5

# Agent -> avatar persona relay file (CONTRACT.md §8, docs/campaign_control.md):
# same file app/agent.py writes and app/replay_pane.py also polls. This pane
# NEVER touches Redis/Kafka directly (docs/duet_replay.md's rule) — it only
# ever polls this local file, same as the agent-state file above.
PERSONA_FILE_ENV = "PERSONA_FILE"
DEFAULT_PERSONA_FILE = "/tmp/persona.json"


def wrap_bubble(text, width):
    """Word-wrap `text` to `width` display columns. Returns a non-empty list of lines."""
    if not text:
        return []
    return textwrap.wrap(text, width=width) or [text]


def resolve_display(state, now, bubble_duration_s, stale_after_s=STALE_AFTER_S):
    """Decide (expression, bubble_text) from the raw state dict.

    - No/unreadable state -> idle, no bubble.
    - A bubble is shown only while fresh (age <= bubble_duration_s); once it
      expires, expressions that only make sense *with* a bubble (speaking,
      frustrated) revert to idle too.
    - A bubble-less expression (e.g. "thinking" during a long LLM call)
      persists until superseded, unless it goes stale (agent likely died).
    """
    if not state:
        return "idle", None

    expression = state.get("expression") or "idle"
    bubble = state.get("bubble")
    age = now - state.get("updated_at", 0)

    if bubble:
        if age > bubble_duration_s:
            bubble = None
            if expression in ("speaking", "frustrated"):
                expression = "idle"
    elif age > stale_after_s:
        expression = "idle"

    return expression, bubble


def _resolve_persona_file():
    return os.environ.get(PERSONA_FILE_ENV) or DEFAULT_PERSONA_FILE


def read_persona_file(path):
    """Best-effort read of the agent -> avatar persona relay file
    (app/agent.py's write_persona_file). Missing/corrupt content returns
    None — the poll loop treats that as "nothing new", never as an error
    worth crashing the pane over (this pane's only job is to stay up)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def persona_identity(persona_doc):
    """A cheap, stable signature for "did the assigned persona change" —
    (campaign, speaker) is exactly what agent.py's tick loop keys the same
    decision on, so every consumer of the relay file agrees on what counts
    as a change. None when there's nothing (yet) to key off of."""
    if not persona_doc:
        return None
    return (persona_doc.get("campaign"), persona_doc.get("speaker"))


def build_avatar_from_persona(persona_doc, fallback_name, fallback_title):
    """Resolve (avatar_config, name, title) to hand to load_provider() from
    a persona relay-file document (CONTRACT.md §8's
    {..., avatar: {name, title, provider, expressions, ...}} shape).
    `persona_doc["name"]`/`["title"]` win over `avatar.name`/`.title` for
    the on-screen label, matching agent.py's own overlay order; either
    falls back to the CURRENT name/title when the persona doesn't specify
    one, so an update never blanks out a label that was already showing.

    Raises ValueError when `persona_doc["avatar"]` isn't a dict — the
    caller (maybe_update_avatar) is the one place responsible for catching
    that and keeping the current face up rather than handing garbage to
    load_provider."""
    avatar_config = persona_doc.get("avatar")
    if not isinstance(avatar_config, dict):
        raise ValueError("persona has no usable 'avatar' config")
    name = persona_doc.get("name") or avatar_config.get("name") or fallback_name
    title = persona_doc.get("title") or avatar_config.get("title") or fallback_title
    return avatar_config, name, title


def maybe_update_avatar(persona_file, current_identity, avatar_config, name, title):
    """One tick's worth of "check the persona relay file, rebuild the
    provider if the assigned persona changed" — factored out of main()'s
    loop so it's directly testable without driving the infinite dispatch
    loop itself.

    Returns (new_provider_or_None, avatar_config, name, title, identity).
    `new_provider_or_None` is None whenever nothing changed OR the update
    attempt failed — in BOTH cases every other returned value is identical
    to what was passed in, so the caller's current face is never disturbed
    (CONTRACT.md §8: "ANY failure keeps the current face — the avatar
    pane's only job is to stay up"). load_provider() itself never raises
    (it falls back to builtin internally on any construction failure — see
    avatar_providers/__init__.py) but this still wraps the whole rebuild in
    a broad except, since a malformed persona_doc can fail before
    load_provider is even called (build_avatar_from_persona's ValueError)."""
    persona_doc = read_persona_file(persona_file)
    identity = persona_identity(persona_doc)
    if identity is None or identity == current_identity:
        return None, avatar_config, name, title, current_identity

    try:
        new_avatar_config, new_name, new_title = build_avatar_from_persona(persona_doc, name, title)
        from avatar_providers import load_provider
        new_provider = load_provider(new_avatar_config, new_name, new_title)
    except Exception as exc:
        print(f"[avatar] persona update failed ({exc!r}) — keeping current face", file=sys.stderr)
        return None, avatar_config, name, title, current_identity

    return new_provider, new_avatar_config, new_name, new_title, identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/worker.yaml")
    args = parser.parse_args()

    config = {}
    if os.path.exists(args.config):
        config = load_worker_config(args.config) or {}
    agent_config = config.get("agent", {})
    avatar_config = config.get("avatar", {})

    name = os.environ.get("AGENT_NAME") or avatar_config.get("name", "WORKER")
    title = os.environ.get("AGENT_TITLE") or avatar_config.get("title", "Agent")
    bubble_duration_s = avatar_config.get("bubble_duration_s", 6)
    bubble_width = avatar_config.get("bubble_width", 32)

    state_path = resolve_state_path(agent_config)
    print(f"[avatar] watching state file={state_path}", file=sys.stderr)

    from avatar_providers import load_provider
    provider = load_provider(avatar_config, name, title)

    persona_file = _resolve_persona_file()
    persona_identity_seen = None

    while True:
        new_provider, avatar_config, name, title, persona_identity_seen = maybe_update_avatar(
            persona_file, persona_identity_seen, avatar_config, name, title)
        if new_provider is not None:
            provider = new_provider
            bubble_duration_s = avatar_config.get("bubble_duration_s", bubble_duration_s)
            bubble_width = avatar_config.get("bubble_width", bubble_width)
            print(f"[avatar] persona updated: name={name} title={title}", file=sys.stderr)

        state = read_state(state_path)
        expression, bubble = resolve_display(state, time.time(), bubble_duration_s)
        bubble_lines = wrap_bubble(bubble, bubble_width) if bubble else None

        provider.render_tick(expression, bubble_lines)
        time.sleep(getattr(provider, "tick_interval_s", DEFAULT_POLL_INTERVAL_S))


if __name__ == "__main__":
    main()
