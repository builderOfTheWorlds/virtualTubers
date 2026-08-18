# virtualTubers

## Summary

virtualTubers is an autonomous AI-powered VTuber streaming system where a team of AI agents (Manager, Coder, Tester) act as a live software development team. Each agent runs in its own Docker container, has its own personality and ASCII-art avatar, works inside a live terminal session (tmux + neovim/htop/etc.), and streams that session to Twitch over RTMP via ffmpeg. It's for anyone who wants to run an always-on, config-driven "AI dev team" stream without hand-building the streaming pipeline from scratch.

The project is early-stage but the core loops are real: the agent brain (`app/agent.py`) has a perceive/think/act slice — it publishes heartbeats every tick and dispatches every incoming message type through role-gated handlers backed by a provider-switchable LLM (Ollama or Claude): the coder narrates a task and hands the commit to the tester, the tester reports `test_passed`/`bug_report` to the manager, and the manager re-delegates fixes (bounded at 3 retries) or reports back to the operator. Coders write real code through swappable backends (native / OpenCode / aider) and the tester really runs pytest against their workspaces. On top of that sits **Rerun Theater**: past real Claude Code dev sessions replay as paced, redacted shows — now with per-airing, two-voice **spoken narration** (boss + coder via local TTS) synchronized to the on-screen action. The terminal avatar (`app/avatar.py`) is still a simple expression-cycling stub. See the Phase 1 roadmap in the architecture doc for what's next.

See [docs/VTuber_AI_Dev_Team_Concept.md](docs/VTuber_AI_Dev_Team_Concept.md) for the full architecture and design plan.

## Changelog

Dated write-ups of every feature and fix live in **[CHANGELOG.md](CHANGELOG.md)**
(newest first) — moved out of this file so the README stays a quick
orientation rather than a running history. Latest entry: the Rerun Theater
episode library moved from the filesystem into Postgres, gated behind a
shape/name/leak-audit/dry-run-render validator on upload.

## Prerequisites

- Docker and Docker Compose
- An RTMP destination — a Twitch stream key for live streaming, or a local RTMP preview server (bundled via `rtmp-preview` in `docker-compose.yml`) for local testing
- (Optional) A running [Ollama](https://ollama.ai) instance for local LLM inference — the default worker config points at `http://localhost:11434`
- (Optional) An [Anthropic API key](https://console.anthropic.com/) if any worker's config sets `llm.provider: claude` instead of `ollama`
- (Optional) Piper voice models for spoken replay narration — fetched with `scripts/download_voices.py`, see [Rerun Theater](docs/usage.md#rerun-theater--replaying-past-sessions-with-voices)
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

## Git Remotes & GitHub Mirror

This repo pushes to a homelab Gitea instance (`origin`), which auto-mirrors
every push to GitHub (`github`, read-only) within seconds. Full remote URLs,
the mirror credential's expiry date, and health-check commands are in
**[docs/git_remotes.md](docs/git_remotes.md)**.

## Usage

Start the full stack (three workers + message-logger + message-api + Redis + local RTMP preview):

```bash
docker compose up
```

This launches three worker containers — `worker-coder`, `worker-manager`, `worker-tester` — plus `message-logger`, `message-api`, a shared `redis` instance, and an `rtmp-preview` server for local testing. Each worker boots a virtual display and tmux session, starts the agent loop (narrating work as it flows coder → tester → manager → operator over the Kafka bus), and streams that session out over RTMP.

To preview locally without a real Twitch key, leave `STREAM_RTMP_URL` unset (it defaults to `rtmp://rtmp-preview:1935/live`) and view the stream with a player like VLC pointed at `rtmp://localhost:1935/live/<stream_key>`.

Send a worker an instruction via `message-api` (port `8090`):

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "say hello"}}'
```

Or drive the same controls — worker on/off, log filtering, message
injection, log pruning, the Rerun Theater replay library — from a browser at
**http://localhost:8091** (`control-panel`, docs/control_panel.md), no curl
required.

For everything else — shelling into a container, the full inter-agent
messaging protocol, pausing/resuming a worker, running Rerun Theater (solo
shows and multi-worker duets, with spoken narration), and local development
outside Docker — see **[docs/usage.md](docs/usage.md)**.

## Deployment (Docker Compose on d2000)

The stack runs on **d2000**, a Windows machine on the local network running
Docker Desktop, via plain `docker compose` — there is no Portainer in front
of it. **The worker image is never built by `docker compose up`** — the
three workers use `image: vtube-worker:latest` with `pull_policy: never`,
so a plain `docker compose up -d` will not build or pull it; it just fails
or runs a stale image. You must build it on the host after any code change,
then recreate the containers. This is the #1 cause of "it won't pick up my
change" confusion on this project.

Full required-env-var table, the `install.ps1`/`install.sh` build-and-deploy
steps, and how to verify a worker is streaming to the right place:
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

## Project Structure

Top level:

- `app/` — agent loop, LLM/TTS/coding-backend clients, avatar rendering, Rerun Theater
- `services/` — `message-logger`, `message-api`, `control-panel`, `twitch-presence`
- `sandbox/` — seeded-bug workspace the coder agents actually code on
- `repos/` — vendored third-party avatar repos
- `config/` — worker configs, tmux panel/layout presets
- `docs/` — per-module reference docs, including this README's detail subfiles
- `tests/` — pytest suite
- `Dockerfile`, `docker-compose.yml`, `startup.sh`, `requirements.txt`, `.env.example` — root-level build/run files

Full annotated tree, one line per file: **[docs/project_structure.md](docs/project_structure.md)**.

> **Note:** the generic "Mafober Deployment Environment" section below is shared
> boilerplate synced across every project on this machine, describing the default
> homelab deploy target for *new* projects. It does not apply to virtualTubers —
> this project's actual deployment target is **d2000** (`192.168.2.158`), documented
> in [docs/deployment.md](docs/deployment.md) above.

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
