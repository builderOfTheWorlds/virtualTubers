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
| `avatar` | `Avatar` | magenta | `python3 /app/avatar.py --config {config_path}` |
| `editor` | `Editor` | green | `nvim` |
| `kafka_feed` | `Message Bus` | cyan | `python3 /app/tail_bus.py --bus-config {config_path} --feed-config {resolved_path}` |
| `htop` | `System` | yellow | `htop` |

---

### `filetree`

- **Purpose:** view of the worker's workspace repo (`/data/repo`), and an
  interactive shell the agent can drive via `tmux_control.py` (see
  `agent.py`'s `demo_filetree_ls`).
- **Default command:** `bash -c "tree /data/repo 2>/dev/null || echo (no workspace yet); exec bash"`
  — prints the tree once (or a placeholder until a workspace exists), then
  `exec bash` hands the pane a live prompt. Not a `watch` loop: the view only
  updates when a command runs in it (agent-driven or manual), it does not
  auto-refresh on a timer.
- **Notable fields:** universal knobs only (`title`, `border_color`, `command`,
  plus placement `split`/`size`/`target` in the layout preset). In the `coder`
  preset it is the **base pane** (a 25% left column).

### `avatar`

- **Purpose:** the agent's ASCII-art face + speech bubble (`app/avatar.py`),
  driven by the worker config's `avatar:` block.
- **Default command:** `python3 /app/avatar.py --config {config_path}` — the engine
  substitutes `{config_path}` with the worker config path.
- **Notable fields:** universal knobs only. Expression states / bubble sizing come
  from the worker config's `avatar:` section, not from this panel file.

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
