# Orchestration Prompt — `tuber_base` Layout: Radar/Knowledge/Avatar/Thinking/Chat+Kafka

> **You are an orchestrator.** Read this whole document, then delegate the work below to
> sub-agents per the **Delegation Plan**. Keep the main context window on coordination;
> let sub-agents implement. Streams run in parallel against the FROZEN CONTRACTS below —
> do not renegotiate them without flagging back to the user. Every decision here is already
> settled with the user; if something is genuinely ambiguous, make the smallest reasonable
> choice and note it in the final summary.

## Mission

Redesign the six individual-worker tmux layouts (coder, coder-native, coder-opencode,
coder-aider, tester, manager) into ONE new shared preset, `tuber_base`, matching this
user-drawn mock:

```
______________________________________
|radar  |       |          |c|        |
|chart  |       |          | |        |
|_______|       |  AVATAR  | |        |
|               |          | |        |
|               |__________| |        |
|               |          | | Kafka  |
|knoledge graph |          | |        |
|               | Thinking | |        |
|               |          | |        |
|               |          | |        |
|-------------------------------------|
|__________tmux bottom bar____________|
```

Left column (narrow): radar chart (top) over a knowledge-graph pane (bottom).
Middle column: avatar (top) over the model's chain-of-thought "Thinking" pane (bottom).
Narrow "c" column: a chat-room-name list (stub: just "all" for now).
Right column: the Kafka feed, reformatted as a plain conversation log (see contract below).

Also: bump capture resolution so more detail fits before ffmpeg scales down to the
1080p stream output, and add a real per-worker "thinking" step + live model-performance
metrics feed the radar chart plots.

**`roundtable` (tuber_0/GM) is OUT OF SCOPE** — it's a different 8-tile grid and stays
exactly as-is. Only the six individual-worker configs move to `tuber_base`.

## Current State (verified — read before changing)

- `app/build_layout.py` — the config-driven tmux layout engine (docs/build_layout.md,
  docs/layout_system.md). Panel types in `config/panels/*.yaml`, layout presets in
  `config/layouts/*.yaml`, resolution order: panel default -> layout placement -> worker
  override -> env. Read `docs/layout_system.md` in full before touching layouts.
- `config/layouts/{coder,tester,manager,replay}.yaml` — existing 3-column presets (editor
  or replay | avatar+filetree | htop+kafka_feed). `config/workers/{coder,coder-native,
  coder-opencode,coder-aider,tester,manager}.yaml` each set `layout.preset` to one of these
  (see each file's `layout:` block) — these six files are what get repointed to
  `tuber_base`.
- `app/tail_bus.py` + `config/panels/kafka_feed.yaml` — the existing rich Kafka feed
  (docs/message_bus_feed.md, docs/panels.md). Today it renders `HH:MM:SS from -> to type
  payload` colorized columns. This mode must be PRESERVED (used elsewhere / configurable)
  — add a new rendering mode alongside it, do not delete the old one.
- `app/agent.py` — 8 call sites of `llm_client.complete(...)` (grep `llm_client\.complete`).
  Each already has a `producer` (MessageProducer) and `worker_id` in scope. `main()`
  (bottom of file) builds `llm_client = build_llm_client(config)` once and constructs
  `producer` once — this is the ONE choke point for instrumentation; do not touch the 8
  call sites individually.
- `app/message_bus.py` — `build_message`, `MessageProducer.send`, `MessageConsumer`.
  Envelope: `id, from, to, type, payload, timestamp` (UTC ISO-8601).
- `app/llm_client.py` — `build_llm_client(config)` returns an `OllamaClient` or
  `ClaudeClient`, both exposing `.complete(system_prompt, messages) -> str`.
- `app/agent_state.py` — `write_state(state_path, expression, action=, bubble=)`, the
  existing pattern for a pane process reading small per-worker JSON/YAML state.
- `startup.sh` — Xvfb + xterm sized to `RESOLUTION` (default `1920x1080`); `build_layout.py`
  invoked with `--config`; `stream_supervisor.py` invoked with `--resolution "${RESOLUTION}"`.
- `app/stream_supervisor.py` — `build_ffmpeg_cmd(rtmp_url, stream_key, resolution, display)`
  drives `ffmpeg -f x11grab -video_size <resolution> ...` straight to the RTMP output. No
  scaling step exists today — capture resolution IS output resolution.
- `docker-compose.yml` — each worker sets `STREAM_RTMP_URL`/`LAYOUT_PRESET`/etc. via env
  with `.env`-driven overrides; `RESOLUTION` is not currently templated per-worker (each
  worker config's `stream.resolution: "1920x1080"` is the fallback).

## Decisions (settled with the user — do not re-litigate)

1. **Capture resolution**: start at `3840x2160` (4K), configurable (env-overridable, not
   hardcoded), scaled down to the existing `1920x1080` stream output via ffmpeg `-vf scale`.
2. **Thinking pane**: a REAL chain-of-thought step. The LLM is asked to emit a `<thinking>`
   block before its reply IN THE SAME completion call (do not add a second LLM round-trip
   per narration — token cost is already accepted, an extra full API round-trip is not).
   The thinking text is published to the bus as its own message type and the reply text
   (unchanged) flows through every existing call site exactly as before.
3. **Knowledge graph pane**: STATIC PLACEHOLDER data for now. Stub structure only; wiring
   to real campaign-pack data is explicitly a separate future task — do not attempt to
   read Postgres/campaign packs here.
4. **Radar chart stats**: six LIVE model-performance metrics, not D&D stats:
   tokens/sec, avg response latency (s), context tokens used, error/retry rate (%),
   uptime (%), messages sent (count). Tracked per-tick by `agent.py`.
5. **Chat-list "c" pane**: STATIC STUB. Always shows exactly one entry, `all`. No real
   multi-channel Kafka routing — that's future work. Selecting it does nothing yet.
6. **Layout scope**: exactly one new preset, `tuber_base`, applied to all six individual
   worker configs (coder, coder-native, coder-opencode, coder-aider, tester, manager).
   `replay.yaml` preset and `roundtable.yaml` / tuber_0 are UNCHANGED (a worker can still
   flip `LAYOUT_PRESET=replay` at runtime per the existing env-override mechanism — don't
   break that path, just don't redesign it).

## Frozen Contracts (do not change without flagging back)

### A. Metrics file (written by `app/agent_metrics.py`, read by the radar pane)

Path: `<runtime-dir>/metrics_<worker_id>.json` (same dir build_layout.py already writes
resolved pane configs to, default `/tmp/panes` — reuse `write_state`'s directory-creation
pattern, do not invent a second runtime dir).

```json
{
  "worker_id": "coder",
  "updated_at": "2026-09-21T19:00:00+00:00",
  "tokens_per_sec": 12.4,
  "avg_latency_s": 3.2,
  "context_tokens": 5400,
  "error_rate_pct": 4.5,
  "uptime_pct": 100.0,
  "messages_sent": 87
}
```

- `tokens_per_sec` — best-effort estimate: `len(response.split()) / latency_s` for the
  most recent call (no true token counts available across providers) — running average.
- `avg_latency_s` — running average of `complete()` wall-clock duration.
- `context_tokens` — best-effort word-count of the last prompt sent (systemprompt +
  messages), not a true tokenizer count — label it as an estimate in the module docstring.
- `error_rate_pct` — `100 * failed_calls / total_calls` since process start.
- `uptime_pct` — `100 * process_wall_clock_s / process_wall_clock_s` = always 100 while
  alive is not useful; instead track `100 * ticks_without_exception / total_ticks` as a
  simple health proxy the agent loop already has visibility into (see agent.py's main
  loop). Document the exact definition chosen in the module docstring — it must be
  something the existing loop can compute without new plumbing.
- `messages_sent` — running count of every `producer.send(...)` call.
- File is rewritten (not appended) after every LLM completion, atomically (`tmp` +
  `os.replace`) — a pane reading it mid-write must never see a torn/partial file.

### B. `agent_thinking` bus message (published by the same instrumentation wrapper)

```json
{
  "from": "coder",
  "to": "broadcast",
  "type": "agent_thinking",
  "payload": {"text": "<the parsed <thinking> block content, trimmed>"}
}
```

- Added to `kafka_feed`'s default `filters.hide_types` (alongside `heartbeat`,
  `status_update`) in `config/panels/kafka_feed.yaml` — it must not flood the reformatted
  conversation feed.
- `thinking` pane pins its OWN worker's stream: filter `type == agent_thinking AND
  from == <this worker's WORKER_ID>` (each worker's thinking pane shows only that worker's
  own reasoning, not the whole cast's).
- If the LLM response has no parseable `<thinking>...</thinking>` block (model ignored the
  instruction), the wrapper must NOT crash or drop the reply — fall back to: no
  `agent_thinking` message published, full response text used as the narration exactly as
  today. Log at DEBUG, not ERROR.

### C. Conversation-format Kafka feed (`app/tail_bus.py` new render mode)

New `content.format` key in `config/panels/kafka_feed.yaml`: `columns` (today's existing
default, keep working byte-for-byte identical) | `conversation` (new). `tuber_base`'s
kafka_feed placement sets `with: { content: { format: conversation } }`.

Conversation mode output line, exactly:

```
<mmDDyy HH:MM:SS> : <character_name>: <character message>
```

- Timestamp: message envelope's `timestamp`, local time (reuse the existing
  `timestamp.local`/format machinery but the literal format string here is
  `%m%d%y %H:%M:%S`, not the columns mode's configurable one — hardcode this one per the
  user's literal spec).
- `character_name`: resolved via a NEW `content.display_names` map in
  `config/panels/kafka_feed.yaml` (same shape/precedence pattern as the existing
  `content.colors` map — keyed by the message's `from`). Seed it with each worker's own
  `agent.name` value already configured today (KODI-7, NYX-1, OKO-2, ADA-3, TESS-3, MAX-1)
  plus `broadcast: "Broadcast"` / `operator: "Operator"`. Unknown `from` falls back to the
  raw id (never crash on a missing key).
- `character message`: extracted from `payload.narration` if present, else
  `payload.text`, else the message is SKIPPED (not printed as a blank/garbled line) — only
  messages that actually carry human-readable narration show up. `agent_thinking` and
  `status_update`/`heartbeat` types are filtered upstream by `hide_types` regardless.
- Still honors the existing `filters.hide_types`/`show_types`/`direction` pipeline —
  conversation mode is a RENDER change, not a filter-pipeline change.

### D. New panel types (`config/panels/*.yaml` + one script each under `app/`)

| `use` | title | border | command | notes |
|---|---|---|---|---|
| `radar_chart` | `Stats` | green | `python3 /app/radar_pane.py --config {config_path} --metrics-path {runtime_dir}/metrics_{worker_id}.json` (or equivalent resolvable path — coordinate exact placeholder with build_layout.py's existing `{resolved_path}`/`{config_path}` substitution rules, see build_layout.py's `build_context`) | Small ASCII radar/hex plot of the 6 metrics from contract A. Refresh on a loop (~3s). Corner-drawn per the user's sketch — top-left, so it should read fine as a compact box even at ~20% width. |
| `knowledge_graph` | `Knowledge` | blue | `python3 /app/knowledge_graph_pane.py --config {config_path}` | Static placeholder ASCII node/edge graph. Clearly label it "(placeholder)" somewhere in the render so nobody mistakes it for real data later. |
| `thinking` | `Thinking` | yellow | `python3 /app/thinking_pane.py --bus-config {config_path}` | Consumes `agent_thinking` per contract B, prints each as it arrives (plain scrolling text, no TUI, matches `tail_bus.py`'s house style). |
| `chat_list` | `c` | white | `python3 /app/chat_list_pane.py` | Extremely narrow (~3-5 cols). Static: prints `all` (highlighted/reverse-video) once. No loop needed. |

`avatar` and `kafka_feed` (conversation mode, contract C) are REUSED, not new.

### E. `stream_supervisor.py` capture->output scaling

- New `--capture-resolution` CLI flag (default equal to `--resolution` if omitted, so
  existing single-resolution behavior is unchanged unless the new env var is set).
- `build_ffmpeg_cmd` gains a `capture_resolution` param: `x11grab -video_size
  <capture_resolution>` (this must match what Xvfb/xterm actually render at), then a
  `-vf scale=<output_w>:<output_h>` before the encode, where `<resolution>` (existing
  param) is the OUTPUT size (unchanged 1920x1080 default).
- `startup.sh`: new `CAPTURE_RESOLUTION` env var (default `3840x2160`), Xvfb/xterm sized to
  it (`VW`/`VH` derived from `CAPTURE_RESOLUTION` instead of `RESOLUTION`), and
  `stream_supervisor.py` invoked with both `--capture-resolution "${CAPTURE_RESOLUTION}"`
  and the existing `--resolution "${RESOLUTION}"` (still the stream OUTPUT size, unchanged
  default `1920x1080`).
- `docker-compose.yml`: add `CAPTURE_RESOLUTION: ${CAPTURE_RESOLUTION:-3840x2160}` to each
  of the six individual worker env blocks (not the GM/roundtable — leave it on its current
  behavior unless trivial to include consistently; if included, no different from the
  others).

## Components to Build

**Agent instrumentation** (`app/agent_metrics.py`, new; `app/agent.py`, edited at ONE
choke point in `main()`)
- `AgentMetrics` class: running counters + `snapshot()` writing contract A's JSON.
- An `InstrumentedLLMClient` wrapper: wraps the real client from `build_llm_client`,
  implements `.complete(system_prompt, messages)` with the SAME signature/return type
  (a plain string — the narration) so none of the 8 existing call sites in `agent.py`
  change at all. Internally: injects the thinking-block instruction into the system
  prompt, times the call, updates `AgentMetrics`, parses `<thinking>...</thinking>` out of
  the raw response (contract B fallback rule), publishes the `agent_thinking` message via
  the `producer` it's given, writes the metrics snapshot, and returns the reply text with
  the thinking block stripped out.
- Wire it into `main()`: `llm_client = InstrumentedLLMClient(build_llm_client(config),
  producer, worker_id, runtime_dir=...)` right after both `build_llm_client` and
  `producer` exist. Also increment the message-sent counter — either wrap `producer.send`
  too, or have `InstrumentedLLMClient` share the same `AgentMetrics` instance with a thin
  producer wrapper; pick whichever is less invasive to `main()`.
- Tests: `tests/test_agent_metrics.py` — snapshot shape, running averages, error-rate math,
  thinking-block parse + fallback-when-absent, mocked producer/LLM (no real Kafka/HTTP).

**New panes** (`app/radar_pane.py`, `app/knowledge_graph_pane.py`, `app/thinking_pane.py`,
`app/chat_list_pane.py` + matching `config/panels/*.yaml`)
- Plain `print()` stdout loops (NOT full-screen TUI) — must work under xterm + ffmpeg
  capture, matching every existing pane's house style (see `tail_bus.py`, `avatar.py`).
- `radar_pane.py`: tolerant of a missing/stale metrics file (worker just started, no LLM
  call yet) — render zeros/placeholder, never crash or exit.
- Tests: `tests/test_radar_pane.py`, `tests/test_knowledge_graph_pane.py`,
  `tests/test_thinking_pane.py`, `tests/test_chat_list_pane.py` — pure rendering/formatting
  functions unit-tested without a real tmux/Kafka.

**Kafka feed conversation mode** (`app/tail_bus.py`, `config/panels/kafka_feed.yaml`,
edited)
- Add `format: conversation` per contract C, `display_names` map, keep `columns` mode
  byte-identical to today.
- Tests: extend `tests/test_tail_bus_format.py` with conversation-mode cases (narration
  extraction, missing-narration skip, display_names fallback, timestamp format).

**Layout** (`config/layouts/tuber_base.yaml`, new; six `config/workers/*.yaml`, edited;
`docs/panels.md`/`docs/layout_system.md`, updated)
- New preset matching the mock's geometry (narrow left column split radar/knowledge,
  middle column split avatar/thinking, narrow `c` column, right column kafka_feed in
  conversation mode via `with:`). Use `docs/build_layout.md`'s `size`/`target` geometry
  math (see `config/layouts/roundtable.yaml`'s worked comment if you need the percentage
  arithmetic refresher for uneven multi-column splits).
- Repoint `layout.preset` in `config/workers/{coder,coder-native,coder-opencode,
  coder-aider,tester,manager}.yaml` from their current preset to `tuber_base`.
- Tests: `tests/test_tuber_base_layout.py` (mirror `tests/test_roundtable_layout.py`'s
  style) — config in, exact tmux command sequence + resolved pane files out.

**Resolution/scaling** (`app/stream_supervisor.py`, `startup.sh`, `docker-compose.yml`,
edited)
- Per contract E.
- Tests: extend `tests/test_stream_supervisor.py` with capture-vs-output resolution cases
  (ffmpeg command includes both `-video_size <capture>` and `-vf scale=<output>`,
  capture-resolution defaults to resolution when unset).

**Docs**
- `docs/panels.md` — add the four new panel types to the catalog table + a subsection each
  (mirror the existing `kafka_feed` subsection's depth for radar_chart/knowledge_graph/
  thinking/chat_list; keep `avatar`/`kafka_feed` entries, just note the new conversation
  mode in `kafka_feed`'s subsection).
- `docs/layout_system.md` — add `tuber_base` to any preset lists/examples.
- New `docs/agent_metrics.md` (per CLAUDE.md's one-doc-per-module rule) for
  `app/agent_metrics.py`, following that file's existing doc template (Overview,
  Signature, Parameters, Return Value, Dependencies, Usage Examples, Error Handling,
  Changelog — see `docs/tail_bus.md`/`docs/message_bus_feed.md` if present, or
  `docs/build_layout.md`, for the house format).
- README — Project Structure section, one line if a new top-level concept is introduced
  (it isn't; skip unless something genuinely needs it).

## Delegation Plan

Four streams. A and B are fully independent of each other and of C. D depends on A's
metrics-file contract existing (frozen above, so D can build against the CONTRACT without
waiting on A's actual code landing) but should verify against A's real output once both are
done. E (docs + integration dry-run) runs last, after A-D land.

- **Sub-agent A — Agent instrumentation**: `app/agent_metrics.py`,
  `tests/test_agent_metrics.py`, the one-choke-point edit to `app/agent.py`'s `main()`.
  Owns contracts A and B. Do not touch any of the 8 `llm_client.complete(...)` call sites.
- **Sub-agent B — New panes + kafka conversation mode**: `app/radar_pane.py`,
  `app/knowledge_graph_pane.py`, `app/thinking_pane.py`, `app/chat_list_pane.py`,
  `config/panels/{radar_chart,knowledge_graph,thinking,chat_list}.yaml`, the `tail_bus.py`
  conversation-mode addition + `config/panels/kafka_feed.yaml` `display_names`/`format`
  fields, and all five panes' tests. Owns contracts C and D. Build `radar_pane.py` against
  contract A's frozen JSON shape — don't block on sub-agent A's code landing first.
- **Sub-agent C — Resolution/scaling**: `app/stream_supervisor.py`, `startup.sh`,
  `docker-compose.yml`, `tests/test_stream_supervisor.py`. Owns contract E. Fully
  independent of A/B/D.
- **Sub-agent D — Layout + worker config wiring**: `config/layouts/tuber_base.yaml`, the
  six `config/workers/*.yaml` preset repoints, `tests/test_tuber_base_layout.py`. Needs to
  know panel `use` names from contract D's table (frozen above) but does NOT need B's
  actual panel scripts to exist to write/test the layout composition — `build_layout.py`
  only resolves `config/panels/<use>.yaml` files existing with the right shape. Coordinate:
  if B's panel yaml files aren't landed yet when D runs, D should create minimal
  placeholder `config/panels/*.yaml` stubs (title/border/command per the table) so its own
  tests pass, and B's later edits fill in any panel-specific `content:` blocks — flag this
  explicitly in D's summary so the orchestrator confirms B didn't overwrite D's stub with a
  conflicting command line.
- **Sub-agent E — Docs + integration** (after A-D land): `docs/panels.md`,
  `docs/layout_system.md`, `docs/agent_metrics.md`, and a dry-run of
  `python3 app/build_layout.py --config config/workers/coder.yaml --panels-dir config/panels
  --layouts-dir config/layouts --runtime-dir /tmp/panes_dryrun` to confirm the emitted tmux
  sequence is valid and every resolved pane file is well-formed. Also run the FULL test
  suite (`pytest`) and report pass/fail — fix any integration seam a stream missed (e.g. a
  placeholder command signature mismatch) rather than leaving it broken.

## Acceptance Criteria

- `config/layouts/tuber_base.yaml` exists and matches the mock's geometry: narrow left
  column (radar/knowledge split), middle column (avatar/thinking split), narrow `c` column,
  right column kafka feed.
- All six individual worker configs (`coder`, `coder-native`, `coder-opencode`,
  `coder-aider`, `tester`, `manager`) use `layout.preset: tuber_base`. `roundtable`/`replay`
  presets and `tuber_0`/GM are unchanged.
- The Kafka pane in `tuber_base` renders `<mmDDyy HH:MM:SS> : <character_name>: <message>`
  lines with no visible envelope/JSON, using each worker's real configured `agent.name`.
- The thinking pane shows real per-worker chain-of-thought text sourced from a genuine
  `<thinking>` block the LLM is asked to produce, published as `agent_thinking` and hidden
  from the main kafka feed.
- The radar pane plots six live metrics (tokens/sec, avg latency, context tokens, error
  rate, uptime, messages sent) from a per-worker metrics file `agent.py` actually updates.
- The knowledge-graph pane renders a clearly-labeled static placeholder.
- The chat-list pane renders a static `all` entry only.
- `startup.sh` captures at a configurable resolution (default 3840x2160) and
  `stream_supervisor.py` scales down to the existing 1920x1080 stream output via ffmpeg.
- `pytest` passes for the full suite, including every new/changed test file listed above.
- New/changed code has docs per CLAUDE.md (one doc per new module); `docs/panels.md` and
  `docs/layout_system.md` reflect the new preset/panels.

## Guardrails

- Do not touch `config/layouts/roundtable.yaml`, `config/workers/tuber_0.yaml`, or any
  roundtable/tile code path.
- Do not remove or change the existing `columns` kafka-feed render mode's behavior/tests —
  it's still the default for anyone not on `tuber_base`.
- Do not add a second LLM round-trip per narration for the thinking block — one completion
  call, parsed into thinking+reply.
- Do not wire the knowledge-graph pane to any real data source (Postgres/campaign packs) —
  explicitly future work.
- Do not touch any of the 8 `llm_client.complete(...)` call sites in `agent.py` directly —
  all instrumentation goes through the one choke point in `main()`.
- Keep every new pane a plain stdout `print()` loop — no full-screen TUI (must survive
  xterm + ffmpeg capture, matches every existing pane).
- Preserve env-over-file override precedence everywhere (matches the rest of this project's
  config conventions).
- Follow CLAUDE.md: structured logging, one doc per module, pytest with mocked externals
  (no real Kafka/Postgres/HTTP in tests).
