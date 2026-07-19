# Layout System — Config-Driven Modular Tmux Panels

## Overview

Each worker container renders its live terminal as a **tmux** session split into
panes (file tree, avatar, editor, message-bus feed, system monitor). That layout
used to be **hardcoded** in `startup.sh` — a fixed sequence of `split-window` +
`send-keys` calls. Changing the arrangement, resizing a pane, or turning one off
meant editing the shell script and rebuilding the image.

The layout system replaces that with **declarative config**. `startup.sh` now
runs a single line:

```bash
eval "$(python3 /app/build_layout.py --config "$CONFIG_PATH")"
```

`app/build_layout.py` reads config, resolves each pane, and emits the exact tmux
command sequence to STDOUT for the shell to `eval`. Reordering, resizing,
retitling, recoloring, or disabling a pane is now a **config-only** change — no
`startup.sh` edit, no image rebuild. That is the prerequisite for the planned
Kubernetes ConfigMap-driven deployment (see [Kubernetes ConfigMap mapping](#kubernetes-configmap-mapping)).

> For the module-level API (function signatures, CLI flags, error handling), see
> **[docs/build_layout.md](build_layout.md)**. This document covers the *system*:
> the config model, resolution order, geometry, and how to operate it.

## The layered config model

Configuration is split into three layers, from most reusable to most specific:

```
config/
  panels/                 # LAYER 1 — reusable panel TYPE defaults (one file each)
    kafka_feed.yaml
    avatar.yaml
    filetree.yaml
    editor.yaml
    htop.yaml
  layouts/                # LAYER 2 — composition presets: place & size panels per role
    coder.yaml
    tester.yaml
    manager.yaml
  worker.yaml             # LAYER 3 — picks a preset; may override per-pane
  workers/*.yaml          #           per-role worker configs (mounted as /config/worker.yaml)
```

- **Panels** (`config/panels/<use>.yaml`) define a panel *type* once: its default
  `title`, `border_color`, and `command`. `kafka_feed` also carries a rich
  `content:` block. These are role-agnostic and shared by every layout.
- **Layouts** (`config/layouts/<preset>.yaml`) are *composition presets*. A preset
  lists which panels to place, in what order, how to split and size them, and any
  per-instance overrides. This is where geometry lives.
- **Worker config** (`config/worker.yaml`, `config/workers/*.yaml`) selects a
  preset (`layout.preset`) and may override individual panes (`layout.panes.<id>`).

See [docs/panels.md](panels.md) for the full catalog of the five panel types and
their fields.

## Resolution / merge order

Each pane is resolved by merging these layers — **later wins**:

```
panel-type default (config/panels/<use>.yaml)
  → layout placement + overrides (config/layouts/<preset>.yaml, incl. the `with:` block)
  → worker-config per-pane override (worker.yaml → layout.panes.<id>)
  → env vars (LAYOUT_PRESET selects the preset)
```

Nested dicts are deep-merged; scalars and lists are replaced wholesale. So a
worker override of `content.filters.hide_types` replaces just that list, leaving
the rest of the panel-type `content:` intact.

Preset selection itself:

- `layout: { preset: coder }` in the worker config, **or** the shorthand
  `layout: coder`.
- The `LAYOUT_PRESET` env var overrides the file value (matches the project-wide
  "env overrides file" convention).
- Falls back to `coder` if nothing is set.

## Geometry — how `size` maps to tmux

Panes are laid out in **list order** within a preset:

- The **first** pane is the **base pane** — the whole screen created by
  `tmux new-session`. Its `size` is informational (no split is emitted).
- Every **subsequent** pane is created by splitting a **target** pane, named by
  the `use`/`id` of an already-placed pane (`target:`); when omitted, it defaults
  to the base pane.
- A pane's **`size` is passed verbatim** as `tmux split-window -p <size>`. Because
  tmux `-p` sizes the **newly-created** pane, `size` is the new pane's percentage
  of the pane it splits from. `split: h` splits left/right; `split: v` splits
  top/bottom.

tmux numbers panes `0..N` in creation order, which equals list order, so
`id → pane index` is deterministic and stable for titles, colors, and `send-keys`.

Worked geometry for the `coder` preset (reproduces the original hardcoded layout
exactly):

| order | pane | split | target | `-p` | result |
|---|---|---|---|---|---|
| 0 | filetree | (base) | — | — | left column, 25% wide |
| 1 | editor | h | filetree | 75 | right column, 75% wide |
| 2 | avatar | v | filetree | 60 | bottom-left, 60% tall |
| 3 | kafka_feed | v | editor | 30 | bottom-right, 30% tall |
| 4 | htop | v | filetree | 15 | thin strip under filetree, 15% |

## Universal per-pane knobs

Any pane placement in a layout preset (and any worker `layout.panes.<id>`
override) accepts these keys:

| Knob | Purpose |
|---|---|
| `use` | Which panel type to load (`config/panels/<use>.yaml`). Required. |
| `id` | Pane identity (runtime file name + override key). Defaults to `use`. |
| `enabled` | `false` keeps the pane defined but omits it from the layout entirely. |
| `title` | tmux `pane-border-format` label (shown on the top border). |
| `split` | `h` (left/right) or `v` (top/bottom). |
| `size` | Percentage handed to `tmux split-window -p` (the new pane's size). |
| `target` | The `use`/`id` of the already-placed pane to split from (default: base). |
| `border_color` | tmux pane border color (`fg=<color>`). |
| `command` | Override the shell command the pane runs. |
| `with` | Free-form per-instance override block, deep-merged into the pane. |

## Editing the layout (config-only workflows)

### Disable a pane

Turn off the `htop` system monitor in a role — edit its layout preset, no other
change:

```yaml
# config/layouts/coder.yaml
  - use: htop
    target: filetree
    split: v
    size: 15
    enabled: false      # ← keep it defined, drop it from the rendered layout
```

The engine skips it: no split, no title, no border color, no `send-keys`, and no
runtime file are emitted for a disabled pane. The remaining panes renumber
automatically.

### Resize a pane

Give the message-bus feed more room — bump its `size` (the `-p` percentage of the
right column it splits from):

```yaml
# config/layouts/coder.yaml
  - use: kafka_feed
    target: editor
    split: v
    size: 45          # ← was 30; the feed now takes 45% of the right column
```

### Reorder or retitle a pane

Reorder by moving list entries (remember: a pane's `target` must be listed before
it). Retitle or recolor a single instance without touching the panel type:

```yaml
  - use: editor
    target: filetree
    split: h
    size: 75
    title: "Scratch"      # ← per-instance title override
    border_color: cyan    # ← per-instance color override
```

### Override one pane for one worker only

Without editing the shared preset, a single worker can override a pane via
`layout.panes.<id>` in its worker config:

```yaml
# config/workers/coder.yaml
layout:
  preset: coder
  panes:
    editor:
      command: "vim"      # this worker uses vim; others keep the preset default
```

## Runtime resolved-file mechanism

For every enabled pane, the engine writes the **fully-resolved** config dict to
`<runtime-dir>/<id>.yaml` (default `/tmp/panes/<id>.yaml`) before emitting the
tmux script. This gives pane processes a single source of truth.

In particular, the `kafka_feed` pane's command template

```
python3 /app/tail_bus.py --bus-config {config_path} --feed-config {resolved_path}
```

has `{resolved_path}` substituted with `/tmp/panes/kafka_feed.yaml` — the resolved
file containing the merged `content:` block. `tail_bus.py` reads that file, so the
feed's colors/filters/highlight/payload settings always match what the layer merge
produced. (`{config_path}` is substituted with the `--config` value; both
placeholders are always available, alongside every scalar field of the resolved
pane.)

## Kubernetes ConfigMap mapping

The layered config is designed to map cleanly onto Kubernetes ConfigMaps as the
project moves off `docker-compose` (the panels/layouts are already `COPY`'d into
the image at `/config/panels` and `/config/layouts` — see the `Dockerfile`):

| Config | Kubernetes object | Scope |
|---|---|---|
| `config/panels/*.yaml` | **one shared ConfigMap** (e.g. `panel-types`) | Mounted read-only at `/config/panels` in every worker pod. Panel *types* are role-agnostic, so one map serves all roles. |
| `config/layouts/coder.yaml` | small **per-role ConfigMap** (`layout-coder`) | Mounted at `/config/layouts` in the coder pod. |
| `config/layouts/tester.yaml` | small per-role ConfigMap (`layout-tester`) | Mounted in the tester pod. |
| `config/layouts/manager.yaml` | small per-role ConfigMap (`layout-manager`) | Mounted in the manager pod. |
| `config/workers/<role>.yaml` | per-role worker ConfigMap (already planned) | Mounted at `/config/worker.yaml`; selects the preset + per-pane overrides. |

**Reconfigure a role = edit its layout ConfigMap + restart the pod.** No image
rebuild, no `startup.sh` change. Because the engine reads `/config/panels` and
`/config/layouts` (falling back to repo-relative dirs for local dry-runs), the
same code path serves both a local `docker compose` run and a k8s deployment.

Env-driven selection (`LAYOUT_PRESET`) still applies inside a pod spec, so a single
image + shared panels map can be re-pointed at a different preset via an env var
if a per-role layout map is not desired.

## Related docs

- [docs/build_layout.md](build_layout.md) — module-level API for the layout engine.
- [docs/panels.md](panels.md) — catalog of the five panel types and their fields.
- [docs/message_bus_feed.md](message_bus_feed.md) — how the `kafka_feed` pane renders.
- [docs/message_bus.md](message_bus.md) — the underlying Kafka message bus.
- [docs/layout_live_reload.md](layout_live_reload.md) — parked proposal for
  reloading layout config without an image rebuild/restart; today, layout
  changes always require both (see that doc for why).
