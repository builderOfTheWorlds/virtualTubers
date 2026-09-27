# virtualTubers

## Summary

virtualTubers is an autonomous AI-powered VTuber streaming system where a team of AI agents (Manager, Coder, Tester) act as a live software development team. Each agent runs in its own Docker container, has its own personality and ASCII-art avatar, works inside a live terminal session (tmux + neovim/htop/etc.), and streams that session to Twitch over RTMP via ffmpeg. It's for anyone who wants to run an always-on, config-driven "AI dev team" stream without hand-building the streaming pipeline from scratch.

The project is early-stage but the core loops are real: the agent brain (`app/agent.py`) has a perceive/think/act slice — it refreshes a Redis liveness key every tick (plus a rate-limited `status_update` bus heartbeat) and dispatches every incoming message type through role-gated handlers (`app/agent_handlers/`) backed by a provider-switchable LLM (Ollama or Claude): the coder narrates a task and hands the commit to the tester, the tester reports `test_passed`/`bug_report` to the manager, and the manager re-delegates fixes (bounded at 3 retries) or reports back to the operator, and every message in one task's chain shares a `correlation_id`. The manager can also run the show on its own from an opt-in **task backlog** (a task file or Gitea issues), pulling the next task whenever the team is idle. Coders write real code through swappable backends (native / OpenCode / aider) and the tester really runs pytest against their workspaces. On top of that sits **Rerun Theater**: past real Claude Code dev sessions replay as paced, redacted shows — now with per-airing, two-voice **spoken narration** (boss + coder via local TTS) synchronized to the on-screen action. Generated episodes can arrive as **drafts** that never air until an operator approves them in the control panel, and the operator has a **worker health view** (alive / stale / down per worker) plus a Docker-only **emergency kill switch** that takes streams off air even when Redis or message-api is down. The terminal avatar (`app/avatar.py`) now has a **parametric character generator**: a worker's 3D head is generated from a flat set of 0..1 sliders (jaw width, eye size, ear size, ...) that an AI agent can iterate on itself via an ASCII preview loop — Chadwick (the coder worker) is the first character running it, see [docs/character_generator.md](docs/character_generator.md). See the Phase 1 roadmap in the architecture doc for what's next.

See [docs/VTuber_AI_Dev_Team_Concept.md](docs/VTuber_AI_Dev_Team_Concept.md) for the full architecture and design plan.

**AI agents working in this repo:** read [docs/agent_flow_reference.md](docs/agent_flow_reference.md) next — a text-only map of every component, message type, feature flow, and invariant, with file paths. Diagram versions: [docs/feature_flow_diagram.md](docs/feature_flow_diagram.md) (features) and [docs/architecture_flow_diagram.md](docs/architecture_flow_diagram.md) (data ownership).

## Changelog

Dated write-ups of every feature and fix live in **[CHANGELOG.md](CHANGELOG.md)**
(newest first) — moved out of this file so the README stays a quick
orientation rather than a running history. Latest entry (2026-09-27): agent
handlers split into `app/agent_handlers/`, correlation IDs across task
chains, Redis liveness + a worker health view, a local emergency kill
switch, a draft review gate with opt-in generator auto-submit, race-safe
relay files (`app/relay_io.py`), an opt-in manager task backlog, and
end-to-end flow tests.

## Prerequisites

- Docker and Docker Compose
- An RTMP destination — a Twitch stream key for live streaming, or a local RTMP preview server (bundled via `rtmp-preview` in `docker-compose.yml`) for local testing
- (Optional) A running [Ollama](https://ollama.ai) instance for local LLM inference — the default worker config points at `http://localhost:11434`
- (Optional) An [Anthropic API key](https://console.anthropic.com/) if any worker's config sets `llm.provider: claude` instead of `ollama`
- (Optional) Piper voice models for spoken replay narration — fetched with `scripts/download_voices.py`, see [Rerun Theater](docs/usage.md#rerun-theater--replaying-past-sessions-with-voices)
- A Kafka broker (agents/services publish and consume inter-agent messages there) and a Postgres instance (every message is durably logged there). `docker-compose.yml` bundles both as **opt-in profiles**: `COMPOSE_PROFILES=local-infra` starts a local `kafka`, `local-postgres` a local `postgres` (use both for a fully standalone host, then set `KAFKA_BOOTSTRAP_SERVERS=kafka:9092` / `POSTGRES_HOST=postgres`). Without a profile, point `.env` at existing instances. Redis is always bundled

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
   TUBER1_STREAM_KEY=your_twitch_stream_key
   TUBER6_STREAM_KEY=your_twitch_stream_key
   TUBER5_STREAM_KEY=your_twitch_stream_key
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

## Git Remotes & GitHub Mirror

This repo pushes to a homelab Gitea instance (`origin`), which auto-mirrors
every push to GitHub (`github`, read-only) within seconds. Full remote URLs,
the mirror credential's expiry date, and health-check commands are in
**[docs/git_remotes.md](docs/git_remotes.md)**.

## Usage

Start the full stack:

```bash
docker compose up
```

This launches eight worker containers — `worker-coder`, `worker-coder-native`, `worker-coder-opencode`, `worker-coder-aider`, `worker-manager`, `worker-tester`, `worker-gm` (the GM's channel, `tuber_0`) and `worker-roundtable` (the roundtable show) — plus `message-logger`, `message-api`, `control-panel`, `twitch-presence`, `log-shipper`, `campaign-manager`, the offline `3layer-generator` (+ its own Postgres), a shared `redis` instance, and an `rtmp-preview` server for local testing (Kafka/Postgres only with the profiles above). Each worker boots a virtual display and tmux session, starts the agent loop (narrating work as it flows coder → tester → manager → operator over the Kafka bus), and streams that session out over RTMP.

To preview locally without a real Twitch key, leave `STREAM_RTMP_URL` unset (it defaults to `rtmp://rtmp-preview:1935/live`) and view the stream with a player like VLC pointed at `rtmp://localhost:1935/live/<stream_key>`.

Send a worker an instruction via `message-api` (port `8090`):

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "say hello"}}'
```

Or drive the same controls — worker on/off and health, log filtering, message
injection, log pruning, the Rerun Theater replay library and its "Drafts
awaiting review" queue (Approve / Reject) — from a browser at
**http://localhost:8091** (`control-panel`, docs/control_panel.md), no curl
required.

Check which workers are alive (Redis liveness key; `state` is `alive`,
`stale`, `down` or `unknown`, plus whether the local kill switch is engaged):

```bash
curl http://localhost:8090/workers/health
```

Emergency stop — takes worker streams off air using only Docker (works with
Redis or message-api down), and resume:

```bash
scripts/emergency_stop.sh              # all running worker-* containers
scripts/emergency_stop.sh coder gm     # specific workers
scripts/emergency_resume.sh            # remove the kill file again
```

From a Windows PC, `scripts/emergency_stop.ps1` / `emergency_resume.ps1` run
the same thing over SSH on the host. The kill file survives `docker restart`
but not container re-creation — see [docs/worker_control.md](docs/worker_control.md).

For everything else — shelling into a container, the full inter-agent
messaging protocol, pausing/resuming a worker, running Rerun Theater (solo
shows and multi-worker duets, with spoken narration), and local development
outside Docker — see **[docs/usage.md](docs/usage.md)**.

## Deployment (argyre, via Portainer)

The stack runs on **argyre** (hostname `gx10-35a4`, `192.168.1.23`), managed
as the Portainer stack `virtualtubers` from the checkout at
`/home/secus/codeProjects/virtualTubers`. Kafka and Redis run in the stack;
Postgres is external on mafober (`192.168.1.120:5432`). It previously ran on
**d2000** (Windows, Docker Desktop, plain `docker compose`).

**The worker image is never built by `docker compose up`** — every worker
uses `image: vtube-worker:latest` with `pull_policy: never`, so a plain
`docker compose up -d` will not build or pull it; it just fails or runs a
stale image. You must build it on the host after any code change
(`docker build -t vtube-worker:latest .`, or `./install.sh` for every image),
then recreate the containers (`./redeploy.sh` does both). This is the #1
cause of "it won't pick up my change" confusion on this project.

Full required-env-var table, the build-and-deploy steps, and how to verify a
worker is streaming to the right place:
**[docs/deployment.md](docs/deployment.md)**.

## Configuration

All runtime behavior is config-driven — no code changes needed to retune an
agent. `config/worker.yaml` is the canonical annotated template of every
worker parameter that exists; per-role configs (`config/workers/*.yaml`)
only ever override a subset of it, and environment variables override those
at runtime.

Full config-section reference, worker on/off control internals, and the
config-driven tmux layout system (which maps directly onto Kubernetes
ConfigMaps): **[docs/configuration.md](docs/configuration.md)**.

Newer knobs, all safe by default:

- `agent.liveness_ttl_s`, `agent.bus_heartbeat_every` (default 12) — liveness
  key TTL and how often the `status_update` bus heartbeat is sent.
- `agent.backlog.*` (off) — the manager's task backlog; `GITEA_TOKEN` env for
  the Gitea source ([docs/task_backlog.md](docs/task_backlog.md)).
- `worker_control.kill_file` / `WORKER_KILL_FILE` — emergency kill-file path
  ([docs/worker_control.md](docs/worker_control.md)).
- `AUTO_SUBMIT_DRAFTS` (off) / `AUTO_SUBMIT_TIMEOUT_S` on `3layer-generator`
  — post finished publish jobs as review drafts
  ([docs/draft_submitter.md](docs/draft_submitter.md)).
- Feed pane `content.correlation.show` (off) — correlation tag column
  ([docs/message_bus_feed.md](docs/message_bus_feed.md)).

### One voice at a time (voice gate)

On the roundtable channel every tile *and* the director feed one shared
Pulse sink, so by default **only one voice line can sound at a time** — a
new line physically cannot start before the previous one has finished
(`fcntl.flock` seat files shared by the container's processes; crash-safe,
never withholds audio). Deliberate overlap is a per-episode config escape
hatch: `show.audio.max_concurrent: 2..7` (+ `line_gap_s`) in the episode
header, or `VOICE_GATE_CONCURRENT` at runtime —
**[docs/voice_gate.md](docs/voice_gate.md)** (rules, escape hatch, event log
for reconstructing the on-air sequence).

### Saving replays to file (5 GB cap)

Rerun Theater's Play has a **Save to file** option (off / roundtable / all 7
streams). It records the exact encoded stream sent to Twitch (a zero-re-encode
tee tap), into host `./recordings/<id>/<worker>.mp4`. The size is estimated
first and the Play is refused if it wouldn't fit the recordings budget
(`RECORDINGS_MAX_BYTES` on message-api, default 5 GB); each file is also
hard-capped by ffmpeg. `RECORDING_TAP_ENABLED=0` on a worker removes the tap.
Details: **[docs/stream_recorder.md](docs/stream_recorder.md)**,
**[docs/recording_budget.md](docs/recording_budget.md)**.

### Background music (roundtable)

The roundtable streams a live-generated score: one theme per campaign
(`campaigns/<name>/music/theme.yaml`), reshaped in real time to the
current scene's emotion (GEMS moods such as sadness, tension and wonder),
ducked under the voices, and recorded to Postgres. The mood comes from
scene cues in the episode JSON, and the GM can override it from the
control panel's Music card. To switch it off, set `music.enabled` in
`config/workers/roundtable.yaml`, or `MUSIC_ENABLED=0` in `.env`. To
audition a theme locally, run `cd app && ../.venv/Scripts/python.exe -m
music.cli render ../campaigns/ashiorid/music/theme.yaml --mood sadness`.
Details: **[docs/music_engine.md](docs/music_engine.md)**.

## Project Structure

Top level:

- `app/` — agent loop, LLM/TTS/coding-backend clients, avatar rendering, Rerun Theater
  - `app/agent_handlers/` — every bus message handler, one module per role/concern, plus the manager's task backlog dispatcher
  - `app/task_backlog.py` — opt-in task sources (file | Gitea) for the manager
  - `app/relay_io.py` — the one race-safe implementation of in-container relay files
- `services/` — `message-logger`, `message-api`, `control-panel`, `twitch-presence`, `log-shipper`, `campaign-manager`, `3layer-generator`
- `scripts/` — operator helpers, incl. `emergency_stop.sh|.ps1` / `emergency_resume.sh|.ps1` (kill switch) and `send_test_message.sh|.ps1`
- `sandbox/` — seeded-bug workspace the coder agents actually code on
- `repos/` — vendored third-party avatar repos
- `config/` — worker configs, tmux panel/layout presets
- `docs/` — per-module reference docs, including this README's detail subfiles
  - `docs/agent_flow_reference.md` — component/message/flow reference for AI agents
  - `docs/feature_flow_diagram.md` — feature-level Mermaid flow diagrams
  - `docs/e2e_tests.md` — the multi-agent end-to-end flow tests
- `tests/` — pytest suite, incl. `e2e_harness.py` + `test_e2e_*.py` (multi-agent flows with fakes)
- `Dockerfile`, `docker-compose.yml`, `startup.sh`, `requirements.txt`, `.env.example` — root-level build/run files

Full annotated tree, one line per file: **[docs/project_structure.md](docs/project_structure.md)**.

> **Note:** the generic "Mafober Deployment Environment" section below is shared
> boilerplate synced across every project on this machine, describing the default
> homelab deploy target for *new* projects. It does not apply to virtualTubers —
> this project's actual deployment target is **argyre** (`192.168.1.23`, via its
> own local Portainer; previously d2000 `192.168.2.158`), documented in
> [docs/deployment.md](docs/deployment.md) above.

<!-- SHARED:START -->
<!-- SHARED ADDITIONS FROM PROJECTS WILL BE APPENDED BELOW THIS LINE -->
### Added from virtualTubers — 2026-08-17 00:53

<!-- SHARED ADDITIONS FROM PROJECTS WILL BE APPENDED BELOW THIS LINE -->
### Added from virtualTubers — 2026-07-12 02:32

## Claude Code Hook: .venv Enforcement

This project's `.claude/settings.json` includes a `PreToolUse` hook (matcher
`Bash|PowerShell`) that blocks Claude Code from invoking the global/system
Python directly — bare `python`, `python3`, `pip`, `pip3` — whenever a
`.venv` directory exists at the project root. It's a no-op in projects
without a `.venv`. Commands that go through `.venv\Scripts\...` /
`.venv/bin/...` directly, or that activate the venv within the same command,
are unaffected.

This exists because the "always use `.venv`, never global Python" rule was
already documented (see above and in CLAUDE.md) but was still being followed
inconsistently when left to memory/instructions alone — a hook enforces it
at the tool-call level instead of relying on the model to remember. Any
project with a `.venv` can adopt the same hook; see this project's
`.claude/settings.json` for the exact hook definition to copy.


## Mafober Deployment Environment

New projects created or cloned into the managed projects root (`projects_root` in `config.yaml`) deploy to **mafober**, a Proxmox VE homelab host that also runs the shared Docker/Portainer stack for this machine.

### Connection

| Item | Value |
|------|-------|
| Hostname | `mafober` |
| IP Address | `192.168.1.117` |
| Proxmox Web UI | `https://192.168.1.117:8006` |
| Portainer (Docker mgmt) | `https://192.168.1.120:9443` |
| SSH / SFTP | port `22` on `192.168.1.117` |

### Deploying a new project

1. Create a ZFS dataset under `tank_0` for the project's persistent storage (`zfs create tank_0/utilities/<project>`) rather than relying on ephemeral CT storage or named Docker volumes.
2. `chown` the new dataset to the UID/GID the container image expects (e.g. `1000:1000` for linuxserver images, `472:472` for Grafana-style images).
3. Add an explicit bind mount for the dataset into CT 101 (the Portainer LXC): `pct set 101 -mp<N> /tank_0/utilities/<project>,mp=/tank_0/utilities/<project>`, then `pct restart 101`. Each ZFS dataset needs its own `mp` entry — mounting a parent dataset does not expose its children.
4. Define the stack/container in Portainer (`https://192.168.1.120:9443`) pointing at the bind-mounted path.
5. If the project should be scraped by Prometheus or shipped logs to Grafana, register it alongside the existing dashboards/exporters on the host.

### Currently deployed on mafober

- **Portainer** — Docker/stack management (CT 101)
- **Plex** — media server
- **qBittorrent** — torrent client
- **Grafana** — dashboards
- **Prometheus** — metrics
- **node_exporter** / **zfs_exporter** — host-level metrics, run directly on the Proxmox host (not containerized)

### More info

Full hardware specs, ZFS layout, container configs, and troubleshooting lessons learned live in `mafober/mafober_summary.md` (a sibling project directory under the managed projects root). Check there first if these details aren't enough.
<!-- SHARED:END -->

## License

This project is licensed under the GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
