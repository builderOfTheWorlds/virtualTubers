# Panels — Panel-Type Catalog

Panel *types* are the reusable building blocks of the tmux layout. Each lives in
`config/panels/<use>.yaml` and defines a pane's default `title`, `border_color`,
and `command`. Layout presets (`config/layouts/*.yaml`) place, size, and (where
needed) override these types per role.

For how panels are composed, merged, sized, and mapped to Kubernetes ConfigMaps,
see [docs/layout_system.md](layout_system.md). For the engine API, see
[docs/build_layout.md](build_layout.md).

**Command placeholders.** A panel `command` may contain `{name}` tokens the layout
engine substitutes at emit time. Two are always available: `{config_path}` (the
worker config passed to `build_layout.py`) and `{resolved_path}` (that pane's
resolved runtime file, `/tmp/panes/<id>.yaml`). Every scalar field of the resolved
pane is also available as a token.

## Catalog

| Panel (`use`) | Default title | Border color | Default command |
|---|---|---|---|
| `filetree` | `Files` | blue | `bash -c "tree /data/repo …; exec bash"` |
| `avatar` | `Avatar` | magenta | `/opt/render3d/bin/python3 /app/avatar.py --config {config_path}` |
| `editor` | `Editor` | green | `nvim` |
| `kafka_feed` | `Message Bus` | cyan | `python3 /app/tail_bus.py --bus-config {config_path} --feed-config {resolved_path}` |
| `htop` | `System` | yellow | `htop` |
| `radar_chart` | `Stats` | green | `/opt/render3d/bin/python3 /app/radar_pane.py --config {config_path}` |
| `knowledge_graph` | `Knowledge` | cyan | `/opt/render3d/bin/python3 /app/knowledge_graph_pane.py --config {config_path}` |
| `thinking` | `Thinking` | yellow | `python3 /app/thinking_pane.py --bus-config {config_path}` |
| `chat_list` | `chats` | white | `python3 /app/chat_list_pane.py` |

---

### `filetree`

- **Purpose:** view of the worker's workspace repo (`/data/repo`), and an
  interactive shell the agent can drive via `tmux_control.py` (see
  `agent.py`'s `demo_filetree_ls`).
- **Default command:** `bash -c "tree /data/repo 2>/dev/null || echo \"(no workspace yet)\"; exec bash"`
  — prints the tree once (or a placeholder until a workspace exists), then
  `exec bash` hands the pane a live prompt. Not a `watch` loop: the view only
  updates when a command runs in it (agent-driven or manual), it does not
  auto-refresh on a timer.
- **Notable fields:** universal knobs only (`title`, `border_color`, `command`,
  plus placement `split`/`size`/`target` in the layout preset). In the `coder`
  preset it is the **base pane** (a 25% left column).

### `avatar`

- **Purpose:** the agent's face + speech bubble (`app/avatar.py`), driven by
  the worker config's `avatar:` block via a pluggable provider
  (`app/avatar_providers/`).
- **Default command:** `/opt/render3d/bin/python3 /app/avatar.py --config {config_path}`
  — the engine substitutes `{config_path}` with the worker config path. Runs
  under the `/opt/render3d` Python 3.11 venv (not the system python3.10) so
  the `termgl_avatar` provider is selectable without a second panel-type
  entrypoint — see "3D rendering (termgl)" below. That venv has the full
  `requirements.txt` installed too, so `builtin` and `ascii_avatar` work
  unchanged when selected instead.
- **Providers** (`avatar.provider` in the worker config, or env
  `AVATAR_PROVIDER`): `builtin` (static ASCII box face, always available,
  no extra deps) | `ascii_avatar` (animated 2D ASCII face via the vendored
  repo) | `termgl_avatar` (rotating shaded 3D mesh via termgl — currently a
  placeholder icosahedron; see "3D rendering (termgl)" below). Any provider
  construction failure falls back to `builtin` automatically
  (`avatar_providers/__init__.py`).
- **Notable fields:** universal knobs only. Expression states / bubble sizing
  come from the worker config's `avatar:` section, not from this panel file.

### `radar_chart`

- **Purpose:** the tmux **"Stats"** pane — a shaded 3D radar/spike mesh
  (`app/radar_pane.py`) of six live model-performance metrics (tokens/sec,
  avg latency, context tokens, error rate, uptime, messages sent), written by
  `app/agent_metrics.py` after every LLM completion. See "3D rendering
  (termgl)" below.
- **Default command:** `/opt/render3d/bin/python3 /app/radar_pane.py --config {config_path}`.
- **Notable fields:** universal knobs only. Metrics file path is derived from
  the worker config's `worker_id`, not a panel field — see
  `app/radar_pane.py`'s module docstring for the full resolution order.

### `knowledge_graph`

- **Purpose:** the tmux **"Knowledge"** pane — a shaded 3D node/edge graph
  (`app/knowledge_graph_pane.py`), currently STATIC PLACEHOLDER data (wiring
  to real campaign-pack/Postgres data is separate future work — the render
  is clearly labeled "(placeholder)" every frame). See "3D rendering
  (termgl)" below.
- **Default command:** `/opt/render3d/bin/python3 /app/knowledge_graph_pane.py --config {config_path}`.
- **Notable fields:** universal knobs only.

### `thinking`

- **Purpose:** consumes the `agent_thinking` bus message type (published by
  `app/agent_metrics.py`'s `InstrumentedLLMClient` wrapper), pinned to THIS
  worker's own stream — prints each as it arrives, plain scrolling text.
- **Default command:** `python3 /app/thinking_pane.py --bus-config {config_path}`.

### `chat_list`

- **Purpose:** narrow chat-room-name list. Static stub today — always shows
  exactly one entry, `all`.
- **Default command:** `python3 /app/chat_list_pane.py`.

---

## 3D rendering (termgl)

`radar_chart`, `knowledge_graph`, and the `termgl_avatar` provider all render
through **[termgl](https://github.com/wojciech-graj/pyTermGL)** (`pip install
termgl`) — a real triangle-mesh rasterizer (Z-buffer, backface culling,
custom vertex/pixel shaders, perspective camera) that draws directly to
terminal characters. This is genuine 3D rendering, not an ASCII-art trick;
see `app/render3d_common.py` for the shared camera/lighting-shader setup and
`app/mesh3d.py` for the pure-numpy mesh builders (radar spikes, graph-node
octahedra, the avatar's placeholder icosahedron).

**Why a second Python venv.** termgl requires Python >=3.11 (it uses
`enum.FlagBoundary.CONFORM`, a 3.11 language addition — this is a hard
language-level requirement, not a packaging artifact). This project's base
image (`ubuntu:22.04`) ships Python 3.10 as `python3`. Rather than bumping
the whole image's interpreter (which would risk shifting every other
dependency's resolution), the Dockerfile installs a second, isolated Python
3.11 interpreter via the deadsnakes PPA into `/opt/render3d` — the same
isolation pattern the `aider` coding backend already uses for its own venv.
The full `requirements.txt` is installed into `/opt/render3d` too (not just
`termgl`+`numpy`), since `avatar.py`/`radar_pane.py`/`knowledge_graph_pane.py`
all import `message_bus` (pyyaml, kafka-python) regardless of which
avatar provider is active. Any pane invoking one of these three scripts
must use `/opt/render3d/bin/python3`, never the system `python3` — the
panel-type `command` fields above already do this.

**DOUBLE_CHARS and text.** Every 3D pane enables termgl's `DOUBLE_CHARS`
setting (square pixels — two terminal columns per rendered "pixel", since
monospace cells are roughly twice as tall as wide). This setting doubles
*every* character termgl draws, including text drawn via `ctx.puts()` — a
header written that way renders as unreadable doubled text (`[[ccooddeerr]]`
instead of `[coder]`). All three panes instead draw their header/status line
via `render3d_common.write_header_line()`, which writes plain-width text
directly via ANSI cursor positioning *after* `ctx.flush()`, bypassing
termgl's text path entirely — matching the same escape-sequence technique
`avatar_providers/ascii_avatar.py` already uses for its own speech-bubble
overlay. Any new 3D pane must follow this pattern for text, not `ctx.puts()`.

**Testing.** `app/mesh3d.py` has zero termgl import (pure numpy mesh
builders only) and is fully unit-tested under the regular test venv
(`tests/test_radar_pane.py`, `tests/test_knowledge_graph_pane.py` import it
directly). `app/render3d_common.py` and the `render_*_3d()` rendering
functions in `radar_pane.py`/`knowledge_graph_pane.py`/`termgl_avatar.py`
import `termgl` lazily (inside the function body, not at module level) so
importing those modules under Python <3.11 (this project's regular test
venv) never fails — only calling the rendering function itself would, and
those functions are excluded from the standard test suite (they'd require
the `/opt/render3d` interpreter to actually exercise).

**Design origin / prototypes.** Earlier flat-ASCII mockups (block-character
bars, hand-drawn node/edge diagrams) are preserved in
`.claude/prompts/tuber_base_mockups/` for reference — they were rejected in
favor of termgl because flat 2D layouts collided node labels/edges past a
handful of graph nodes, and a flat radar polygon read as an indistinct blob
at pane width. termgl's real Z-buffer/lighting solves both structurally
(occlusion separates far/near graph nodes; shading gives the radar shape
visible depth) rather than requiring more layout tuning.

---

### `editor`

- **Purpose:** the main editor/output pane. The default is a full `nvim` editor,
  but presets override `command` per role.
- **Default command:** `nvim`.
- **Per-role overrides** (in each layout preset):
  - **coder** — `nvim` (`with: { variant: nvim }`).
  - **tester** — title `Test Output`, runs the suite on a loop:
    `watch -n5 "cd /data/repo … && pytest --cov -q … | tail -40 …"`.
  - **manager** — title `Ticket Board`, watches the shared tickets dir:
    `watch -n5 "cat /data/world-state/tickets/*.md …"`.
- **Notable fields:** `command`, `title`, and the free-form `with:` block (e.g.
  `variant: nvim|pytest|tickets`) are the intended override points.

### `htop`

- **Purpose:** system-monitor strip (CPU/memory) for visual "the machine is alive"
  feedback on stream.
- **Default command:** `htop`.
- **Notable fields:** universal knobs only. In the standard presets it is a thin
  15% strip under the file tree; a common tweak is `enabled: false` to reclaim the
  space (see [docs/layout_system.md](layout_system.md#disable-a-pane)).

---

### `kafka_feed` (the rich message-bus feed)

- **Purpose:** the tmux **"Message Bus"** pane — a live, colorized, filterable feed
  of every message on the Kafka bus (not just those addressed to the local worker).
  It replaces the old cramped one-line-per-message consumer.
- **Default command:**
  `python3 /app/tail_bus.py --bus-config {config_path} --feed-config {resolved_path}`.
  The engine writes the resolved panel (including the `content:` block below) to
  `/tmp/panes/kafka_feed.yaml` and passes it as `--feed-config`, so the feed reads
  exactly what the layer merge produced.
- **Rendering** (`HH:MM:SS from ──▶ to  type  payload`): colorized sender, aligned
  columns, TYPE-column highlight, truncated payload. Full rendering details are in
  **[docs/message_bus_feed.md](message_bus_feed.md)**.

Only the `content:` block is consumed by `tail_bus.py`. Its schema:

| Key | Type | Default | Description |
|---|---|---|---|
| `colors` | map | `{coder: green, manager: yellow, tester: magenta, broadcast: cyan, operator: blue}` | Sender color keyed by the message `from` value. **Feed colors live here, never coupled to the `avatar:` block** — senders include `broadcast`, which has no avatar. |
| `filters.hide_types` | list | `[heartbeat, status_update]` | Message types to drop. See the status_update note below. |
| `filters.show_types` | list | `[]` | Empty = show all except hidden; non-empty = whitelist (only these types). |
| `filters.direction` | str | `all` | `all` \| `broadcast` \| `to:<id>` \| `from:<id>`. Filter by message routing. |
| `highlight` | map | `{task_complete: green, clarification_request: yellow, error: red, bug_report: red, test_passed: green, manager_report: cyan, operator_reply: blue}` | Colors the TYPE column when the message type matches a category. |
| `payload.mode` | str | `pretty` | `pretty` (key=value) \| `raw` (`repr`) \| `hidden` (omit payload). |
| `payload.max_chars` | int | `80` | Truncate the rendered payload to this length (ellipsis appended). |
| `timestamp.format` | str | `%H:%M:%S` | `strftime` format for the leading time column. |
| `timestamp.local` | bool | `true` | Convert the UTC ISO stamp to local time before formatting. |
| `header` | bool | `true` | Print a one-time dim column header at startup. |

Filtering order: `hide_types` → `show_types` (whitelist if non-empty) → `direction`.

> **status_update / heartbeat note.** `app/agent.py` publishes its per-tick
> heartbeat flood as message type **`status_update`** (payload
> `{"text": "heartbeat #N"}`), **not** `heartbeat`. To stop that flood the feed
> hides **both** `heartbeat` and `status_update` by default. `agent.py` was
> intentionally left unchanged; the filter is fully configurable — edit
> `content.filters.hide_types` to change what is hidden.

**Example — a debug-oriented feed** (worker override that keeps only errors and
clarifications, shows raw payloads, and re-enables status updates):

```yaml
# config/workers/coder.yaml → layout.panes.kafka_feed
layout:
  preset: coder
  panes:
    kafka_feed:
      content:
        filters:
          hide_types: []                              # show everything…
          show_types: [error, clarification_request]  # …but only these two
        payload:
          mode: raw
```

Because nested dicts are deep-merged, only the listed keys change; the rest of the
`content:` block (colors, highlight, timestamp, header) keeps its panel-type
defaults.

## Related docs

- [docs/layout_system.md](layout_system.md) — the config model, geometry, and k8s mapping.
- [docs/build_layout.md](build_layout.md) — the layout engine's module API.
- [docs/message_bus_feed.md](message_bus_feed.md) — `tail_bus.py` feed rendering.
- [docs/message_bus.md](message_bus.md) — the Kafka message bus itself.
