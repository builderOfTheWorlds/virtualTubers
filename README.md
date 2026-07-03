# virtualTubers

## Summary

virtualTubers is an autonomous AI-powered VTuber streaming system where a team of AI agents (Manager, Coder, Tester) act as a live software development team. Each agent runs in its own Docker container, has its own personality and ASCII-art avatar, works inside a live terminal session (tmux + neovim/htop/etc.), and streams that session to Twitch over RTMP via ffmpeg. It's for anyone who wants to run an always-on, config-driven "AI dev team" stream without hand-building the streaming pipeline from scratch.

The project is early-stage: the terminal avatar (`app/avatar.py`) is still a stub that keeps the container alive and cycles through expressions on a timer. The agent brain (`app/agent.py`) now has a real perceive/think/act slice — it publishes heartbeats every tick and dispatches every incoming message type through role-gated handlers backed by a provider-switchable LLM (Ollama or Claude): the coder narrates a task and hands the commit to the tester, the tester reports `test_passed`/`bug_report` to the manager, and the manager re-delegates fixes (bounded at 3 retries) or reports back to the operator. Writing real code and running real tests are still ahead — see the Phase 1 roadmap in the architecture doc.

See [docs/VTuber_AI_Dev_Team_Concept.md](docs/VTuber_AI_Dev_Team_Concept.md) for the full architecture and design plan.

## Recent Changes

**Container logs now ship to Postgres too** — `services/log-shipper/` (new)
follows the stdout/stderr of every container in this project's docker-compose
stack (discovered via a read-only Docker socket mount) and inserts each line
into a `container_logs` table, alongside the existing `messages` table from
`message-logger`. This means all of this project's container logs — workers,
`message-logger`, `message-api`, etc. — can be reviewed with a single SQL
query instead of `docker logs` per container. Ships new lines only; no
historical backfill. See [docs/log_shipper.md](docs/log_shipper.md) for
details, including a security note on the Docker socket mount.

**Workers now collaborate as a team — coder → tester → manager → operator** —
`app/agent.py` dispatches all 8 message types from the concept doc (§3.4) via a
`MESSAGE_HANDLERS` table, not just `task_assignment`:

- The coder still replies `task_complete`, but now also hands its commit to the
  tester (`commit_notification`); the tester "runs the suite" (a weighted-random
  stub for now — no real test execution yet) and reports `test_passed` or
  `bug_report` to the manager.
- The manager reports back to the operator with a new `manager_report` message
  type (payload `report_type: milestone | blocker | escalation`) — celebrating
  passing suites, escalating blockers and stuck bugs.
- The bug ↔ fix loop is bounded: a `retry_count` travels in the message payloads
  around the whole loop, and after 3 retries (`MAX_BUG_RETRIES`) the manager
  escalates to the operator instead of re-delegating another fix.
- Any worker answers a direct `operator_message` (message-api's default type)
  with a new `operator_reply` type addressed to `operator`; `retest_request` is
  an operator lever via `message-api` (nothing sends it automatically yet).
- Handlers are role-gated on the worker config's `agent.role` — a message type
  arriving at the wrong role logs and no-ops. The Kafka feed pane highlights the
  new traffic (`bug_report` red, `test_passed` green, `manager_report` cyan,
  `operator_reply` blue). See [docs/agent.md](docs/agent.md) for details.
- Full reference of what the operator can send (`task_assignment`,
  `operator_message`, plus manual/debug injections for every stage of the
  pipeline) is now written up in
  [docs/operator_commands.md](docs/operator_commands.md).

**Workers can now act as agents — LLM-driven task narration** — `app/agent.py` is no
longer a heartbeat-only stub:

- `app/llm_client.py` (new) — provider-switchable LLM client (`llm.provider: ollama | claude`
  in a worker's config, or `LLM_PROVIDER` env override). Ollama goes through a plain
  `httpx` call to `/api/chat`; Claude goes through the official `anthropic` SDK,
  which reads credentials from `ANTHROPIC_API_KEY` — never from the config file.
- `app/agent.py` — on receiving a `task_assignment` message, calls the LLM with
  the worker's `agent.system_prompt` and the task, then replies on the bus with
  `task_complete` (or `clarification_request` if the LLM call fails) — the
  narration shows up in the worker's console output and the Kafka feed pane.
- To send a worker an instruction, POST a `task_assignment` to `message-api`
  (see [Inter-agent messaging](#inter-agent-messaging-kafka) below) — no new
  endpoint needed, this is the same `message-api` used for test injection.
- `requirements.txt` gained `anthropic`; `.env.example` gained `ANTHROPIC_API_KEY`
  (only required when a worker's config sets `llm.provider: claude`).
- This does not yet write files, run commands, or touch the shared repo — see
  [docs/VTuber_AI_Dev_Team_Concept.md](docs/VTuber_AI_Dev_Team_Concept.md) Phase 1
  for what's next. See [docs/agent.md](docs/agent.md) and
  [docs/llm_client.md](docs/llm_client.md) for details.

**Config-driven modular tmux panels + rich Kafka message feed** — the worker's
tmux layout is no longer hardcoded in `startup.sh`; it is now declarative,
ConfigMap-friendly config:

- `app/build_layout.py` (new) — layout engine. Resolves a worker's chosen preset
  (`config/layouts/<preset>.yaml`) against reusable panel-type defaults
  (`config/panels/*.yaml`), writes each pane's resolved config to `/tmp/panes/<id>.yaml`,
  and emits the tmux command sequence; `startup.sh` now just runs
  `eval "$(python3 /app/build_layout.py --config "$CONFIG_PATH")"`
- `config/panels/{kafka_feed,avatar,filetree,editor,htop}.yaml` (new) — panel-type
  defaults; `config/layouts/{coder,tester,manager}.yaml` (new) — per-role composition presets
- `config/worker.yaml` + `config/workers/*.yaml` now select a preset via
  `layout.preset` (the dead `layout.variant` block was removed); `LAYOUT_PRESET` env overrides
- `app/tail_bus.py` — rewritten into a rich, filterable feed: colorized sender,
  aligned columns, TYPE highlighting, truncated payloads, and heartbeat filtering
  (the per-tick flood arrives as type `status_update`, hidden by default)
- Reorder/resize/disable any pane with a **config-only** change — no `startup.sh`
  or image rebuild

See [docs/layout_system.md](docs/layout_system.md), [docs/panels.md](docs/panels.md),
[docs/build_layout.md](docs/build_layout.md), and [docs/message_bus_feed.md](docs/message_bus_feed.md).

**Kafka message bus + Postgres logging + HTTP test-injection API** — the inter-agent message bus moved from a plain file (`/data/world-state/messages/bus.log`) to Kafka:

- `app/message_bus.py` (new) — shared envelope/producer/consumer helper used by agents and the new services
- `app/agent.py` — now actually parses its mounted config (previously ignored it — every worker silently ran as `worker_id: "worker"`), publishes heartbeats as real Kafka messages, and has a minimal `perceive()` that prints messages addressed to it
- `app/tail_bus.py` (new) — replaces the `tail -f bus.log` tmux pane with a live Kafka consumer
- `services/message-logger/` (new) — durably logs every bus message to Postgres
- `services/message-api/` (new) — `POST /messages` on port `8090` for injecting test messages onto the bus, see [docs/message_api.md](docs/message_api.md)
- `config/*.yaml` gained a `message_bus` section (bootstrap servers, topic, worker ID); `docker-compose.yml` gained the two new services plus `WORKER_ID`/`KAFKA_*` env vars per worker
- Fixed a pre-existing YAML syntax bug (`frustrated:{` missing a space) in all three role configs that would have broken the new config-parsing on startup

See [docs/message_bus.md](docs/message_bus.md), [docs/message_logger.md](docs/message_logger.md), and [docs/message_api.md](docs/message_api.md) for details.

## Prerequisites

- Docker and Docker Compose
- An RTMP destination — a Twitch stream key for live streaming, or a local RTMP preview server (bundled via `rtmp-preview` in `docker-compose.yml`) for local testing
- (Optional) A running [Ollama](https://ollama.ai) instance for local LLM inference — the default worker config points at `http://localhost:11434`
- (Optional) An [Anthropic API key](https://console.anthropic.com/) if any worker's config sets `llm.provider: claude` instead of `ollama`
- A reachable Kafka broker (agents/services publish and consume inter-agent messages there) and a Postgres instance (every message is durably logged there) — neither is bundled in `docker-compose.yml`; point at existing instances via `.env`

## Installation

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd virtualTubers
   ```
2. Build the worker image (the `docker-compose.yml` expects a locally-built image and never pulls):
   ```bash
   docker build -t vtube-worker:latest .
   ```
3. Copy `.env.example` to `.env` and fill in your stream keys, Kafka bootstrap servers, and Postgres credentials:
   ```bash
   cp .env.example .env
   ```
   ```bash
   CODER_STREAM_KEY=your_twitch_stream_key
   MANAGER_STREAM_KEY=your_twitch_stream_key
   TESTER_STREAM_KEY=your_twitch_stream_key
   STREAM_RTMP_URL=rtmp://live.twitch.tv/app   # omit to use the local rtmp-preview server

   KAFKA_BOOTSTRAP_SERVERS=your_kafka_host:9092
   KAFKA_TOPIC=vtuber.messages

   POSTGRES_HOST=your_postgres_host
   POSTGRES_PORT=5432
   POSTGRES_DB=your_db
   POSTGRES_USER=your_user
   POSTGRES_PASSWORD=your_password
   ```
   `.env` is gitignored — never commit real credentials.

## Usage

Start the full stack (three workers + message-logger + message-api + Redis + local RTMP preview):

```bash
docker compose up
```

This launches three worker containers — `worker-coder`, `worker-manager`, `worker-tester` — plus `message-logger`, `message-api`, a shared `redis` instance, and an `rtmp-preview` server for local testing. Each worker:

1. Boots a virtual display (Xvfb) and PulseAudio sink
2. Lays out a tmux session (file tree, ASCII avatar, editor/output pane, agent chat log, htop)
3. Opens that session in xterm on the virtual display
4. Starts the agent loop (`app/agent.py`), which publishes heartbeats, consumes messages addressed to it over the Kafka bus, and dispatches each one to its role's handler — narrating every step via its configured LLM as work flows coder → tester → manager → operator (see [docs/agent.md](docs/agent.md))
5. Captures the display with ffmpeg and pushes it out over RTMP to the configured stream key

To preview locally without a real Twitch key, leave `STREAM_RTMP_URL` unset (it defaults to `rtmp://rtmp-preview:1935/live`) and view the stream with a player like VLC pointed at `rtmp://localhost:1935/live/<stream_key>`.

### Shelling into a running container

To poke around inside a running worker (check logs, inspect config, debug tmux panes), exec into it directly — no need to stop/restart anything. Since no `container_name` is pinned in `docker-compose.yml`, Compose auto-names containers `<project>-<service>-<n>`; under Portainer that's typically the `virtualtubers-` project prefix:

```bash
docker exec -it virtualtubers-worker-coder-1 bash
```

Swap `worker-coder` for `worker-manager`, `worker-tester`, `message-logger`, `message-api`, or `log-shipper` as needed. Run `docker ps` first if you're unsure of the exact name/suffix on your host.

### Inter-agent messaging (Kafka)

Agents talk to each other over a Kafka topic (`vtuber.messages` by default) instead of a file — see `docs/message_bus.md`. Every message is durably logged to Postgres by the `message-logger` service (`docs/message_logger.md`).

To send a worker an instruction (or inject a test message), use the `message-api` HTTP service (`docs/message_api.md`), exposed on port `8090`:

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "say hello"}}'
```

The `coder` worker's agent loop picks up the message, calls its configured LLM (`llm.provider` in `config/workers/coder.yaml`) with its system prompt and the task, and replies with `task_complete` — then hands the commit to the tester (`commit_notification`), whose `test_passed`/`bug_report` verdict flows on to the manager and, as a `manager_report`, back to the operator. The whole exchange is visible in each worker's console output and the tmux "agent chat"/Kafka feed pane — see [docs/agent.md](docs/agent.md). To point a worker at Claude instead of Ollama, set that worker's `llm.provider: claude` and export `ANTHROPIC_API_KEY`.

For the full list of commands an operator can send (task assignment, direct chat, and manual/debug injections for every pipeline stage), see [docs/operator_commands.md](docs/operator_commands.md).

To run a single worker outside Docker for quick iteration on `app/agent.py` or `app/avatar.py`:

> **Always use the project's `.venv` for local development — never install packages into or run scripts against the global/system Python on this machine.** Create it once with `python -m venv .venv`, then activate it before installing dependencies or running anything.

```bash
python -m venv .venv          # first time only
.venv\Scripts\activate         # Windows (use `source .venv/bin/activate` on macOS/Linux)
pip install -r requirements.txt
python3 app/avatar.py --config config/workers/coder.yaml
```

## Deployment (Portainer)

In production the stack is managed by **Portainer** (the repo is checked out on the
host, e.g. `/opt/virtualTubers`). Two things differ from a plain `docker compose`
workflow and cause most "it won't pick up my change" confusion:

**1. Portainer stack env vars are NOT the CLI `.env` file.**
Values set in the stack's **Environment variables** panel are injected by Portainer
(as a `stack.env`) **only when you deploy/redeploy through the Portainer UI**. A
manual `docker compose up -d` run from the host reads the local `.env` file instead
and ignores the Portainer values. **Pick one mechanism and stick with it** — if the
stack lives in Portainer, set env vars there and redeploy there; don't recreate
containers from the CLI.

**2. The worker image is never built by the stack.**
The three workers use `image: vtube-worker:latest` with `pull_policy: never`, so
Portainer will **not** build or pull it. You must build it on the host after any
code change, then redeploy the stack so the containers pick up the new image.

### Required stack environment variables

Set these in the Portainer stack's **Environment variables** panel. Each worker
streams to its **own** Twitch channel, so each needs that channel's key:

| Variable | Example | Notes |
|---|---|---|
| `STREAM_RTMP_URL` | `rtmp://live.twitch.tv/app` | Omit/empty → falls back to the bundled local `rtmp-preview` |
| `CODER_STREAM_KEY` | `live_xxxxxxxx` | Coder channel's Twitch stream key |
| `MANAGER_STREAM_KEY` | `live_yyyyyyyy` | Manager channel's key |
| `TESTER_STREAM_KEY` | `live_zzzzzzzz` | Tester channel's key |
| `LLM_BASE_URL` | `http://host:11434` | Ollama endpoint |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Only needed if a worker's config sets `llm.provider: claude` |
| `KAFKA_BOOTSTRAP_SERVERS` | `192.168.1.120:9092` | Message-bus broker |
| `KAFKA_TOPIC` | `vtuber.messages` | |
| `POSTGRES_HOST` … `POSTGRES_PASSWORD` | | `message-logger` Postgres connection |

> Set each variable as its own `name` → `value` pair. Don't put a URL (or any value)
> in the `name` field — that just creates a junk variable nothing reads.

### Deploy / redeploy after a code change

On the host (the repo checkout, e.g. `/opt/virtualTubers`):

```bash
git pull                                 # get the latest code
docker build -t vtube-worker:latest .    # rebuild the worker image (NOT `docker compose build`)
```

Then in the **Portainer UI** → **Stacks** → this stack → **Update the stack**,
enabling **Re-pull image and redeploy** / force recreate. Portainer recreates the
workers on the freshly built image using the current stack env vars.

> Env-only change (e.g. a new stream key)? Skip `docker build` — just **Update the
> stack** in Portainer to re-inject the env and recreate the containers.

### Verify a worker is streaming to the right place

Compose prefixes container names with the project, so they are
`virtualtubers-worker-coder-1`, `-manager-1`, and `-tester-1`:

```bash
# What env did the container actually receive?
docker exec virtualtubers-worker-coder-1 env | grep -E 'STREAM_RTMP_URL|STREAM_KEY'

# Where is ffmpeg pushing? (should be your Twitch ingest, not rtmp-preview)
docker logs virtualtubers-worker-coder-1 2>&1 | grep -a 'ffmpeg broadcaster'

# Full startup, minus the agent heartbeat spam:
docker logs virtualtubers-worker-coder-1 2>&1 | grep -avE '\[agent' | tail -40
```

A healthy worker logs
`[startup] Starting ffmpeg broadcaster → rtmp://live.twitch.tv/app/<key>` followed
by ffmpeg `frame= … speed=~1x` progress lines. If it shows
`rtmp://rtmp-preview:1935/live/...`, `STREAM_RTMP_URL` didn't reach the container
(see gotcha #1 above).

## Configuration

All runtime behavior is config-driven — no code changes needed to retune an agent.

- `config/worker.yaml` — the annotated template/default worker config (role, name, system prompt, LLM/voice/avatar/stream/world-state/message-bus settings)
- `config/workers/coder.yaml`, `manager.yaml`, `tester.yaml` — per-role configs mounted into each container at `/config/worker.yaml`
- Environment variables (set via `docker-compose.yml` or `.env`) override config file values at runtime, notably: `STREAM_RTMP_URL`, `CODER_STREAM_KEY` / `MANAGER_STREAM_KEY` / `TESTER_STREAM_KEY`, `LLM_BASE_URL`, `DISPLAY_NUM`, `WORKER_ID`, `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`, `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`

Key sections inside a worker config:

| Section | Controls |
|---|---|
| `agent` | Role, display name, system prompt, tick rate, context window |
| `llm` | Provider (`ollama` \| `claude`), base URL, model, temperature |
| `voice` | TTS provider (`elevenlabs` \| `kokoro` \| `null`), voice ID, verbosity |
| `avatar` | Name, title, ASCII expression states, speech bubble sizing |
| `layout` | Which tmux layout preset to use (`layout.preset`: `coder` \| `tester` \| `manager`; `LAYOUT_PRESET` env overrides). Presets live in `config/layouts/`; reusable panel-type defaults in `config/panels/`. Optional per-pane overrides under `layout.panes.<id>`. |
| `stream` | RTMP URL/key, resolution, bitrate, fps |
| `world_state` | Shared state backend (`file` \| `redis`) and connection info |
| `message_bus` | Kafka backend, bootstrap servers, topic, and this worker's ID |

### Tmux layout (config-driven)

The worker's tmux panes are declarative config, not baked into `startup.sh`. A
worker config picks a preset (`layout.preset`) from `config/layouts/*.yaml`, which
places and sizes reusable panel types from `config/panels/*.yaml`. **Reorder,
resize, retitle, or disable a pane by editing config only** — no `startup.sh` edit
or image rebuild. The rich Kafka "Message Bus" feed pane (`config/panels/kafka_feed.yaml`)
is configured the same way (colors, type filters, payload controls). See
[docs/layout_system.md](docs/layout_system.md) and [docs/panels.md](docs/panels.md).

The layered config maps directly onto **Kubernetes ConfigMaps** — `config/panels/`
becomes one shared ConfigMap, each `config/layouts/*.yaml` a small per-role
ConfigMap; reconfigure a role by editing its layout ConfigMap and restarting the
pod. Details in [docs/layout_system.md](docs/layout_system.md#kubernetes-configmap-mapping).

## Project Structure

```
virtualTubers/
├── app/
│   ├── agent.py          # Agent loop (perceive/think/act): heartbeats + LLM-driven task narration
│   ├── llm_client.py     # Provider-switchable LLM client (Ollama | Claude)
│   ├── avatar.py         # Terminal ASCII avatar renderer — expression + speech bubble driven by agent_state.py
│   ├── agent_state.py    # Small local state file bridging agent.py's activity to avatar.py's display
│   ├── build_layout.py   # Config-driven tmux layout engine (emits the tmux command sequence)
│   ├── tmux_control.py   # Agent's "hands": select a pane by name, type text/commands into it
│   ├── message_bus.py    # Shared Kafka producer/consumer/schema helper
│   └── tail_bus.py       # Rich configurable Kafka feed for the tmux "Message Bus" pane
├── services/
│   ├── message-logger/    # Consumes every bus message, logs it to Postgres
│   └── message-api/       # FastAPI service for injecting test messages onto the bus
├── config/
│   ├── worker.yaml        # Annotated default/template worker config (selects a layout preset)
│   ├── workers/           # Per-role configs (coder.yaml, manager.yaml, tester.yaml)
│   ├── panels/            # Reusable panel-TYPE defaults (kafka_feed, avatar, filetree, editor, htop)
│   └── layouts/           # Composition presets that place & size panels (coder, tester, manager)
├── docs/
│   ├── VTuber_AI_Dev_Team_Concept.md   # Full architecture & roadmap doc
│   ├── agent.md, llm_client.md         # Agent loop and LLM client docs
│   ├── layout_system.md, panels.md, build_layout.md   # Config-driven panel system
│   ├── message_bus.md, message_bus_feed.md, message_logger.md, message_api.md   # Per-module docs
├── tests/                  # pytest suite (agent, llm_client, message_bus, message-api, build_layout, tail_bus)
├── Dockerfile              # Worker container image (Xvfb, tmux, ffmpeg, Python, etc.)
├── docker-compose.yml      # Local dev stack: 3 workers + message-logger + message-api + Redis + RTMP preview
├── startup.sh              # Container entrypoint: sets up display, tmux layout, avatar, agent loop, and ffmpeg broadcaster
├── requirements.txt        # Python dependencies (worker image)
└── .env.example            # Template for stream keys, Kafka, and Postgres config
```

## License

This project is licensed under the GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
