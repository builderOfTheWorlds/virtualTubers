# Orchestration Prompt — Modular Tmux Panels + Kafka Message Feed

> **You are an orchestrator.** Read this whole document, then delegate the work below to
> sub-agents (Task tool) per the **Delegation Plan**. Keep the main context window on
> coordination; let sub-agents do research and implementation. Run independent streams in
> parallel. Every decision here is already settled with the user — do not re-litigate scope;
> if something is genuinely ambiguous, make the smallest reasonable choice and note it.

---

## Mission

Turn the worker container's hardcoded tmux layout into a **config-driven, modular panel
system**, and upgrade the Kafka "agent chat" pane into a **pretty-formatted, filterable
message feed**. The user is moving deployment toward **Kubernetes**, so layout and panels
must be declarative config (ConfigMap-friendly), not baked into `startup.sh`.

## Current State (verified — read these before changing them)

- `startup.sh:36-62` — tmux layout is **hardcoded**: a fixed sequence of `split-window` +
  `send-keys`. Five panes: filetree, avatar (`app/avatar.py`), editor (`nvim`),
  agent-chat (`app/tail_bus.py`), htop.
- `app/tail_bus.py` — **already** a live Kafka consumer that prints every message on the
  topic (`to_filter=False`), one cramped uncolored line each. This IS the panel to upgrade;
  do not create a second one.
- `app/message_bus.py` — envelope helper. Every message has:
  `id`, `from`, `to`, `type`, `payload` (dict), `timestamp` (ISO-8601 UTC).
  `BROADCAST = "broadcast"`. `MessageConsumer.poll_new(to_filter=False)` yields all messages.
- `app/agent.py` — publishes **heartbeats every tick** → the feed will be flooded unless
  filtered. Heartbeat filtering is mandatory, not optional.
- `config/worker.yaml:135-144` — a `layout:` section exists (`variant: coder|tester|manager`)
  but **no code reads it**. It is dead config to be replaced by the new schema.
- `config/workers/{coder,manager,tester}.yaml` — per-role configs, mounted into each
  container at `/config/worker.yaml` (see `docker-compose.yml` volumes). These become
  per-role ConfigMaps under k8s.
- `docker-compose.yml` — configs mounted read-only per worker; `KAFKA_*` / `WORKER_ID` set
  via env. Env must continue to override file values.

## Decisions (settled with the user)

1. **Full layered config**: separate reusable **panel-type** files **and** **layout**
   composition presets. Not a single flat list.
2. **Kafka feed customizations — all in scope**: heartbeat filter, type filters + type
   highlighting, payload controls (pretty/raw/hidden + max_chars), direction filter.
3. **Feed content = pretty-formatted**: colorized sender, aligned columns, timestamp.
4. **Feed colors live in the feed panel config** (a new config field), NOT coupled to the
   `avatar:` block — senders include `broadcast`, which has no avatar.
5. **Panes are processes in one container's tmux**, never separate containers/pods. The
   modular unit is a *pane definition in config*, not a sidecar. Do not architect toward
   pane-per-container.

## Target Architecture

```
config/
  panels/                 # reusable panel TYPE definitions (one file each)
    kafka_feed.yaml       # rich: colors, filters, highlight, payload controls
    avatar.yaml
    filetree.yaml
    editor.yaml
    htop.yaml
  layouts/                # composition presets: place & size panels per role
    coder.yaml
    tester.yaml
    manager.yaml
  worker.yaml             # picks a layout preset; may override per-pane
  workers/*.yaml
```

**Resolution / merge order (later wins):**
panel-type default → layout-preset placement + overrides → worker-config override → env vars.

**k8s mapping:** `config/panels/` → one shared ConfigMap; each `config/layouts/*.yaml` →
a small per-role ConfigMap. Reconfigure a role = edit its layout ConfigMap + restart pod.

### Layout composition schema (in a `config/layouts/*.yaml`)

```yaml
preset: coder
panes:
  - use: filetree    split: h  size: 25
  - use: avatar      split: v  size: 60
  - use: editor      split: v  size: 70  with: { variant: nvim }   # per-instance override
  - use: kafka_feed  split: v  size: 30
  - use: htop        split: v  size: 15  enabled: false            # toggle off, keep defined
```

Universal per-pane knobs: `enabled`, `title` (tmux `pane-border-format`), `split`, `size`,
`border_color`, `command` (override).

### Kafka feed panel schema (`config/panels/kafka_feed.yaml`)

```yaml
type: kafka_feed
title: "Message Bus"
border_color: cyan
command: "python3 /app/tail_bus.py --bus-config {config_path} --feed-config {resolved_path}"
content:
  colors: { coder: green, manager: yellow, tester: magenta, broadcast: cyan }
  filters:
    hide_types: [heartbeat]        # heartbeat filter (mandatory default)
    show_types: []                 # empty = all except hidden
    direction: all                 # all | broadcast | to:<id> | from:<id>
  highlight:                       # color the TYPE column by category
    task_complete: green
    clarification_request: yellow
    error: red
  payload: { mode: pretty, max_chars: 80 }   # pretty | raw | hidden
  timestamp: { format: "%H:%M:%S", local: true }
  header: true
```

## Components to Build

**Layout engine**
- `app/build_layout.py` — load panels + selected layout, resolve the merge, then:
  (a) emit the `tmux split-window` / `send-keys` / `set pane-border-format` (titles) /
  border-color command sequence to stdout; (b) write each pane's **resolved** config to a
  runtime dir (`/tmp/panes/<id>.yaml`) so pane processes read one source of truth.
- `startup.sh` — replace lines 36-62 with `eval "$(python3 /app/build_layout.py --config "$CONFIG_PATH")"`.

**Kafka feed**
- Rewrite `app/tail_bus.py`: read resolved feed config (`--feed-config`), apply
  heartbeat/type/direction filters, colorize sender + type-highlight, render aligned columns
  with truncated payload. Keep it a plain `print()` stdout process (must work under
  xterm + ffmpeg capture; no full-screen TUI).

**Config files**
- `config/panels/*.yaml` (all five), `config/layouts/{coder,tester,manager}.yaml`,
  and update `config/worker.yaml` + `config/workers/*.yaml` to select a preset and drop the
  dead `layout.variant`.

**Tests (pytest, per CLAUDE.md — mock Kafka, no real broker)**
- `tests/test_build_layout.py` — config in → exact tmux command sequence + resolved files out.
- `tests/test_tail_bus_format.py` — envelope in → formatted/filtered line out (heartbeat
  dropped, direction filter honored, colors + type-highlight applied, payload truncated).

**Docs (per CLAUDE.md)**
- `docs/layout_system.md`, `docs/panels.md`; update `docs/message_bus.md` and README
  (Project Structure + a k8s ConfigMap note). One doc per new module.

## Delegation Plan

Two streams are independent → run in parallel. Stream C depends on A+B landing.

- **Sub-agent A — Kafka feed** (`app/tail_bus.py` rewrite + `config/panels/kafka_feed.yaml`
  + `tests/test_tail_bus_format.py`). Self-contained; only needs the message envelope shape.
- **Sub-agent B — Layout engine** (`app/build_layout.py`, remaining `config/panels/*.yaml`,
  `config/layouts/*.yaml`, `startup.sh` refactor, `tests/test_build_layout.py`). Owns the
  panel/layout schema and merge resolver. Must define the `kafka_feed` pane's `command`
  contract that Stream A's CLI implements — coordinate the `--bus-config`/`--feed-config`
  flag names up front so A and B agree.
- **Sub-agent C — Docs + k8s + integration** (after A+B): write `docs/layout_system.md`,
  `docs/panels.md`, update `docs/message_bus.md` + README, add k8s ConfigMap mapping notes,
  and do a dry-run of `build_layout.py` to confirm the emitted tmux sequence is valid.

**Contract to fix before A and B start (write it down, share to both):**
`tail_bus.py` CLI = `--bus-config <worker.yaml>` (Kafka connection + WORKER_ID) and
`--feed-config <resolved kafka_feed panel yaml>` (the `content:` block). `build_layout.py`
writes the resolved feed config and emits exactly that command.

## Acceptance Criteria

- `docker compose up` (or a single `python3 app/build_layout.py --config …`) produces the
  same five-pane layout, now driven entirely by `config/layouts/*.yaml`.
- Reordering/resizing/disabling a pane requires **only** a config edit — no `startup.sh` or
  image change.
- The message feed hides heartbeats by default, colors senders per config, highlights types,
  truncates payloads, and shows `HH:MM:SS from ──▶ to  type  payload`.
- Env vars still override config (`KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`, `WORKER_ID`).
- New/changed code has tests and a `docs/*.md` entry; README structure updated.

## Guardrails

- Do not create a second Kafka pane — upgrade the existing one.
- Do not couple feed colors to `avatar:` (broadcast has no avatar).
- Keep `tail_bus.py` a plain stdout printer.
- Preserve env-over-file override precedence everywhere.
- Follow CLAUDE.md: structured logging, one doc per module, pytest with mocked externals.
