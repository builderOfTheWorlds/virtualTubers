# avatar.py

## Overview

Thin dispatcher for the ASCII-art avatar pane. It polls the small local
JSON state file `app/agent_state.py` writes, resolves the current
expression + speech bubble text (`resolve_display`, `wrap_bubble`), and
hands one frame off to a pluggable **avatar provider** each tick —
it no longer draws anything itself.

Rendering behavior (the face, the animation, the bubble box) lives in
`app/avatar_providers/*.py`. `avatar.py` owns state-file polling and the
expression/bubble *decision* logic only; a provider owns everything about
*drawing* a frame. See [docs/avatar_providers.md](avatar_providers.md) for
the provider contract, the registry/selection precedence
(`AVATAR_PROVIDER` env > `avatar.provider` config > `builtin`), the safe
fallback behavior, and how to add a new provider. This split replaced the
original single-file version of `avatar.py`, which had a fixed static box
face baked directly into it — that face still exists, unchanged, as
`avatar_providers/builtin.py`, the always-available default/fallback
provider.

## Signature

```python
def wrap_bubble(text: str | None, width: int) -> list[str]

def resolve_display(state: dict | None, now: float, bubble_duration_s: float,
                     stale_after_s: float = STALE_AFTER_S) -> tuple[str, str | None]

def main() -> None
```

`display_width(s: str) -> int` also lives in `app/avatar_display.py` now
(shared by the dispatcher and every provider) but is re-exported from
`avatar.py` for backward-compat imports.

## Parameters

- `text` (str) — bubble text as read from agent state.
- `width` (int) — `avatar.bubble_width` from the worker config.
- `state` (dict | None) — result of `agent_state.read_state`.
- `now` (float) — Unix timestamp (injected for testability instead of
  calling `time.time()` inline).
- `bubble_duration_s` / `stale_after_s` — `avatar.bubble_duration_s` from
  config, and a fixed 30s safety net respectively (see Error Handling).
- `--config` (CLI flag, default `/config/worker.yaml`).

Provider construction reads its own config from `avatar.*` — `avatar.name`
/ `avatar.title` (env vars `AGENT_NAME` / `AGENT_TITLE` win if set),
`avatar.provider`, `avatar.expressions`, `avatar.ascii_avatar.*`, etc. See
[docs/avatar_providers.md](avatar_providers.md) for the full set.

## Return Value

- `resolve_display` — `(expression, bubble_text_or_None)`, the pure decision
  logic behind what gets rendered each poll tick.
- `wrap_bubble` — list of lines, `[]` for empty/`None` input.
- `main` — `None`; side effect is the dispatcher loop running forever,
  calling `provider.render_tick(...)` each tick (never returns).

## Dependencies

- `message_bus.load_worker_config` (reuses the same YAML loader as
  `agent.py`/`tail_bus.py`).
- `agent_state` (`resolve_state_path`, `read_state`).
- `avatar_display` (`display_width`, re-exported; `build_bubble_box` used
  by providers directly).
- `avatar_providers` (`load_provider`) — see
  [docs/avatar_providers.md](avatar_providers.md).
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
- Provider selection/construction failures never reach `main()` — they're
  handled entirely inside `avatar_providers.load_provider`, which always
  returns a working provider (falling back to `builtin`). See
  [docs/avatar_providers.md](avatar_providers.md#error-handling).

## Changelog

- v2.0.0 (2026-07-12) — Split into a thin dispatcher + pluggable
  `avatar_providers/` rendering layer. `render()`/`DEFAULT_EXPRESSIONS`
  moved verbatim to `avatar_providers/builtin.py`; `display_width()`/
  `build_bubble_box()` moved to the shared `avatar_display.py`. `avatar.py`
  itself now only polls state, resolves expression/bubble, and calls
  `provider.render_tick(...)` on the dispatcher's `DEFAULT_POLL_INTERVAL_S`
  or the provider's own `tick_interval_s`. See
  [docs/avatar_providers.md](avatar_providers.md).
- v1.0.0 (2026-07-01) — Replaced the fixed-timer expression-cycling stub
  with a real state-file-driven renderer: reads `avatar.*` from the worker
  config, polls `agent_state.py`'s state file, and shows a word-wrapped
  speech bubble with auto-dismiss.
