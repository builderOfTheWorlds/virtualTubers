# Avatar Provider Integration — what we did

## Overview

This document is a single narrative record of the 2026-07-12 project to make
the worker avatar swappable: vendoring a third-party animated ASCII avatar
project, building a pluggable provider layer around it, and wiring
configuration all the way from the worker YAML up through `docker-compose.yml`
and `.env` to Portainer's stack env panel. It exists so a reader can see the
whole shape of the change in one place; for API-level detail, see
[docs/avatar_providers.md](avatar_providers.md) (the provider layer reference)
and [docs/avatar.md](avatar.md) (the dispatcher reference).

## Why

Every worker's avatar was a static ASCII box (`app/avatar.py`) that only
swapped an `eyes`/`mouth` string pair per expression — no animation. The goal
was a visibly better avatar without losing the always-safe static face as a
fallback, and without hard-coding a single alternative — the ask was
explicitly for something **configurable, per worker, with no code change**.

## What we did

1. **Evaluated existing ASCII avatar projects** and picked
   [Angelopvtac/ascii-avatar](https://github.com/Angelopvtac/ascii-avatar)
   (MIT) as the best match — it already has a five-state animated face
   (idle/thinking/speaking/listening/error) with per-persona frame sets,
   closely mirroring our own expression model.

2. **Vendored it** into `repos/ascii-avatar/` as a plain snapshot (no
   `.git`), pinned by commit hash in `repos/README.md`. Only its
   rendering/animation code is used — its ZeroMQ event bus, MCP server,
   Claude Code hooks, and TTS/voice engines are deliberately never imported,
   so none of their dependencies (`pyzmq`, `mcp`, `anthropic`, `sounddevice`)
   were added to the worker image. The frame set is forced to `"cyberpunk"`
   specifically because the other frame sets (`musetalk`, `layered2d`) pull
   in Pillow/numpy at import time.

3. **Built a provider abstraction**, `app/avatar_providers/`, following the
   same registry pattern already used for `coding_backend.py` /
   `coding_backends/`:
   - `AvatarProvider` base contract: `__init__(avatar_config, name, title)`,
     `render_tick(expression, bubble_lines)`, `tick_interval_s`.
   - `builtin.py` — the original static face, moved verbatim; always
     available, always the fallback.
   - `ascii_avatar.py` — drives the vendored renderer, mapping our 7
     expressions onto its 5 states (configurable override via
     `avatar.expression_map`).
   - `load_provider()` resolves `AVATAR_PROVIDER` env → worker config
     `avatar.provider` → `"builtin"`, and **never lets a bad provider crash
     the pane** — any unknown name or construction failure (missing repo,
     bad persona, terminal init failure) logs a warning and falls back to
     `builtin`.
   - `app/avatar.py` was rewritten as a thin dispatcher: it still owns
     state-file polling and expression/bubble decision logic
     (`resolve_display`, `wrap_bubble`), but rendering itself is entirely
     delegated to whichever provider `load_provider()` returns.

4. **Wired configuration end-to-end** so the provider is switchable at every
   layer, from "permanent default" down to "flip it right now without
   touching a file":
   - `config/worker.yaml` / `config/workers/*.yaml` — `avatar.provider` (the
     durable, per-worker-role default; every existing role config was given
     an explicit `provider: builtin` for clarity).
   - `docker-compose.yml` — every worker service gets an `AVATAR_PROVIDER`
     environment variable sourced from its own stack env var
     (`CODER_AVATAR_PROVIDER`, `CODER_NATIVE_AVATAR_PROVIDER`,
     `CODER_OPENCODE_AVATAR_PROVIDER`, `CODER_AIDER_AVATAR_PROVIDER`,
     `MANAGER_AVATAR_PROVIDER`, `TESTER_AVATAR_PROVIDER`), defaulting to
     empty — an empty env var is treated as "unset" by `load_provider()`
     (`os.environ.get(...) or ...`), so nothing changes for anyone who
     doesn't set it.
   - `.env` / `.env.example` — the same six variables added, ready to copy
     into Portainer's stack **Environment variables** panel (each is its own
     `name` → `value` pair there, same as every other stack env var this
     project uses).
   - `Dockerfile` — gained `COPY repos/ /repos/` so the vendored package
     actually reaches the running container.

5. **Tested**: 9 new unit tests in `tests/test_avatar_providers.py`
   (registry defaulting, env override, unknown-provider fallback,
   constructor-failure fallback, expression-map behavior) — full suite
   418/418 passing. Verified locally: provider selection, the fallback path
   for both an unknown provider name and a raising constructor, and live
   `render_tick()` calls against the real vendored renderer (Windows console
   needed `PYTHONIOENCODING=utf-8` to print the animation's glyphs — a local
   dev quirk, not a container concern since Linux containers default to
   UTF-8).

## How to actually switch a worker's avatar

Three ways, in order of how "sticky" the change is:

1. **Permanent, per role** — edit that role's `config/workers/<role>.yaml`:
   ```yaml
   avatar:
     provider: ascii_avatar
     ascii_avatar:
       persona: ghost   # ghost | oracle | spectre
   ```
2. **Per deploy, per worker** — set that worker's env var in Portainer's
   stack env panel (e.g. `CODER_AVATAR_PROVIDER=ascii_avatar`) and redeploy.
3. **Quick local test** — export `AVATAR_PROVIDER=ascii_avatar` before
   running `app/avatar.py` directly.

**The very first switch to `ascii_avatar` on a given host needs an image
rebuild** (`./install.sh` or `docker build -t vtube-worker:latest .`) because
`repos/ascii-avatar` only reaches a container via the `Dockerfile`'s `COPY`.
After that image exists, flipping `avatar.provider` / `AVATAR_PROVIDER`
between `builtin` and `ascii_avatar` needs no further rebuild — just a
container restart/redeploy.

## Known follow-ups (not blocking)

- `AsciiAvatarProvider` enters `blessed`'s fullscreen terminal mode and never
  explicitly exits it — harmless in practice since the avatar pane is a
  dedicated tmux pane that lives as long as the container, but worth fixing
  if the provider is ever reused somewhere shorter-lived.
- The vendored package's `listening` state has no default expression mapped
  to it — only reachable via a custom `avatar.expression_map`. Could add a
  natural default (e.g. map nothing, since our agents don't currently model
  "listening" as a distinct expression) or leave as-is.

## Related documents

- [docs/avatar_providers.md](avatar_providers.md) — provider layer API
  reference: contract, registry, selection precedence, expression mapping
  table, how to add a new provider.
- [docs/avatar.md](avatar.md) — the dispatcher (`app/avatar.py`) reference.
- [repos/README.md](../repos/README.md) — vendored-repo pinning convention.

## Changelog

- 2026-07-12 — Initial integration: vendored `ascii-avatar`, built the
  provider layer, wired config/compose/env, tests, docs.
