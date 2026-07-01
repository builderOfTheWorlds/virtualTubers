# avatar.py

## Overview

Renders the ASCII-art avatar pane: a face (eyes + mouth per expression) and
an optional word-wrapped speech bubble, redrawn on a short poll timer as
`app/agent_state.py`'s state file changes. Replaces the earlier stub that
just auto-cycled through expressions on a fixed timer with no awareness of
what the agent was actually doing — see `docs/VTuber_AI_Dev_Team_Concept.md`
§13.3.

## Signature

```python
def display_width(s: str) -> int

def wrap_bubble(text: str | None, width: int) -> list[str]

def resolve_display(state: dict | None, now: float, bubble_duration_s: float,
                     stale_after_s: float = STALE_AFTER_S) -> tuple[str, str | None]

def render(name: str, title: str, expression: str, eyes: str, mouth: str,
           bubble_lines: list[str] | None = None) -> None

def main() -> None
```

## Parameters

- `s` / `text` (str) — terminal-bound text; `display_width` uses `wcwidth`
  (falls back to `len()`) so wide/zero-width Unicode doesn't misalign the
  speech-bubble box.
- `width` (int) — `avatar.bubble_width` from the worker config.
- `state` (dict | None) — result of `agent_state.read_state`.
- `now` (float) — Unix timestamp (injected for testability instead of
  calling `time.time()` inline).
- `bubble_duration_s` / `stale_after_s` — `avatar.bubble_duration_s` from
  config, and a fixed 30s safety net respectively (see Error Handling).
- `name` / `title` — `avatar.name` / `avatar.title` from config (env vars
  `AGENT_NAME` / `AGENT_TITLE` win if set).
- `expression` / `eyes` / `mouth` — the resolved expression key and its
  glyphs from `avatar.expressions` (or `DEFAULT_EXPRESSIONS` if the config
  omits that key).
- `--config` (CLI flag, default `/config/worker.yaml`).

## Return Value

- `resolve_display` — `(expression, bubble_text_or_None)`, the pure decision
  logic behind what gets rendered each poll tick.
- `wrap_bubble` — list of lines, `[]` for empty/`None` input.
- `render` / `main` — `None`; side effect is a terminal redraw (`main` never
  returns).

## Dependencies

- `message_bus.load_worker_config` (reuses the same YAML loader as
  `agent.py`/`tail_bus.py`).
- `agent_state` (`resolve_state_path`, `read_state`).
- `wcwidth` (optional at runtime; declared in `requirements.txt`).
- Python standard library: `os`, `sys`, `time`, `argparse`, `textwrap`.

## Usage Examples

```bash
python3 app/avatar.py --config config/workers/coder.yaml
```

```python
# Pure decision logic, independent of the terminal — easy to unit test:
from avatar import resolve_display
expression, bubble = resolve_display(
    {"expression": "speaking", "bubble": "Fixing the bug now.", "updated_at": 1000.0},
    now=1002.0, bubble_duration_s=6,
)
# -> ("speaking", "Fixing the bug now.")
```

## Error Handling

- No state file yet (agent hasn't started) or a torn/malformed read ->
  `read_state` returns `None` -> `resolve_display` shows `idle` with no
  bubble; the pane never crashes or blocks waiting on the agent.
- A bubble auto-dismisses after `bubble_duration_s`; expressions that only
  make sense alongside a bubble (`speaking`, `frustrated`) revert to `idle`
  at the same time. A bubble-less expression (e.g. `thinking` during a long
  LLM call) persists past `bubble_duration_s` since there's no bubble timer
  to key off — but if the agent dies mid-state, `STALE_AFTER_S` (30s) forces
  a fallback to `idle` so the avatar doesn't stay stuck "thinking" for the
  rest of the stream.
- Missing `avatar.expressions` entries fall back to `DEFAULT_EXPRESSIONS`.

## Changelog

- v1.0.0 (2026-07-01) — Replaced the fixed-timer expression-cycling stub
  with a real state-file-driven renderer: reads `avatar.*` from the worker
  config, polls `agent_state.py`'s state file, and shows a word-wrapped
  speech bubble with auto-dismiss.
