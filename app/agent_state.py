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
import time

DEFAULT_STATE_FILE = "/tmp/agent_state.json"


def resolve_state_path(agent_config=None, env_name="AGENT_STATE_FILE"):
    """Env var > agent_config['state_file'] > DEFAULT_STATE_FILE."""
    return os.environ.get(env_name) or (agent_config or {}).get("state_file") or DEFAULT_STATE_FILE


def write_state(path, expression, action="", bubble=None):
    """Atomically write the agent's current expression/action/bubble to `path`.

    Written via a same-directory temp file + os.replace so a concurrent
    reader (avatar.py, polling on its own timer) never observes a
    partially-written file.
    """
    state = {
        "expression": expression,
        "action": action,
        "bubble": bubble,
        "updated_at": time.time(),
    }
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp_path, path)
    return state


def read_state(path):
    """Read the state file. Returns None if missing/unreadable/malformed —
    callers should fall back to an idle display rather than raise."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
