# build_layout.py — Config-Driven Tmux Layout Engine

## Overview

`app/build_layout.py` turns the worker container's tmux layout from a hardcoded
block in `startup.sh` into declarative config. It reads a worker config, picks a
**layout preset** (`config/layouts/<preset>.yaml`), resolves every pane against
its reusable **panel-type** default (`config/panels/<use>.yaml`), then:

1. Emits, on **STDOUT**, a shell-evaluable sequence of `tmux` commands that
   reproduces the layout (`new-session` → ordered `split-window`/`select-pane`
   → per-pane titles and border colors → `send-keys`). `startup.sh` runs
   `eval "$(python3 /app/build_layout.py --config "$CONFIG_PATH")"`.
2. Writes each pane's fully-resolved config dict to `<runtime-dir>/<id>.yaml`
   (default `/tmp/panes/<id>.yaml`) so pane processes read one source of truth.
   In particular the `kafka_feed` pane's resolved file (with its `content:`
   block) is what `tail_bus.py --feed-config` consumes.

It exists so a role's panels can be reordered, resized, retitled, recolored, or
disabled with **only a config edit** — no `startup.sh` or image change — which is
the prerequisite for the planned Kubernetes ConfigMap-driven deployment.

> **STDOUT is reserved for the tmux script.** All logging goes to STDERR; nothing
> else may be printed to stdout or the `eval` breaks.

## Signature

```python
def build(config_path: str, panels_dir: str, layouts_dir: str,
          runtime_dir: str) -> tuple[list[str], list[dict]]
def main(argv: list[str] | None = None) -> int
```

CLI:

```
python3 /app/build_layout.py \
    --config <worker.yaml>        # default /config/worker.yaml
    [--panels-dir <dir>]          # default /config/panels, else config/panels
    [--layouts-dir <dir>]         # default /config/layouts, else config/layouts
    [--runtime-dir <dir>]         # default /tmp/panes
```

## Parameters

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| `--config` | path | no | `/config/worker.yaml` | Worker config selecting a preset; also substituted as `{config_path}`. |
| `--panels-dir` | path | no | `/config/panels` → `config/panels` | Dir of panel-type YAML files. In-container path preferred; falls back to repo-relative for local dry-runs. |
| `--layouts-dir` | path | no | `/config/layouts` → `config/layouts` | Dir of layout-preset YAML files. |
| `--runtime-dir` | path | no | `/tmp/panes` | Where resolved per-pane configs are written. |

Env vars: `LAYOUT_PRESET` overrides the preset chosen in the worker config;
`BUILD_LAYOUT_LOG_LEVEL` sets the log level (default `INFO`).

## Return Value

- `build(...)` returns `(tmux_lines, resolved_panes)`: the ordered list of tmux
  command strings, and the list of resolved (enabled) pane dicts in emission order.
- `main(...)` writes the tmux script to stdout and returns an exit code
  (`0` success, `1` on a build failure logged to stderr).

## Config schema

### Layout preset (`config/layouts/<preset>.yaml`)

```yaml
preset: coder
panes:
  - use: filetree    size: 25                                   # base pane
  - use: editor      target: filetree  split: h  size: 75  with: { variant: nvim }
  - use: avatar      target: filetree  split: v  size: 60
  - use: kafka_feed  target: editor    split: v  size: 30
  - use: htop        target: filetree  split: v  size: 15  enabled: false
```

Universal per-pane knobs: `use`, `id` (defaults to `use`), `split` (`h`|`v`),
`size`, `target`, `title`, `border_color`, `command`, `enabled`, `with`.

### Panel type (`config/panels/<use>.yaml`)

```yaml
type: editor
title: "Editor"
border_color: green
command: "nvim"
```

## Geometry / `size` → tmux `-p` mapping

- The **first** pane in the list is the **base pane** (the whole screen created
  by `new-session`); its `size` is informational (no split emitted).
- Every **subsequent** pane is created by splitting a **target** pane (named by
  the `use`/`id` of an already-placed pane; defaults to the base pane). The
  pane's `size` is passed verbatim as `tmux split-window -p <size>`. **tmux `-p`
  sizes the newly-created pane**, so `size` is the new pane's percentage of the
  target.
- tmux numbers panes `0..N` in creation order, which equals the list order, so
  `id → pane index` is deterministic and stable for `send-keys` targeting.

Worked example — the `coder` preset, a three-column layout reverse-engineered
from a live-tuned tmux session's `window_layout` string:

| order | pane | split | target | `-p` | result |
|---|---|---|---|---|---|
| 0 | editor | (base) | — | — | column 1, ~33% wide |
| 1 | kafka_feed | h | editor | 66 | columns 2+3 combined, ~66% wide |
| 2 | filetree | h | kafka_feed | 49 | column 2 (whole), carved off the block |
| 3 | htop | v | kafka_feed | 50 | column 3 top; remainder stays kafka_feed |
| 4 | avatar | v | filetree | 19 | column 2 top; remainder stays filetree |

> **`size` is always the `-p` value, i.e. the new pane's %** — the target pane
> keeps whatever percentage remains, never the pane being carved out.

## Resolution / merge order (later wins)

```
panel-type default (config/panels/<use>.yaml)
  → layout placement + overrides (incl. `with:` block)
  → worker-config per-pane override (worker.yaml `layout.panes.<id>`)
  → env vars (LAYOUT_PRESET selects the preset)
```

Nested dicts are deep-merged; scalars and lists are replaced. Panes with
`enabled: false` are kept defined in config but omitted from the output (no
split, no title, no `send-keys`, no runtime file).

## Placeholder substitution

Any `{name}` token in a pane's `command` is substituted from a context of the
pane's scalar fields plus two contract placeholders:

- `{config_path}` — the `--config` value passed to the engine.
- `{resolved_path}` — that pane's `<runtime-dir>/<id>.yaml` file (POSIX-joined,
  since the emitted script runs in the Linux container).

Unknown tokens are left intact. This resolves the kafka_feed panel template
`python3 /app/tail_bus.py --bus-config {config_path} --feed-config {resolved_path}`
to the CLI contract agreed with `tail_bus.py`.

## Dependencies

- Standard library: `os`, `sys`, `argparse`, `logging`, `pathlib`.
- Third-party: `PyYAML` (already in the worker image).
- Reads: `config/panels/*.yaml`, `config/layouts/*.yaml`, the worker config.
- Consumed by: `startup.sh` (via `eval`) and, indirectly, `app/tail_bus.py`
  (reads the resolved `kafka_feed.yaml` this engine writes).

## Usage Examples

Dry-run locally against the repo config and inspect the emitted script:

```bash
python3 app/build_layout.py \
    --config config/workers/coder.yaml \
    --panels-dir config/panels \
    --layouts-dir config/layouts \
    --runtime-dir /tmp/panes
```

In the container (`startup.sh`), apply the layout to a live tmux server:

```bash
eval "$(python3 /app/build_layout.py --config "$CONFIG_PATH")"
```

Override the preset at runtime without editing config:

```bash
LAYOUT_PRESET=tester python3 /app/build_layout.py --config /config/worker.yaml
```

## Error Handling

- `load_yaml` wraps file reads in `try/except (OSError, yaml.YAMLError)`, logs an
  ERROR to stderr, and re-raises — a missing/malformed worker config or preset is
  a hard failure surfaced by `main()` as exit code `1`.
- `resolve_pane` logs an ERROR and **skips** a pane whose panel-type file cannot
  be loaded, rather than aborting the whole layout.
- `write_runtime_config` logs and re-raises `OSError` on write failure.
- `substitute` logs and returns the template unchanged on a formatting error, and
  leaves unknown `{tokens}` intact.
- `main()` catches any unexpected exception from `build`, logs it to stderr, and
  returns `1` so `startup.sh`'s `eval` receives no partial script.

## Changelog

- **v1.0.0** (2026-07-01): Initial version. Config-driven layout engine replacing
  the hardcoded tmux block in `startup.sh`; five-pane `coder` preset reproduces
  the original geometry; `tester`/`manager` presets vary the editor pane.
