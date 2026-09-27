#!/usr/bin/env python3
"""
agent_state.py
Small local JSON state file the agent loop writes and the avatar pane reads,
so the avatar can reflect what the agent is doing (expression + speech
bubble) without an inter-process socket (see
docs/VTuber_AI_Dev_Team_Concept.md §13.3).
"""
import json
import os
import tempfile
import time

DEFAULT_STATE_FILE = "/tmp/agent_state.json"


def resolve_state_path(agent_config=None, env_name="AGENT_STATE_FILE"):
    """Env var > agent_config['state_file'] > DEFAULT_STATE_FILE."""
    return os.environ.get(env_name) or (agent_config or {}).get("state_file") or DEFAULT_STATE_FILE


def write_state(path, expression, action="", bubble=None, emotion=None,
                audio_envelope=None, audio_rate_hz=None, audio_started_at=None):
    """Atomically write the agent's current expression/action/bubble to `path`.

    Written via a same-directory temp file + os.replace so a concurrent
    reader (avatar.py, polling on its own timer) never observes a
    partially-written file.

    `emotion` (one of emotion.EMOTIONS, default "neutral" when omitted) is
    the character's current EMOTIONAL POSE — independent of `expression`,
    which remains the ACTIVITY cue (idle/thinking/speaking/...). A caller
    with no opinion on emotion (most existing call sites) can omit it
    entirely; avatar.py treats a missing/unrecognized value as "neutral"
    (see emotion.normalize_emotion), so every pre-existing write_state call
    keeps rendering a neutral face exactly as before this field existed.

    `audio_envelope`/`audio_rate_hz`/`audio_started_at` are the audio-
    driven mouth animation channel (docs/avatar_emotion_design.md):
    `audio_envelope` is a list of 0..1 floats (audio_envelope.compute_envelope's
    output) sampled at `audio_rate_hz` samples/second, anchored at
    `audio_started_at` (a time.time() timestamp taken the moment playback
    actually started). All three are None when the caller has no real
    audio to report (e.g. agent.py's team workers, which only ever show a
    text bubble) — the avatar pane falls back to a bubble-presence
    heuristic for mouth animation in that case (see avatar.resolve_mouth_open).
    """
    state = {
        "expression": expression,
        "action": action,
        "bubble": bubble,
        "emotion": emotion,
        "audio_envelope": audio_envelope,
        "audio_rate_hz": audio_rate_hz,
        "audio_started_at": audio_started_at,
        "updated_at": time.time(),
    }
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp_path, path)
    except FileNotFoundError:
        # A second write_state call racing on the SAME fixed `{path}.tmp`
        # name can have its rename source vanish out from under it if
        # another writer's os.replace already consumed that exact tmp file
        # first (observed live: agent.py's tick loop crashed outright with
        # "No such file or directory: '/tmp/agent_state.json.tmp' ->
        # '/tmp/agent_state.json'", which killed the roundtable worker's
        # ENTIRE message consumer — no more replay_request handling at all —
        # since startup.sh launches agent.py once with no restart-on-crash
        # supervisor). Retry once with a name unique to this call
        # (tempfile.mkstemp in the SAME directory, so os.replace stays an
        # atomic same-filesystem rename) rather than letting a benign race
        # between two legitimate writers take the whole agent down.
        directory = os.path.dirname(path) or "."
        fd, unique_tmp = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(unique_tmp, path)
        except Exception:
            try:
                os.unlink(unique_tmp)
            except OSError:
                pass
            raise
    return state


def read_state(path):
    """Read the state file. Returns None if missing/unreadable/malformed —
    callers should fall back to an idle display rather than raise."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
