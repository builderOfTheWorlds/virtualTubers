# primitives.py — Campaign Platform Rendering Engine

## Overview

`app/primitives.py` is the engine behind the campaign platform's declarative
episode schema (`docs/campaign_platform_build.md`). An episode scene carries
zero or more `render[]` entries — `{primitive, payload, target}` — and this
module is what turns one of those entries into paced, colorized terminal
output, and (before anything airs) estimates how long it will take on screen.

It exists to split "what appears" (a scene's `render[]`, owned by whichever
generator wrote the episode) from "how it's drawn" (a **primitive**, a named
recipe over a handful of **behaviors**) from "what a behavior physically
does" (`type`/`print`/`diff`/`image`/`pause`, the only part that lives in
code). Three layers, each owned by a different party:

| Layer | Lives in | Who adds to it |
|---|---|---|
| **Behaviors** (`BEHAVIORS`) | this module | rarely — platform devs |
| **Primitives** (named recipes) | `config/primitives.yaml` + `config/campaigns/<c>/primitives.yaml` | anyone, per campaign |
| **Scenes** (`kind` + `render[]`) | the episode file | generators, per episode |

Adding a primitive — a new combination of existing behaviors with its own
styling, timing, and avatar hookup — is a YAML edit, not a code change (see
"Worked example" below). Adding a **behavior** (a genuinely new rendering
mechanism, e.g. a video pane) is the one thing that needs code, and is meant
to stay rare.

This module never imports `replay.py` — `RenderContext` is a plain
structural facade `replay.py` constructs and hands in, so `primitives.py` has
no dependency on `Pacer`/`Palette`/`agent_state` beyond the small interface
`RenderContext` describes. It does import `build_layout.deep_merge` directly
(rather than re-implementing it), because the two-layer primitive merge is
deliberately the same merge `build_layout.py` already uses for panel/layout
resolution (`docs/build_layout.md`).

### Layered config, in one picture

```
config/primitives.yaml                     ← shared, presentation-neutral
config/campaigns/<campaign>/primitives.yaml ← campaign additions + overrides
                       │
                       ▼ deep_merge (build_layout semantics: dicts recurse,
                         scalars/lists fully replace) + `extends` resolution
                       │
                       ▼
              one flat {name: resolved_recipe} table  (load_primitives)
```

A campaign's file can, without any code change:

- **Add** a brand-new primitive name (no collision with the shared layer).
- **Override a scalar field** of a shared primitive by giving a partial dict
  with no `behaviors` key at all (e.g. just `{target: notes}`) — ordinary
  `deep_merge` leaves the shared `behaviors` list untouched.
- **Redefine a shared primitive outright** by giving a full `behaviors` list
  under the same name with no `extends` — allowed, but logged at WARNING
  (silent overrides are confusing).
- **Specialize via `extends`** — resolve the named parent first (recursively;
  a broken or cyclic chain raises `PrimitiveError`), then deep-merge every
  field except `behaviors`, which merges **positionally**: child
  `behaviors[i]` deep-merges onto parent `behaviors[i]`; extra child entries
  append past the parent's length; a shorter child leaves the parent's tail
  intact. `extends` naming the **same** primitive name (e.g. a campaign's own
  `type_text` writing `extends: type_text`) is the intentional way to
  specialize a shared primitive without triggering the "redefines without
  extends" warning — it resolves against the shared layer's own version, not
  against itself.

  This positional scheme is unambiguous for the single-behavior
  specializations shipped today (`display_map` extends `show_image` just to
  change `hold_s`; `scene_heading` extends `type_text` just to change
  `rate_cps`/`style`). The design doc flags it as an open question for
  multi-behavior recipes: extending only behaviors[2] of a five-behavior
  parent means writing placeholder entries for 0 and 1, since there is no
  named-slot addressing. Nothing shipped today needs that; if it comes up,
  named behavior slots would be the natural follow-up.

## Signature

```python
BEHAVIORS: set[str] = {"type", "print", "diff", "image", "pause"}

class PrimitiveError(Exception): ...

class RenderContext:
    def __init__(self, write, pacer, palette, avatar, max_output_lines=24): ...

def load_primitives(campaign: str, *, base_path=None, campaigns_dir=None) -> dict[str, dict]: ...
def resolve_recipe(primitives: dict, name: str) -> dict: ...
def estimate_entry_seconds(entry: dict, primitives: dict, *, max_output_lines: int = 24) -> float: ...
def estimate_scene_seconds(scene: dict, primitives: dict, *, max_output_lines: int = 24, speed: float = 1.0) -> float: ...
def render_summary(scene: dict, primitives: dict) -> str: ...
def perform_entry(entry: dict, primitives: dict, ctx: RenderContext) -> None: ...
```

## Parameters

### `load_primitives(campaign, *, base_path=None, campaigns_dir=None)`

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| `campaign` | str | yes | — | Campaign name, e.g. `"coder"`, `"dnd"`. |
| `base_path` | path-like | no | `/config/primitives.yaml` → `config/primitives.yaml` | The shared layer. In-container path preferred, repo-relative fallback (mirrors `build_layout._default_dir`). |
| `campaigns_dir` | path-like | no | `/config/campaigns` → `config/campaigns` | Parent of every campaign's own `<campaign>/primitives.yaml`. |

### `resolve_recipe(primitives, name)`

| Name | Type | Required | Notes |
|---|---|---|---|
| `primitives` | dict | yes | A table returned by `load_primitives`. |
| `name` | str | yes | Primitive name, e.g. `"show_command"`. |

### `estimate_entry_seconds(entry, primitives, *, max_output_lines=24)`

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| `entry` | dict | yes | — | One `render[]` element: `{primitive, payload, target?}`. |
| `primitives` | dict | yes | — | A `load_primitives` table. |
| `max_output_lines` | int | no | `24` | Truncation cap for behaviors that don't set their own `max_lines`. |

### `estimate_scene_seconds(scene, primitives, *, max_output_lines=24, speed=1.0)`

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| `scene` | dict | yes | — | A normalized episode scene (`speaker, kind, text, fallback, mode, render`). |
| `primitives` | dict | yes | — | A `load_primitives` table. |
| `max_output_lines` | int | no | `24` | Passed through to `estimate_entry_seconds`. |
| `speed` | float | no | `1.0` | Show-wide playback speed; total is divided by `max(speed, 0.01)`. |

### `render_summary(scene, primitives)`

| Name | Type | Required | Notes |
|---|---|---|---|
| `scene` | dict | yes | A normalized episode scene. |
| `primitives` | dict | yes | Present for signature symmetry with the estimator functions; `render_summary` does not currently need to resolve a recipe to describe an entry (it summarizes the entry's own `primitive` name and payload, not its resolved behaviors). |

### `perform_entry(entry, primitives, ctx)`

| Name | Type | Required | Notes |
|---|---|---|---|
| `entry` | dict | yes | One `render[]` element. |
| `primitives` | dict | yes | A `load_primitives` table. |
| `ctx` | `RenderContext` | yes | Built by the caller (`replay.py`'s `Performer._perform_render`, not shown here). |

### `RenderContext(write, pacer, palette, avatar, max_output_lines=24)`

| Name | Type | Notes |
|---|---|---|
| `write` | `Callable[[str], None]` with keyword `target: str \| None` | Routes text to a sink; an unresolvable target is the caller's problem (falls back to a default sink there), not this module's. |
| `pacer` | duck-typed | Needs `.sleep(seconds)`, `.type_out(write, text, cps)`, `.check_stop()` — `replay.Pacer`'s shape. |
| `palette` | duck-typed | Needs `.reset/.dim/.bold/.cyan/.green/.yellow/.red/.magenta` — `replay.Palette`'s shape. |
| `avatar` | `Callable[[str], None]` with keywords `action: str = "", bubble: str \| None = None` | Must never raise. |
| `max_output_lines` | int | Default truncation cap. |

## Return Value

- `load_primitives` → `dict[str, dict]`, every value a **fully resolved**
  recipe (no lingering `extends` key, `behaviors` already positionally
  merged).
- `resolve_recipe` → one recipe dict, or raises `PrimitiveError`.
- `estimate_entry_seconds` / `estimate_scene_seconds` → `float` seconds,
  never negative, `0.0` for a missing field / empty `render[]`.
- `render_summary` → `str`, possibly empty (`""`), never raises.
- `perform_entry` → `None` (side effect only: writes to `ctx.write`, sleeps
  via `ctx.pacer`, updates `ctx.avatar`).

## Dependencies

- Standard library: `logging`, `re`, `pathlib`.
- Third-party: `PyYAML` (already a hard dependency of the worker image).
- `build_layout.deep_merge` — imported, not re-implemented (see
  `docs/build_layout.md`).
- **Pillow (`PIL`) is a soft import.** `from PIL import Image` is wrapped in
  `try/except ImportError` at module load; `Image` is `None` when Pillow
  isn't installed, and the `image` behavior falls back to a framed
  placeholder box naming the asset. Pillow is **not** added to
  `requirements.txt` by this change — it stays fully optional, matching the
  existing soft-import pattern in `app/avatar_providers/ascii_avatar.py` for
  the same library.
- Consumed by `app/replay.py`'s `Performer._perform_render` (constructs
  `RenderContext` and calls `perform_entry`/`estimate_scene_seconds`),
  `app/revoice.py`'s rewritten `scene_visual_seconds` (delegates to
  `estimate_scene_seconds`), and `app/episode_schema.py`'s validator
  (`unknown_primitive`, `max_scene_seconds` rules).

## Usage Examples

### Estimate a scene's screen time before airing

```python
from primitives import load_primitives, estimate_scene_seconds

primitives = load_primitives("coder")
scene = {
    "speaker": "coder", "kind": "coder_work", "text": "...", "fallback": "...",
    "mode": "sequence",
    "render": [
        {"primitive": "show_command",
         "payload": {"command": "pytest -x", "output": "1 failed"}},
        {"primitive": "show_diff",
         "payload": {"file": "app/agent.py",
                     "hunks": ["- i = 0", "+ i = 0  # tick"]}},
    ],
}
seconds = estimate_scene_seconds(scene, primitives, speed=1.0)
```

### Perform one entry against a real Performer's context

```python
from primitives import RenderContext, perform_entry

ctx = RenderContext(
    write=lambda text, target=None: sinks[target or "theater"].write(text),
    pacer=pacer,       # a replay.Pacer instance
    palette=palette,   # a replay.Palette instance
    avatar=lambda expression, action="", bubble=None: write_state(state_path, expression, action, bubble),
    max_output_lines=24,
)
perform_entry({"primitive": "show_command",
              "payload": {"command": "git status"}}, primitives, ctx)
```

### Worked example — adding a primitive with no code change

Say a campaign wants a `roll_dice` primitive: a short colored line naming the
roll, held for a beat. Nothing here needs a new behavior — `print` (a literal
line) and `pause` (the hold) already exist:

```yaml
# config/campaigns/dnd/primitives.yaml
roll_dice:
  target: theater
  avatar: {expression: focused, action: "rolling {payload.die}"}
  behaviors:
    - behavior: print
      text: "🎲 rolling {payload.die}..."
      style: {fg: yellow, bold: true}
    - behavior: pause
      seconds: 1.2
```

A scene can use it immediately:

```json
{"primitive": "roll_dice", "payload": {"die": "1d20"}}
```

No import, no new class, no redeploy of `app/primitives.py` — the generator
and the platform both pick it up the next time `load_primitives("dnd")` runs.

## Error Handling

- `load_primitives` raises `PrimitiveError` when the shared base file is
  missing or malformed (there is no sensible default for the shared layer).
  A missing **campaign** file is fine — shared-only campaign, no error.
- `load_primitives` logs a WARNING (never raises) when a campaign primitive
  shares a name with a shared one and has no `extends` — the override still
  takes effect.
- `resolve_recipe` raises `PrimitiveError` for an unknown primitive name.
  `estimate_entry_seconds`/`perform_entry` do not catch this — by the time a
  real episode reaches them it has already passed `episode_schema.py`'s
  `unknown_primitive: reject` validation at ingest, so an unknown name here
  means a real bug, not a content problem, and should surface loudly.
- `extends` resolution raises `PrimitiveError` on an unknown parent name or a
  cycle (`A extends B extends A`), with the full chain in the message.
- A missing `field` in a behavior renders nothing and costs `0.0` seconds —
  never an error (episodes may legitimately omit optional payload fields,
  e.g. a `Bash` call with no `output`).
- `perform_entry`'s `avatar` call is wrapped in `try/except Exception` (belt
  and braces on top of `RenderContext.avatar`'s own "never raises" contract)
  so a misbehaving avatar hook can never take a scene down.
- The `image` behavior never raises on a bad/missing asset: Pillow decode
  failures fall back to the placeholder box; a missing `field` renders
  nothing but still holds for `hold_s`.
- **Known limit**: the `image` behavior does not itself perform campaign
  asset-directory containment or existence checks — `RenderContext` has no
  `assets_dir` in its frozen interface, so `payload.image` is expected to
  already be a path the process can open by the time `perform_entry` runs.
  `episode_schema.py`'s `assets.must_exist`/`basename_only` rules are what
  actually enforce containment and existence, at ingest. This module treats
  the resolved value's basename-only for **display** purposes (the
  placeholder box never echoes path components), but that is not a
  substitute for ingest-time validation.

## Changelog

- **v1.0.0** (2026-07-26): Initial version. `BEHAVIORS = {type, print, diff,
  image, pause}`; two-layer config merge via `build_layout.deep_merge`;
  `extends` resolution with positional behavior merge and cycle detection;
  `estimate_entry_seconds`/`estimate_scene_seconds` (sum under `sequence`,
  max under `parallel`); `render_summary`; `perform_entry` +
  `RenderContext`. Ships `config/primitives.yaml` (shared), `config/campaigns/
  coder/primitives.yaml` (reproduces the pre-campaign-platform coder show's
  glyphs/colors/rates/avatar-expressions/truncation exactly — see
  `tests/test_primitives.py`'s fidelity-table test), `config/campaigns/coder/
  narration.yaml`, and `config/campaigns/dnd/{primitives,narration}.yaml` as
  the second working campaign proving `extends` generalizes.
