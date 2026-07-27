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

**Runtime persona assignment (campaign_platform_contract.md §8, docs/campaign_control.md).**
Before this, avatar identity (name, title, expression glyphs, provider
choice) was 100% fixed at container boot — nothing about it was ever
re-read. `main()`'s loop now also polls the agent → pane persona relay
file (`/tmp/persona.json`, env `PERSONA_FILE`, written by `app/agent.py`'s
`write_persona_file`) every tick via `maybe_update_avatar`: when the
resolved `(campaign, speaker)` identity changes, it rebuilds the provider
from the persona's own `avatar:` block (same shape as a worker config's
`avatar:` section) via `avatar_providers.load_provider`. **ANY failure
keeps the current face** — a missing/malformed persona doc, or an
`avatar:` block that isn't usable, never touches the currently-running
provider/name/title at all; this pane's only job is to stay up. This pane
NEVER touches Redis or Kafka directly (docs/duet_replay.md's rule) — it
only ever polls this local file, exactly like the agent-state file above.

## Signature

```python
def wrap_bubble(text: str | None, width: int) -> list[str]

def resolve_display(state: dict | None, now: float, bubble_duration_s: float,
                     stale_after_s: float = STALE_AFTER_S) -> tuple[str, str | None]

def main() -> None
```

Persona relay-file polling (campaign_platform_contract.md §8):

```python
def read_persona_file(path: str) -> dict | None
def persona_identity(persona_doc: dict | None) -> tuple | None
def build_avatar_from_persona(persona_doc: dict, fallback_name: str, fallback_title: str) -> tuple
def maybe_update_avatar(persona_file: str, current_identity, avatar_config: dict,
                        name: str, title: str) -> tuple
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

- `persona_file` (str) — the relay file path (`PERSONA_FILE` env, default
  `/tmp/persona.json`); same file `app/agent.py` writes and
  `app/replay_pane.py` also polls.
- `current_identity` (tuple | None) — the `(campaign, speaker)` pair last
  successfully applied; `maybe_update_avatar` compares against this to
  decide whether to rebuild.
- `persona_doc` (dict) — campaign_platform_contract.md §8's relay-file shape: the persona
  fields (`name`/`title`/`avatar`/...) plus `campaign`/`speaker`/
  `updated_at`. `build_avatar_from_persona` reads `persona_doc["avatar"]`
  as the avatar config to hand to `load_provider`, and
  `persona_doc["name"]`/`["title"]` (falling back to `avatar.name`/
  `.title`, then the CURRENT name/title) for the on-screen label.

## Return Value

- `resolve_display` — `(expression, bubble_text_or_None)`, the pure decision
  logic behind what gets rendered each poll tick.
- `wrap_bubble` — list of lines, `[]` for empty/`None` input.
- `main` — `None`; side effect is the dispatcher loop running forever,
  calling `provider.render_tick(...)` each tick (never returns).
- `read_persona_file` — parsed dict, or `None` for a missing/corrupt/
  non-object relay file.
- `persona_identity` — `(campaign, speaker)`, or `None` for a falsy doc —
  the exact key `app/agent.py`'s tick loop and `app/replay_pane.py`'s idle
  loop also use, so every consumer agrees on what counts as "the assigned
  persona changed".
- `build_avatar_from_persona` — `(avatar_config, name, title)` to hand to
  `load_provider`; raises `ValueError` when `persona_doc["avatar"]` isn't a
  usable dict.
- `maybe_update_avatar` — `(new_provider_or_None, avatar_config, name,
  title, identity)`. `new_provider_or_None` is `None` whenever nothing
  changed OR the rebuild attempt failed — in BOTH cases every other
  returned value is identical to what was passed in, so the caller's
  current face is never disturbed.

## Dependencies

- `message_bus.load_worker_config` (reuses the same YAML loader as
  `agent.py`/`tail_bus.py`).
- `agent_state` (`resolve_state_path`, `read_state`).
- `avatar_display` (`display_width`, re-exported; `build_bubble_box` used
  by providers directly).
- `avatar_providers` (`load_provider`) — see
  [docs/avatar_providers.md](avatar_providers.md).
- Python standard library: `os`, `sys`, `time`, `argparse`, `textwrap`,
  `json` (persona relay-file parsing).

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
- **Persona updates never crash the pane or blank the face
  (campaign_platform_contract.md §8).** A missing/corrupt/non-object relay file —
  `read_persona_file` returns `None` — is treated as "nothing new yet".
  A persona doc with no usable `avatar` dict raises inside
  `build_avatar_from_persona`; `maybe_update_avatar` catches this (and any
  other exception from the rebuild attempt, though `load_provider` itself
  is documented to never raise) and returns the CURRENT
  provider/config/name/title unchanged, logging
  `[avatar] persona update failed (...) — keeping current face` to stderr.
  The internal "last applied identity" is only advanced on success, so a
  later valid persona still triggers a rebuild rather than being silently
  skipped forever.

## Changelog

- v2.1.0 (2026-07-26, campaign_platform_contract.md §8) — Runtime persona assignment:
  `main()`'s loop polls `/tmp/persona.json` (env `PERSONA_FILE`) every
  tick via the new `maybe_update_avatar`; on a `(campaign, speaker)`
  change, rebuilds the provider from the persona's own `avatar:` block via
  `avatar_providers.load_provider`. ANY failure keeps the current face.
  See docs/campaign_control.md.
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
