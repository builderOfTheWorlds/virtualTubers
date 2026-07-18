# virtualTubers

## Summary

virtualTubers is an autonomous AI-powered VTuber streaming system where a team of AI agents (Manager, Coder, Tester) act as a live software development team. Each agent runs in its own Docker container, has its own personality and ASCII-art avatar, works inside a live terminal session (tmux + neovim/htop/etc.), and streams that session to Twitch over RTMP via ffmpeg. It's for anyone who wants to run an always-on, config-driven "AI dev team" stream without hand-building the streaming pipeline from scratch.

The project is early-stage but the core loops are real: the agent brain (`app/agent.py`) has a perceive/think/act slice — it publishes heartbeats every tick and dispatches every incoming message type through role-gated handlers backed by a provider-switchable LLM (Ollama or Claude): the coder narrates a task and hands the commit to the tester, the tester reports `test_passed`/`bug_report` to the manager, and the manager re-delegates fixes (bounded at 3 retries) or reports back to the operator. Coders write real code through swappable backends (native / OpenCode / aider) and the tester really runs pytest against their workspaces. On top of that sits **Rerun Theater**: past real Claude Code dev sessions replay as paced, redacted shows — now with per-airing, two-voice **spoken narration** (boss + coder via local TTS) synchronized to the on-screen action. The terminal avatar (`app/avatar.py`) is still a simple expression-cycling stub. See the Phase 1 roadmap in the architecture doc for what's next.

See [docs/VTuber_AI_Dev_Team_Concept.md](docs/VTuber_AI_Dev_Team_Concept.md) for the full architecture and design plan.

## Recent Changes

**Rerun Theater can now perform as a duet — multiple workers airing the
SAME episode together, each voicing a different speaker** — a
`replay_request` may now carry `payload.cast`, a `{speaker: worker_id}`
map:

- The worker that receives the request becomes the **director**: it
  prepares the airing exactly like a solo show (LLM + TTS for every
  speaker), persists it to Postgres via `app/narration_store.py`, invites
  every other cast worker over the bus (`replay_invite`), waits for all of
  them to confirm ready (`replay_ready`), then paces the whole cast
  scene-by-scene with a `replay_cue` published immediately before
  performing each scene.
- Each invited **follower** loads the SAME persisted airing (never
  generates its own narration), keeps audio only for the scene(s) cast to
  it, and performs the full episode on its own stream — visuals for
  every scene, speaking only its own lines, the avatar "listening" on
  everyone else's.
- **Duets never degrade to solo**: an unreachable narration store/Kafka,
  a voice-prep failure, or a follower that doesn't show up in time
  (`REPLAY_READY_TIMEOUT_S`, default 60s) refuses the whole airing outright
  (`replay_end` + an `operator_reply` error) rather than airing partially.
  Solo requests (no `cast`) are completely unaffected.
- `app/agent.py` gained four any-role relay handlers
  (`replay_invite`/`replay_ready`/`replay_cue`/`replay_end`) that write to
  two new local files (`REPLAY_CUE_FILE`, `REPLAY_READY_FILE`) — panes
  still never consume Kafka directly.
- Every cast worker (director and followers) needs `LAYOUT_PRESET=replay`,
  the `POSTGRES_*` env vars, and reachable Kafka; as shipped that's
  `worker-coder`/`worker-manager`/`worker-tester` only — the three A/B
  coding-backend workers lack the Postgres env and replay library mount.
  Needs a worker image rebuild (no new dependency). See
  [docs/duet_replay.md](docs/duet_replay.md) for the full protocol
  (message schemas, timeouts, ownership rules) and
  [Duets](#duets-multiple-workers-same-episode) below.

**Avatar rendering is now a pluggable provider layer** — `app/avatar.py`
is a thin dispatcher now, not a renderer:

- `app/avatar_providers/` (new) — `AvatarProvider` contract
  (`render_tick(expression, bubble_lines)` + `tick_interval_s`), a registry
  (`builtin` | `ascii_avatar`), and `load_provider()`, which picks a
  provider via `AVATAR_PROVIDER` env > worker config `avatar.provider` >
  `builtin` default. The original static ASCII box face moved verbatim
  into `avatar_providers/builtin.py` — still the default and the
  always-available fallback.
- `repos/ascii-avatar/` (new) — a vendored MIT snapshot
  (`repos/README.md` has the pinned commit) driving a new `ascii_avatar`
  provider: an animated face via the vendored renderer/animation stack
  (forced to its dependency-light "cyberpunk" frame set), with our 7
  expressions mapped onto its 5 states (`avatar.expression_map` to
  override). Only its rendering code is used — its event bus, MCP bridge,
  and TTS/voice modules are never imported.
- **Safe by construction**: an unknown provider name or ANY exception
  while constructing the configured provider (bad config, missing
  vendored repo, terminal init failure) is logged and falls back to
  `builtin` — the avatar pane's only job is to stay up.
- Switching providers is config-only (`avatar.provider` in a worker's
  config, or `AVATAR_PROVIDER` env for a no-config-edit override) — no
  code change needed. `docker-compose.yml` gives every worker its own
  stack env var (`CODER_AVATAR_PROVIDER`, `MANAGER_AVATAR_PROVIDER`,
  `TESTER_AVATAR_PROVIDER`, etc. — see `.env.example`), so a Portainer
  redeploy can flip a single worker's avatar without editing any config
  file. The `Dockerfile` gained `COPY repos/ /repos/`, so the **first**
  switch to `ascii_avatar` needs a worker image rebuild + Portainer
  redeploy to get the vendored repo into the image; after that, flipping
  between providers needs no rebuild. Full write-up of what changed and
  why: [docs/avatar_provider_integration.md](docs/avatar_provider_integration.md)
  (see also [docs/avatar_providers.md](docs/avatar_providers.md) and
  [docs/avatar.md](docs/avatar.md) for API-level reference).

**A viewer starting to watch on Twitch now starts a rerun** — a new
`services/twitch-presence/` service watches each worker's Twitch chat
(anonymous IRC read — no OAuth token or Twitch app needed) and, when a
viewer joins a channel, POSTs a `viewer_joined` message to `message-api`
addressed to that channel's worker. The agent's new `handle_viewer_joined`
(any role) queues a Rerun Theater episode — picked at random from the
worker's library — for its replay pane (needs `LAYOUT_PRESET=replay`,
docs/replay_pane.md), then greets the viewer with an LLM-written
in-character welcome introducing the show (console + avatar speech
bubble) — narration-only, deliberately nothing back on the bus, so a burst
of arrivals never becomes bus traffic. The rerun is queued before the LLM
call (a dead LLM can't stop the show), and a pending operator-queued
replay request is never overwritten. Per-viewer greeting
cooldown (`PRESENCE_COOLDOWN_S`, default 1h) and a built-in bot ignore list
stop rejoin/bot spam. Configure with one stack env var —
`TWITCH_CHANNEL_MAP=mycoderchannel:coder,mymanagerchannel:manager` — the
service idles harmlessly until it's set. Caveat: Twitch has no true
"started watching" event; the chat JOIN (fired automatically by the web
player for logged-in viewers, but batched by Twitch and absent for
logged-out viewers) is the closest per-user signal. `install.sh` builds the
new `virtualtubers-twitch-presence:latest` image. See
[docs/twitch_presence.md](docs/twitch_presence.md).

**Fixed: narration audio never actually reached the stream** —
`app/stream_supervisor.py`'s ffmpeg command captured a synthesized silent
audio track (`anullsrc`) unconditionally, never the PulseAudio `vout` sink
that `audio_player.py`'s `paplay` plays Rerun Theater's narration into.
Every other part of the voice pipeline (LLM lines, Piper synthesis, the
`voiced_narration` transcript table) could work perfectly and the stream
would still be silent. `build_ffmpeg_cmd` now captures `vout.monitor`
(`-f pulse -i vout.monitor`) whenever `pulse_monitor_available()` confirms
Pulse is actually up, falling back to the old silent track only if it
isn't — same soft-degradation contract as the rest of the feature: an
audio problem mutes the show, never cancels it.

That fix then surfaced a second, deeper bug it had been quietly hiding:
PulseAudio's `--system` mode gates every client (`pactl`, `paplay`,
ffmpeg's `-f pulse` input) on membership in the `pulse-access` group,
which the container's `root` user was never added to — every Pulse call
was silently failing with "Access denied" the whole time (masked by a
`2>/dev/null || true` in `startup.sh` and `DEVNULL` in
`audio_player.py`). Fixed with `RUN usermod -aG pulse-access root` in the
Dockerfile; `startup.sh`'s sink creation now logs success/failure instead
of hiding it. See [docs/stream_supervisor.md](docs/stream_supervisor.md).

**Rerun Theater episodes are now SPOKEN — two-voice narration, synced to the
screen** — the planned persona re-voicing layer landed, with TTS on top:

- `app/revoice.py` (new) — per-airing narration pass: groups an episode's
  events into scenes (boss message / coder talk / coder work), asks the
  local LLM for a fresh spoken line per scene — sized to the scene's
  estimated screen time (~2.5 words/sec), so a long console scroll gets
  enough narration to talk over all of it — then synthesizes each line.
  Every airing of the same episode gets new dialogue. LLM down → template
  lines from the redacted script; the show always airs.
- `app/tts_client.py` (new) — provider-switchable TTS (same pattern as
  `llm_client.py`): local **Piper** (default, free), OpenAI, or ElevenLabs;
  adapted from the autoVideo project. Returns each WAV's *measured*
  duration. Two voices via `voice.speakers` config — the boss and the coder
  speak with different models. `app/audio_player.py` (new) plays into the
  container's PulseAudio sink, which ffmpeg already captures onto the
  stream.
- `app/replay.py` — audio-anchored pacing: each voiced scene's typing/
  scrolling speed is scaled so the visuals and the spoken line finish
  together (clamped 0.4–3.0×; visuals done early → the scene holds for the
  voice). Spoken lines also render as dim `♪` text for muted viewers, and
  drive the avatar's speech bubble. `replay_pane.py` reads the worker
  config and runs the pass before each show; `"voice": false` in a
  `replay_request` forces a silent airing.
- Setup: `./install.sh` now fetches the Piper voice models straight into
  `voices/` on the deployment host (compose mounts it `:ro` at
  `/data/voices`) — no manual download/sync step needed there anymore; set
  the worker's `voice.provider: piper`. Worker image rebuild required
  (`piper-tts` added to requirements). See
  [docs/revoice.md](docs/revoice.md), [docs/tts_client.md](docs/tts_client.md),
  and [docs/audio_player.md](docs/audio_player.md).
- **Narration is now durably saved** — the synthesized audio itself is
  never kept (regenerated fresh every airing, then deleted with the temp
  workdir), but `replay_pane.py` publishes each airing's spoken transcript
  (episode, timestamp, every scene's speaker + text) as a `replay_narration`
  bus message; `message-logger` unpacks it into a new Postgres
  `voiced_narration` table, one row per scene. Fire-and-forget — a down or
  unconfigured message bus just skips saving, never blocks the show. See
  [docs/message_logger.md](docs/message_logger.md).

**Rerun Theater — workers can re-perform past real dev sessions as shows** —
saved Claude Code session logs become replayable stream content:

- `app/session_log_parser.py` (new) — parses a `claudeBackupUtility` session
  log into a canonical, **redacted** episode script (passwords/credential
  values, public+tailnet IPs — private LAN IPs stay readable — usernames,
  key-shaped tokens, emails scrubbed before anything can reach a broadcast
  pane). `scripts/build_replay_library.py` batch-builds the episode library;
  it refuses to write any episode that fails the leak audit.
- `app/replay.py` (new) — performs a script as a paced, colorized show:
  boss messages, typed narration, `$ command` + recorded output, edits as
  red/green diffs. **Display-only** — recorded commands are rendered, never
  executed. Drives the existing avatar via `agent_state.py`.
- `app/replay_pane.py` (new) — "Rerun Theater" pane: idles with the episode
  listing, performs an episode when the agent drops the request file.
- Operator wiring: send `{"type": "replay_request", "payload": {"episode":
  "<name>"}}` via message-api (docs/operator_commands.md); `agent.py`
  queues it (any role). Episode names resolve basename-only inside the
  library — bus payloads can't reach other files.
- Config-only mode switch: `layout.preset: replay` (or
  `LAYOUT_PRESET=replay`) swaps the editor pane for the theater
  (`config/panels/replay.yaml`, `config/layouts/replay.yaml`).
- Episode library: build locally, sync to `/opt/virtualTubers/replays` on
  the host — mounted `:ro` into coder/manager/tester at `/data/replays`.
  Persona re-voicing (unique shows per airing via the local LLM) is the
  planned next layer. See [docs/replay_pane.md](docs/replay_pane.md),
  [docs/replay.md](docs/replay.md), and
  [docs/session_log_parser.md](docs/session_log_parser.md).

**Workers can now be turned on/off via an API — no stack redeploy needed** —
each worker (agent + Twitch stream) can be paused and resumed in place, in
the same container:

- `app/worker_control.py` (new) — a Redis-backed `worker:{id}:enabled` flag,
  checked by `app/agent.py`'s tick loop (pauses task/message processing when
  disabled) and by the new `app/stream_supervisor.py` (stops/starts the
  ffmpeg broadcaster when disabled — the Twitch channel actually goes
  offline). Reads fail open (Redis down or key unset → enabled), so a
  control-plane hiccup never silently kills a live stream; writes do not
  fail open, so the operator finds out if a toggle didn't take effect.
- `services/message-api` gained `GET /workers/{id}`, `POST /workers/{id}/enable`,
  and `POST /workers/{id}/disable` — the intended integration point for a
  planned web GUI worker manager. See
  [Turning a worker on/off](#turning-a-worker-onoff-no-redeploy) below.
- `startup.sh` no longer runs `ffmpeg` as its final foreground command —
  ffmpeg used to *be* the container's long-lived process, so killing it to
  honor a "disable" would have killed the whole container. It now runs
  `stream_supervisor.py`, which starts/stops ffmpeg as a child process
  instead (and, as a side effect, auto-restarts it if it ever crashes on its
  own).
- Landing this needs one worker-image rebuild + Portainer redeploy (like any
  code change); every toggle after that is just an HTTP call — see
  [docs/worker_control.md](docs/worker_control.md) and
  [docs/stream_supervisor.md](docs/stream_supervisor.md).

**Coders now write REAL code — swappable coding backends, A/B-tested live** —
the biggest Phase-1 gap is closed: a coder worker can actually edit files, commit,
and have its work really tested, via a config-selected backend
(`coding_backend.provider` in the worker config — same provider-switch pattern
as `llm.provider`):

- Three new coder workers run the SAME task through different tools, each in
  its own workspace volume seeded from a tiny `sandbox/` project (one seeded
  bug, suite goes green when fixed): **NYX-1** (`coder-native`, our own
  minimal LLM loop), **OKO-2** (`coder-opencode`, OpenCode CLI), **ADA-3**
  (`coder-aider`, aider). Send the same `task_assignment` to each via
  `message-api` and compare.
- The tester now **really runs pytest** against read-only mounts of each
  coder's workspace — real `test_passed`/`bug_report` verdicts with failing
  test IDs in the repro; the weighted-random stub survives only for
  workspaces it can't reach. The manager re-delegates fixes to the
  *originating* coder (`coder_id` travels the whole loop).
- Every run is published as a `coding_run_report` bus message and unpacked
  by `message-logger` into a new `coding_backend_runs` Postgres table:
  `SELECT backend, success, duration_s FROM coding_backend_runs;`
- Commits are local-only for now (per-persona authorship via
  `app/git_client.py`); push/PR no-op gracefully until `GIT_SERVER_URL`
  points at the (separately planned) local git server.
- Worker image grew Node 18 + OpenCode + aider (isolated venv) — rebuild
  required: `docker build -t vtube-worker:latest .`

See [docs/coding_backend.md](docs/coding_backend.md),
[docs/git_client.md](docs/git_client.md),
[docs/test_runner.md](docs/test_runner.md),
[docs/workspace_setup.md](docs/workspace_setup.md), and
[sandbox/README.md](sandbox/README.md) for task ideas.

**Container logs now ship to Postgres too** — `services/log-shipper/` (new)
follows the stdout/stderr of every container in this project's docker-compose
stack (discovered via a read-only Docker socket mount) and inserts each line
into a `container_logs` table, alongside the existing `messages` table from
`message-logger`. This means all of this project's container logs — workers,
`message-logger`, `message-api`, etc. — can be reviewed with a single SQL
query instead of `docker logs` per container. Ships new lines only; no
historical backfill. See [docs/log_shipper.md](docs/log_shipper.md) for
details, including a security note on the Docker socket mount.

Postgres access also moved off the shared `mafober` role/database onto a
project-dedicated `virtualtubers` role/database — see
[docs/sql/](docs/sql/) for the one-time `CREATE ROLE`/`CREATE DATABASE`/
`CREATE TABLE` setup scripts and how to run them. `.env.example` and
`docker-compose.yml`'s Postgres defaults were updated to match.

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
- (Optional) Piper voice models for spoken replay narration — fetched with `scripts/download_voices.py`, see [Rerun Theater](#rerun-theater--replaying-past-sessions-with-voices)
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

This repo lives on the homelab **Gitea** instance, which auto-mirrors every push to
GitHub — set up 2026-07-05, so pushing to GitHub by hand is never needed:

| Remote | URL | Role |
|---|---|---|
| `origin` | `ssh://git@192.168.1.120:2222/gitea_admin/virtualTubers.git` | **Source of truth — push here** |
| `github` | `https://github.com/builderOfTheWorlds/virtualTubers` | Read-only mirror target (don't push) |

- A normal `git push` (to `origin`) lands on GitHub within seconds via Gitea's native
  push-mirror (`sync_on_commit: true`), with an 8-hour interval sync as fallback.
- The mirror credential is a fine-grained GitHub PAT stored inside Gitea, scoped to the
  mirrored repos only (Contents: read/write). **It expires 2026-10-03** — after that,
  mirroring silently fails with 403s until the token is regenerated and updated in
  Gitea (repo → Settings → Repository → Mirror Settings).
- Check mirror health: Gitea (`http://192.168.1.120:3300`) → repo → Settings →
  Repository → Mirror Settings (shows last-sync time and last error), or compare
  `git ls-remote origin main` vs `git ls-remote github main` — the hashes should match.
- To enable the same mirroring for another project, run `add_push_mirror.ps1` from
  `mafober/portainer/configs/gitea/` — full walkthrough (including the one-time GitHub
  PAT steps) in that folder's `github_push_mirror.md`.

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

### Turning a worker on/off (no redeploy)

Any worker can be paused and resumed without touching `docker-compose.yml`,
Portainer, or rebuilding the image — via `message-api`'s `/workers` endpoints
(see [docs/worker_control.md](docs/worker_control.md) and
[docs/message_api.md](docs/message_api.md)). "Off" stops both the agent
(no more task/message processing) and the Twitch stream (ffmpeg stops
pushing frames); the container itself stays up the whole time, ready to
resume instantly:

```bash
curl -X POST http://localhost:8090/workers/coder/disable   # agent pauses, stream goes offline
curl http://localhost:8090/workers/coder                   # {"worker_id": "coder", "enabled": false}
curl -X POST http://localhost:8090/workers/coder/enable    # resumes both, in place
```

The flag lives in the shared `redis` service and defaults to enabled — a
worker nobody has ever toggled, or a temporarily-unreachable Redis, both
behave as "on" rather than silently going dark.

### Rerun Theater — replaying past sessions, with voices

Rerun Theater re-performs saved (parsed, redacted) Claude Code dev sessions
as stream shows, and can narrate them out loud with two TTS voices — the
boss and the coder — whose spoken lines are written fresh by the local LLM
on every airing and timed so speech and on-screen text finish together.
Full pipeline docs: [docs/session_log_parser.md](docs/session_log_parser.md)
→ [docs/revoice.md](docs/revoice.md) → [docs/replay.md](docs/replay.md) →
[docs/replay_pane.md](docs/replay_pane.md) → (multi-worker)
[docs/duet_replay.md](docs/duet_replay.md).

One-time setup:

```bash
# 1. Build the episode library from your session logs (on the machine that has them)
.venv/Scripts/python.exe scripts/build_replay_library.py \
  --logs "path/to/logs/claude/virtualTubers" --out replays

# 2. Sync the episode library onto the deployment host
#    replays/ -> /opt/virtualTubers/replays   (mounted :ro at /data/replays)
```

The Piper voice models (coder + boss) don't need a manual download/sync —
`./install.sh` fetches them straight into `voices/` on the deployment host
(see [Deploy / redeploy](#deploy--redeploy-after-a-code-change) below), which
is already the bind-mount source for `/data/voices`. Only needed manually for
local preview off the host: `.venv/Scripts/python.exe scripts/download_voices.py --out voices`.

Then enable it per worker (config-only, plus one image rebuild for the
`piper-tts` dependency):

```yaml
# config/workers/<role>.yaml
voice:
  provider: piper          # "null" keeps replays silent
```

Set `LAYOUT_PRESET=replay` on that worker (e.g. `CODER_LAYOUT_PRESET=replay`
in the Portainer stack env) so its editor pane becomes the theater, and
request a show:

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "2026-07-02_04-27-00_6ecdde82"}}'
```

The pane prints "preparing tonight's episode…" while the LLM writes the
dialogue and TTS renders it, then performs the show — boss messages in the
boss's voice, narration and work commentary in the coder's, audio going out
on the stream via the same PulseAudio sink ffmpeg already captures. Long
command outputs get proportionally longer narration, so the avatar always
has something to say over the scroll. Add `"voice": false` to the payload
for a silent airing; voice failures (LLM/TTS/player down) automatically
degrade to a silent show rather than cancelling it. Local preview without
the stack:

```bash
python app/replay.py replays/<episode>.json --voice-config config/workers/coder.yaml
```

### Duets (multiple workers, same episode)

Add `payload.cast` to a `replay_request` to have several workers perform
the SAME episode together, each on its own Twitch channel, each voicing a
different speaker — full protocol reference: [docs/duet_replay.md](docs/duet_replay.md).

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "2026-07-02_04-27-00_6ecdde82",
                    "cast": {"boss": "manager", "coder": "coder"}}}'
```

- `coder` (the worker addressed) becomes the **director**: it prepares and
  Postgres-persists the airing once, invites `manager`, and paces both
  streams scene-by-scene with cues.
- Every stream shows the **whole episode's visuals**, but only speaks its
  own cast lines: the coder's stream plays the `coder` speaker's audio and
  shows the avatar "listening" during boss lines; the manager's stream is
  the mirror image.
- **Duets never degrade to solo** — if the director can't reach the
  narration store or Kafka, voice prep fails, or `manager` never confirms
  ready in time, the whole airing refuses outright (an `operator_reply`
  error, when the director could still reach Kafka at all) rather than
  airing solo or partially.
- Deployment: every cast worker needs `LAYOUT_PRESET=replay`, the
  `POSTGRES_*` env vars, and reachable Kafka — as shipped that's `coder`/
  `manager`/`tester` only (the three A/B coding-backend workers aren't
  wired for Postgres or the replay library yet).

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
| `REDIS_URL` | *(optional)* | Worker on/off flags (docs/worker_control.md). Defaults to `redis://redis:6379`, the bundled `redis` service — only set this if pointing at a different Redis instance |
| `POSTGRES_HOST` … `POSTGRES_PASSWORD` | | `message-logger` Postgres connection |
| `CODER_NATIVE_STREAM_KEY` etc. | `live_...` | Optional keys for the three A/B coder workers (default to rtmp-preview) |
| `CODER_LAYOUT_PRESET` / `MANAGER_LAYOUT_PRESET` / `TESTER_LAYOUT_PRESET` | `replay` | Optional per-worker layout preset override — set to `replay` to switch that worker into Rerun Theater mode (docs/replay_pane.md). Defaults to the role's normal layout |
| `REPLAY_READY_TIMEOUT_S` | `60` | Optional — seconds a duet **director** worker waits for every invited follower's `replay_ready` before refusing the airing outright (docs/duet_replay.md). Passed through to `worker-coder`/`worker-manager`/`worker-tester`; unset keeps the code default (`60.0`) |
| `CODER_AVATAR_PROVIDER` / `CODER_NATIVE_AVATAR_PROVIDER` / `CODER_OPENCODE_AVATAR_PROVIDER` / `CODER_AIDER_AVATAR_PROVIDER` / `MANAGER_AVATAR_PROVIDER` / `TESTER_AVATAR_PROVIDER` | `ascii_avatar` | Optional per-worker avatar renderer override — swaps the avatar pane's provider with no config edit or rebuild (docs/avatar_provider_integration.md, docs/avatar_providers.md). Unset keeps that worker config's `avatar.provider` (defaults to `builtin`) |
| `GIT_SERVER_URL` | *(empty)* | Leave empty for local-commits-only; set when the local git server exists |
| `TWITCH_CHANNEL_MAP` | `mychannel:coder,other:manager` | Twitch channel → worker pairs for viewer greetings (docs/twitch_presence.md). Unset → the twitch-presence service idles |
| `PRESENCE_COOLDOWN_S` | `3600` | Optional — seconds before the same viewer is greeted again |
| `PRESENCE_IGNORE_USERS` | `somebot,otherbot` | Optional — extra chat bots to never greet (extends the built-in list) |

> Set each variable as its own `name` → `value` pair. Don't put a URL (or any value)
> in the `name` field — that just creates a junk variable nothing reads.

### Deploy / redeploy after a code change

The `git` and `docker` commands must run **where the Docker daemon lives**: inside
CT 101 (the Portainer LXC, `192.168.1.120`) on the `mafober` Proxmox host — *not*
on the Proxmox host itself, and not on your local machine.

SSH into the Proxmox host:

```bash
ssh root@192.168.1.117
```

Then, from the Proxmox shell:

```bash
pct enter 101                            # enter the Portainer LXC (CT 101)
cd /opt/virtualTubers                    # the repo checkout
git pull                                 # get the latest code
./install.sh                             # fetches Piper voices + rebuilds every image the stack needs (see below)
```

Then in the **Portainer UI** → **Stacks** → this stack → **Update the stack**,
enabling **Re-pull image and redeploy** / force recreate. Portainer recreates the
workers on the freshly built images using the current stack env vars.

> Env-only change (e.g. a new stream key)? Skip `install.sh` — just **Update the
> stack** in Portainer to re-inject the env and recreate the containers.

`install.sh` builds every image the stack needs directly (`docker build -f
services/<name>/Dockerfile -t virtualtubers-<name>:latest .`), the same way it
builds `vtube-worker:latest`. **No service in `docker-compose.yml` may use a
`build:` block** — Portainer's stack working directory (`/data/compose/<id>/`)
only contains the compose YAML, not the rest of the repo, so any `build:`
pointing at `services/<name>/Dockerfile` fails on every deploy with `lstat
.../services: no such file or directory`. Every service must be `image:` +
`pull_policy: never`, built here first. **Whenever a new service is added to
the stack, add its `docker build` line to `install.sh` in the same change** —
a service missing from the script has no image on the host, so Portainer
recreates its container from a stale or nonexistent image. `install.sh`'s
header comment is the single source of truth for what it currently builds —
keep it and this paragraph in sync with the file.

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
- Environment variables (set via `docker-compose.yml` or `.env`) override config file values at runtime, notably: `STREAM_RTMP_URL`, `CODER_STREAM_KEY` / `MANAGER_STREAM_KEY` / `TESTER_STREAM_KEY`, `LLM_BASE_URL`, `DISPLAY_NUM`, `WORKER_ID`, `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`, `REDIS_URL`, `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`

Key sections inside a worker config:

| Section | Controls |
|---|---|
| `agent` | Role, display name, system prompt, tick rate, context window |
| `llm` | Provider (`ollama` \| `claude`), base URL, model, temperature |
| `voice` | TTS for spoken replay narration: provider (`piper` \| `kokoro` \| `openai` \| `elevenlabs` \| `null`), Piper model path, per-speaker (boss/coder) voice overrides. See [docs/tts_client.md](docs/tts_client.md) |
| `avatar` | Name, title, ASCII expression states, speech bubble sizing |
| `layout` | Which tmux layout preset to use (`layout.preset`: `coder` \| `tester` \| `manager`; `LAYOUT_PRESET` env overrides). Presets live in `config/layouts/`; reusable panel-type defaults in `config/panels/`. Optional per-pane overrides under `layout.panes.<id>`. |
| `stream` | RTMP URL/key, resolution, bitrate, fps |
| `world_state` | Shared state backend (`file` \| `redis`) and connection info |
| `message_bus` | Kafka backend, bootstrap servers, topic, and this worker's ID |
| `coding_backend` | Which tool writes real code (`provider`: `native` \| `opencode` \| `aider` \| `none`; `workspace`, `timeout_s`, optional `model` override). See [docs/coding_backend.md](docs/coding_backend.md). |

### Worker on/off control (what's set up)

Every worker's enabled/disabled state lives outside `worker.yaml` entirely —
in the shared `redis` service (`docker-compose.yml`), one key per worker
(`worker:{id}:enabled`), so it can be flipped at runtime without touching
config files or the stack:

- **Who reads it**: `app/agent.py`'s tick loop (gates task/message
  processing) and `app/stream_supervisor.py` (gates the ffmpeg broadcaster —
  this is what makes "disable" actually take the Twitch channel offline,
  not just idle the avatar).
- **Who writes it**: `services/message-api`'s `GET/POST /workers/{id}...`
  endpoints (port `8090`) — see
  [Turning a worker on/off](#turning-a-worker-onoff-no-redeploy) above for
  `curl` examples. This is the integration point for a planned web GUI
  worker manager.
- **Failure behavior**: reads fail open — a worker with no key yet, or a
  temporarily unreachable Redis, is treated as *enabled*. A control-plane
  hiccup can never silently take a live stream down. Writes do not fail
  open — the API returns HTTP 503 if a toggle couldn't be persisted.
- **Full design**: [docs/worker_control.md](docs/worker_control.md) and
  [docs/stream_supervisor.md](docs/stream_supervisor.md).

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
│   ├── agent.py          # Agent loop (perceive/think/act): heartbeats, task narration, real coding + testing flows, duet replay relay
│   ├── llm_client.py     # Provider-switchable LLM client (Ollama | Claude)
│   ├── coding_backend.py # Swappable coding backend layer (native | opencode | aider) + TaskResult
│   ├── coding_backends/  # One adapter per backend provider
│   ├── git_client.py     # Local git ops per persona; push/PR no-op until GIT_SERVER_URL
│   ├── workspace_setup.py# Seeds coder workspace volumes from the sandbox template
│   ├── test_runner.py    # Tester's real pytest execution (copy-to-tmpdir, ro mounts)
│   ├── worker_control.py # Redis-backed per-worker on/off flag (agent + stream pause/resume)
│   ├── stream_supervisor.py # Starts/stops ffmpeg based on the on/off flag (replaces startup.sh's raw ffmpeg call)
│   ├── avatar.py         # Terminal ASCII avatar dispatcher — polls agent_state.py, hands frames to an avatar_providers/ backend
│   ├── avatar_providers/ # Pluggable avatar rendering backends (builtin static face | ascii_avatar animated adapter)
│   ├── avatar_display.py # display_width()/build_bubble_box() shared by avatar.py and every avatar provider
│   ├── agent_state.py    # Small local state file bridging agent.py's activity to avatar.py's display
│   ├── session_log_parser.py # Saved Claude session logs -> redacted replay scripts
│   ├── replay.py         # Performs a replay script as a paced show (display-only, audio-synced, duet cue hooks)
│   ├── replay_pane.py    # "Rerun Theater" pane: idles, plays operator-requested episodes solo or as a duet director/follower
│   ├── revoice.py        # Per-airing narration pass: scenes + LLM-written spoken lines
│   ├── narration_store.py # Postgres cache for voiced airings; duet director persists, followers load the same airing
│   ├── tts_client.py     # Provider-switchable TTS (Piper | OpenAI | ElevenLabs), measured durations
│   ├── audio_player.py   # Best-effort WAV playback into the streamed PulseAudio sink
│   ├── build_layout.py   # Config-driven tmux layout engine (emits the tmux command sequence)
│   ├── tmux_control.py   # Agent's "hands": select a pane by name, type text/commands into it
│   ├── message_bus.py    # Shared Kafka producer/consumer/schema helper
│   └── tail_bus.py       # Rich configurable Kafka feed for the tmux "Message Bus" pane
├── services/
│   ├── message-logger/    # Consumes every bus message, logs it to Postgres
│   ├── message-api/       # FastAPI service for injecting test messages onto the bus
│   └── twitch-presence/   # Watches Twitch chat, announces arriving viewers (viewer_joined)
├── sandbox/               # Seeded-bug workspace template the coder agents actually code on
├── repos/                 # Vendored third-party avatar repos (see repos/README.md) — e.g. ascii-avatar, used by avatar_providers/ascii_avatar.py
├── config/
│   ├── worker.yaml        # Annotated default/template worker config (selects a layout preset)
│   ├── workers/           # Per-role configs (coder, manager, tester + coder-native/-opencode/-aider)
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

<!-- SHARED:START -->
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
