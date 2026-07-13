# app/avatar_providers/

## Overview

The pluggable avatar rendering layer. `app/avatar.py` no longer draws the
avatar itself — it's a thin dispatcher that polls the agent-state file,
decides the current expression/bubble (`resolve_display`, `wrap_bubble`),
and hands one frame off to whichever `AvatarProvider` the worker is
configured to use. `avatar_providers/` owns everything about *drawing* that
frame: the original static ASCII box face (`builtin.py`, always available),
and an adapter (`ascii_avatar.py`) driving the vendored, animated
`repos/ascii-avatar` renderer.

This split exists so a worker can switch its on-screen avatar between very
different rendering backends with a **config change only** — no code
change, no new dispatcher logic — and so a broken/misconfigured backend
degrades to the always-safe builtin face instead of taking the avatar pane
(and the tmux session it lives in) down.

## The contract

Every provider is a subclass of `avatar_providers.base.AvatarProvider`:

```python
class AvatarProvider:
    tick_interval_s: float = 0.5  # dispatcher's sleep between render_tick() calls

    def __init__(self, avatar_config: dict, name: str, title: str): ...

    def render_tick(self, expression: str, bubble_lines: list[str] | None) -> None:
        """Draw one frame. Raises NotImplementedError if not overridden."""
```

- Constructed **once** per avatar pane process and reused for its lifetime.
- `avatar.py` owns state-file polling, deciding the current
  expression/bubble text, and word-wrapping the bubble. A provider owns
  everything about *drawing* a frame, including its own internal animation
  timing/frame cycling between `render_tick()` calls.
- `tick_interval_s` lets a provider set its own natural cadence (the
  builtin static face redraws every 0.5s; the animated `ascii_avatar`
  backend ticks every 0.1s by default so its own frame-rate logic has
  enough resolution to work with).
- `avatar_display.py` (`display_width()`, `build_bubble_box()`) is shared,
  presentation-only code both providers use to draw the bordered speech
  bubble consistently.

## Registry + selection (`avatar_providers/__init__.py`)

```python
PROVIDERS = {
    "builtin": _load_builtin,       # lazy factories -> provider class
    "ascii_avatar": _load_ascii_avatar,
}

def load_provider(avatar_config: dict, name: str, title: str) -> AvatarProvider: ...
```

Selection precedence (first one set wins):

1. Environment variable `AVATAR_PROVIDER`
2. Worker config `avatar.provider`
3. `"builtin"` (default)

Factories are **lazy** — `PROVIDERS[name]` is a zero-arg callable that only
imports the provider module when that name is actually selected, so a
worker running `builtin` never pays the import cost (or needs the
dependencies) of `ascii_avatar`.

### Fallback behavior

The avatar pane runs inside a tmux pane whose only job is to stay up — it
must never crash the container over a provider problem. `load_provider`
therefore falls back to `BuiltinProvider` and logs to stderr (never raises)
when:

- **The configured/`env`-selected name isn't in `PROVIDERS`** — logs
  `unknown avatar provider ... falling back to builtin`.
- **Constructing the selected provider raises for any reason** — bad
  config, missing vendored repo, blessed/terminal init failure, etc. Logs
  `provider ... failed to initialize (...) — falling back to builtin`.

On success, `[avatar] using provider=...` is logged so it's easy to confirm
which backend a running worker actually picked up.

## Built-in expression → state mapping (`ascii_avatar` adapter)

`ascii_avatar.py` maps our 7 expression keys onto the vendored package's 5
`AvatarState` values. Default map (`DEFAULT_EXPRESSION_MAP`), overridable
per-worker via config `avatar.expression_map`:

| Our expression | ascii_avatar `AvatarState` |
|---|---|
| `idle` | `idle` |
| `thinking` | `thinking` |
| `typing` | `thinking` |
| `focused` | `thinking` |
| `speaking` | `speaking` |
| `happy` | `speaking` |
| `frustrated` | `error` |

Note the vendored package also has a `listening` state that nothing in our
7-expression set maps to by default — reachable only via an
`expression_map` override. An expression that maps to an unrecognized
state value falls back to `idle` and logs a warning (`render_tick` catches
`ValueError` from the `AvatarState(...)` lookup).

The `ascii_avatar` adapter also **forces the frame set to `"cyberpunk"`**
regardless of the selected persona's own configured frame set — the other
frame sets (`musetalk`, `layered2d`) pull in Pillow/numpy at import time,
which are intentionally not installed. See the module docstring in
`avatar_providers/ascii_avatar.py` for the full list of vendored subsystems
that are deliberately never imported (event bus, MCP bridge, TTS/voice,
`avatar.main`).

## Adding a NEW avatar provider

Switching *between* the providers that already exist is config-only (see
below). Adding a **new** backend is a three-step, additive change:

1. **Vendor the upstream repo** into its own directory under `repos/<name>/`
   (see `repos/README.md` for the pinning convention `ascii-avatar` already
   follows — commit hash/tag noted, license checked, only the pieces you
   actually use documented as "used" vs. "never imported").
2. **Write one adapter module** in `app/avatar_providers/<name>.py`
   subclassing `AvatarProvider`, implementing `__init__` (resolve config,
   set up whatever the backend needs, **raise on any setup failure** rather
   than leaving the object half-constructed) and `render_tick(expression,
   bubble_lines)`. Follow `ascii_avatar.py`'s pattern: resolve expression
   via a config-overridable map, draw the bubble with
   `avatar_display.build_bubble_box()` for a consistent look across
   providers.
3. **Register it** in `avatar_providers/__init__.py`: add a `_load_<name>()`
   lazy factory and an entry in `PROVIDERS`. That's the only place the
   registry needs to change.

Nothing in `app/avatar.py` or the dispatcher loop needs to change — it only
ever calls `load_provider(...)` and `provider.render_tick(...)`.

## Config examples

Switch a worker to `ascii_avatar` via its config file:

```yaml
# config/workers/coder.yaml
avatar:
  provider: ascii_avatar
  ascii_avatar:
    persona: ghost        # ghost | oracle | spectre (only frame_rate_modifier used)
  # expression_map:
  #   happy: speaking      # optional override of the default map above
```

Or switch it at deploy time with **no config edit** — just set the env var
on that worker's container (e.g. a Portainer stack env var):

```bash
AVATAR_PROVIDER=ascii_avatar
```

Switch back to the always-available static face the same way:

```yaml
avatar:
  provider: builtin
```

```bash
AVATAR_PROVIDER=builtin
```

> **First-time switch to `ascii_avatar` needs an image rebuild + redeploy.**
> The vendored repo only reaches a running container because the
> `Dockerfile` does `COPY repos/ /repos/` at build time — a config-only or
> env-only change takes effect on container restart *after* an image that
> already contains `/repos/ascii-avatar/src` has been built and deployed
> (`docker build -t vtube-worker:latest .` on the host, then redeploy the
> stack in Portainer — see the root README's Deploy section). Once that
> image is running, flipping `avatar.provider` / `AVATAR_PROVIDER` between
> `builtin` and `ascii_avatar` needs no further rebuild.

## Dependencies

- `avatar_providers/base.py` — the `AvatarProvider` contract, no external deps.
- `avatar_providers/builtin.py` — `avatar_display.build_bubble_box`; no
  deps beyond `wcwidth` (already required by the rest of `avatar.py`).
- `avatar_providers/ascii_avatar.py` — `blessed` (declared in
  `requirements.txt`) plus the vendored `repos/ascii-avatar/src/avatar`
  package (`avatar.renderer`, `avatar.animation`, `avatar.state_machine`,
  `avatar.personas` only — see that module's docstring for what's
  deliberately never imported).
- `avatar_providers/__init__.py` — stdlib only (`os`, `sys`).

## Usage Examples

```python
# app/avatar.py's dispatcher loop (simplified)
from avatar_providers import load_provider

provider = load_provider(avatar_config, name, title)
while True:
    ...
    provider.render_tick(expression, bubble_lines)
    time.sleep(getattr(provider, "tick_interval_s", DEFAULT_POLL_INTERVAL_S))
```

```python
# Constructing a provider directly (e.g. in a test)
from avatar_providers.builtin import BuiltinProvider

provider = BuiltinProvider({"expressions": {"idle": {"eyes": "^  ^", "mouth": "-----"}}},
                            "KODI-7", "Software Engineer")
provider.render_tick("idle", None)
```

## Error Handling

- Unknown `avatar.provider` / `AVATAR_PROVIDER` name → `BuiltinProvider`,
  logged, never raises.
- Any exception during the selected provider's `__init__` (including
  `AsciiAvatarProviderError`, raised by `ascii_avatar.py` for missing
  vendored repo / import failure / terminal init failure) →
  `BuiltinProvider`, logged, never raises.
- Once constructed, a provider is trusted to keep running — `render_tick`
  exceptions are **not** caught by the registry (that's `avatar.py`'s
  dispatcher loop territory, unchanged from before this layer existed).
- `ascii_avatar.py`'s `render_tick` catches an expression that maps to an
  unrecognized `AvatarState` value (bad `expression_map` override) and
  falls back to `idle` for that tick, logging a warning rather than
  crashing the pane.

## Changelog

- v1.0.0 (2026-07-12) — Initial pluggable provider layer: registry +
  env/config/default selection, safe fallback to `BuiltinProvider` on any
  unknown name or construction failure, `ascii_avatar` adapter vendoring
  `repos/ascii-avatar`'s animation/rendering stack (cyberpunk frame set
  only) mapped onto our 7 expressions.
