# agent_state.py

## Overview

A small local JSON state file that `agent.py` writes and `avatar.py` polls,
so the avatar pane can reflect what the agent is doing (expression + speech
bubble) without an inter-process socket — see
`docs/VTuber_AI_Dev_Team_Concept.md` §13.3. This is the seam between the
"think + narrate" loop (`agent.py`) and the previously-stubbed avatar pane.

## Signature

```python
DEFAULT_STATE_FILE = "/tmp/agent_state.json"

def resolve_state_path(agent_config: dict | None = None, env_name: str = "AGENT_STATE_FILE") -> str

def write_state(path: str, expression: str, action: str = "", bubble: str | None = None) -> dict

def read_state(path: str) -> dict | None
```

## Parameters

- `agent_config` (dict) — `config["agent"]`; only `state_file` is read here.
- `env_name` (str) — env var checked before `agent_config`; defaults to
  `AGENT_STATE_FILE`.
- `path` (str) — the resolved state file path (from `resolve_state_path`).
- `expression` (str) — one of the avatar's expression keys (`idle`,
  `thinking`, `speaking`, `frustrated`, etc. — see `config/worker.yaml`
  `avatar.expressions`).
- `action` (str) — free-text description of what triggered this state;
  informational only (not currently rendered).
- `bubble` (str | None) — speech-bubble text, or `None` for no bubble.

## Return Value

- `resolve_state_path` — the resolved path string: env var > config value >
  `DEFAULT_STATE_FILE`.
- `write_state` — the dict that was written (includes `updated_at`, a Unix
  timestamp used by `avatar.py` to decide when a bubble/expression goes
  stale).
- `read_state` — the parsed state dict, or `None` if the file is missing,
  unreadable, or malformed.

## Dependencies

- Python standard library only: `json`, `os`, `time`.

## Usage Examples

```python
from agent_state import resolve_state_path, write_state

state_path = resolve_state_path(agent_config)
write_state(state_path, "thinking", action="working on: fix the login bug")
# ... after the LLM call ...
write_state(state_path, "speaking", action="replied to manager", bubble=narration)
```

```python
# avatar.py's poll loop
from agent_state import read_state
state = read_state(state_path)  # None if agent.py hasn't written yet
```

## Error Handling

- `write_state` writes to `<path>.tmp` then `os.replace`s it into place, so a
  concurrent reader never observes a partially-written file. It does not
  catch `OSError` — an unwritable state path (bad permissions, missing
  parent dir) is a startup-config problem and fails loud, matching
  `message_bus.py`'s convention.
- `read_state` catches `OSError`/`json.JSONDecodeError` and returns `None` —
  the avatar should never crash or freeze because the agent hasn't started
  yet or wrote a torn file; callers fall back to an idle display.

## Changelog

- v1.0.0 (2026-07-01) — Initial implementation, wiring `agent.py`'s
  task-handling lifecycle to `avatar.py`'s expression/speech-bubble display.
