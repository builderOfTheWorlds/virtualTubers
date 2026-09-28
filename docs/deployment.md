# Deployment (argyre, via Portainer)

The stack runs on **argyre** (hostname `gx10-35a4`, `192.168.1.23`, Ubuntu
24.04 LTS), managed through a **Portainer** instance running locally on argyre
itself (stack name `virtualtubers`). The repo is checked out on that host at
`/home/secus/codeProjects/virtualTubers`. Kafka and Redis are bundled
containers in the stack (`kafka:9092`, `redis:6379`, stack-internal — Kafka
via the `local-infra` compose profile); Postgres is **external**, on mafober
at `192.168.1.120:5432` (`.env` `POSTGRES_HOST`). Source of truth for these
facts: `PROJECT_CLAUDE.md`.

> **Previously (until 2026-08-16):** the stack ran on **d2000**, a Windows
> machine running Docker Desktop, via plain `docker compose` with no
> Portainer, with Kafka/Postgres/Redis on d2000 too (`192.168.2.158`).
> `install.ps1` was written for that host. Old IPs still appear in some
> examples in other docs.

**The worker image is never built by `docker compose up`.**
Every worker service (`worker-coder`, `-coder-native`, `-coder-opencode`,
`-coder-aider`, `-manager`, `-tester`, `-gm`, `-roundtable`) uses
`image: vtube-worker:latest` with `pull_policy: never`, so
plain `docker compose up -d` will **not** build or pull it — it just fails or runs a
stale image. You must build it on the host after any code change ([Deploy /
redeploy](#deploy--redeploy-after-a-code-change), below), then recreate the
containers so they pick up the new image. This is the #1 cause of "it won't
pick up my change" confusion on this project.

## Required environment variables (`.env`)

Copy `.env.example` to `.env` on the host and fill these in — `docker compose`
reads `.env` from the repo root automatically, no separate stack-env mechanism
involved. Each worker streams to its **own** Twitch channel, so each needs that
channel's key:

| Variable | Example | Notes |
|---|---|---|
| `STREAM_RTMP_URL` | `rtmp://live.twitch.tv/app` | Omit/empty → falls back to the bundled local `rtmp-preview` |
| `TUBER1_STREAM_KEY` | `live_xxxxxxxx` | Coder channel's Twitch stream key |
| `TUBER6_STREAM_KEY` | `live_yyyyyyyy` | Manager channel's key |
| `TUBER5_STREAM_KEY` | `live_zzzzzzzz` | Tester channel's key |
| `TUBER0_STREAM_KEY` | `live_00000000` | GM / roundtable channel's key — the 7th channel (`worker-gm`, worker id `tuber_0`) |
| `LLM_BASE_URL` | `http://host:11434` | Ollama endpoint |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Only needed if a worker's config sets `llm.provider: claude` |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Message-bus broker — on argyre the bundled `kafka` service (enable with `COMPOSE_PROFILES=local-infra`); was `192.168.2.158:9092` on d2000 |
| `KAFKA_TOPIC` | `vtuber.messages` | |
| `REDIS_URL` | *(optional)* | Worker on/off flags (docs/worker_control.md). Defaults to `redis://redis:6379`, the bundled `redis` service — only set this if pointing at a different Redis instance |
| `POSTGRES_HOST` … `POSTGRES_PASSWORD` | `192.168.1.120` / `5432` / … | Postgres connection — external on mafober for argyre (a bundled `postgres` service exists behind the `local-postgres` profile for standalone hosts; d2000 used `192.168.2.158`). Backs `message-logger`, `log-shipper`, the narration cache, **and the Rerun Theater episode library** — a worker without these can't perform a rerun at all (docs/episode_store.md) |
| `TUBER2_STREAM_KEY` etc. | `live_...` | Optional keys for the three A/B coder workers (default to rtmp-preview) |
| `TUBER1_LAYOUT_PRESET` / `TUBER6_LAYOUT_PRESET` / `TUBER5_LAYOUT_PRESET` | `replay` | Optional per-worker layout preset override — set to `replay` to switch that worker into Rerun Theater mode (docs/replay_pane.md). Defaults to the role's normal layout |
| `TUBER2_LAYOUT_PRESET` / `TUBER3_LAYOUT_PRESET` / `TUBER4_LAYOUT_PRESET` | `coder` | Same override for the three A/B coding-backend workers — these three currently **default to `replay`** (Rerun Theater); set one to `coder` to switch that worker back to its normal editor pane |
| `GM_LAYOUT_PRESET` | `roundtable` | Layout preset for the GM / roundtable channel. Defaults to `roundtable` |
| `REPLAY_READY_TIMEOUT_S` | `60` | Optional — seconds a duet **director** worker waits for every invited follower's `replay_ready` before refusing the airing outright (docs/duet_replay.md). Passed through to `worker-coder`/`worker-manager`/`worker-tester`; unset keeps the code default (`60.0`) |
| `TUBER1_AVATAR_PROVIDER` / `TUBER2_AVATAR_PROVIDER` / `TUBER3_AVATAR_PROVIDER` / `TUBER4_AVATAR_PROVIDER` / `TUBER6_AVATAR_PROVIDER` / `TUBER5_AVATAR_PROVIDER` | `ascii_avatar` | Optional per-worker avatar renderer override — swaps the avatar pane's provider with no config edit or rebuild (docs/avatar_provider_integration.md, docs/avatar_providers.md). Unset keeps that worker config's `avatar.provider` (defaults to `builtin`) |
| `WORKER_KILL_FILE` | `/tmp/worker_disabled` | Optional — path of the per-container emergency kill file (docs/worker_control.md). Only matters in worker containers; never create it in `message-api` |
| `GITEA_TOKEN` | *(secret)* | Optional — manager only, for the `gitea` task-backlog source (`read:issue` + `write:issue`, docs/task_backlog.md). Empty = gitea backlog idles with a WARN |
| `AUTO_SUBMIT_DRAFTS` / `AUTO_SUBMIT_TIMEOUT_S` | `false` / `60` | Optional — `3layer-generator` posts each finished publish job to message-api as a review **draft** (never airs until approved; docs/draft_submitter.md) |
| `GIT_SERVER_URL` | *(empty)* | Leave empty for local-commits-only; set when the local git server exists |
| `TWITCH_CHANNEL_MAP` | `mychannel:coder,other:manager` | Twitch channel → worker pairs for viewer greetings (docs/twitch_presence.md). Unset → the twitch-presence service idles |
| `PRESENCE_COOLDOWN_S` | `3600` | Optional — seconds before the same viewer is greeted again |
| `PRESENCE_IGNORE_USERS` | `somebot,otherbot` | Optional — extra chat bots to never greet (extends the built-in list) |

> `.env` is one `NAME=value` pair per line — see `.env.example` for the full
> annotated template.

> **Why `TUBER<n>_` keys but still role-based worker ids?** Only the env var
> **key names** were migrated to slots. `WORKER_ID` values (`coder`,
> `manager`, …), the compose service names (`worker-coder`, `worker-manager`,
> …), `TWITCH_CHANNEL_MAP` values and the `config/workers/<role>.yaml`
> filenames are deliberately unchanged. An episode's `speaker` values *are*
> worker ids, and every episode in the library speaks as
> `coder`/`tester`/`coder-native`/`coder-opencode`/`coder-aider`. Renaming the
> worker ids without migrating those episodes would leave each `speaker`
> matching no `voice.speakers` entry, so the line silently falls through to the
> base voice — all six characters would collapse onto **one** voice with no
> error raised. The full worker-id migration is therefore WP-7, gated on live
> verification. Spec: `.claude/prompts/roundtable_stream_design.md` v1.1
> §2.1–2.2.

> The GM container (`worker-gm`) additionally needs the **voices mount**
> (`/data/voices`) plus `config/voices.yaml` (the symbolic voice registry). It
> is the director that synthesizes *every* speaker in the cast, so without
> those there is no audio on **any** channel, not just the roundtable one.

## Deploy / redeploy after a code change

Image builds run **on argyre itself**, in the repo checkout — that's where
the Docker daemon Portainer manages lives:

```bash
cd /home/secus/codeProjects/virtualTubers
git pull
./install.sh                             # fetches Piper voices + rebuilds every image (SKIP_VOICES=1 skips voices)
# or build just the worker image:
docker build -t vtube-worker:latest .
```

Then recreate the containers so they pick up the new images. The repo ships
`./redeploy.sh` (pytest smoke gate → `install.sh` → `docker compose build`
for the two `build:` services → `docker compose up -d --no-deps
--force-recreate` on every `worker-*` and support service → verification);
it drives `docker compose` directly from the checkout.

**Office mode.** `./redeploy.sh -y --office` deploys the ashiorid_office
show instead (it layers `docker-compose.office.yml` over
`docker-compose.yml`; omit `--office` to return to the dev-team show). Its
one-time setup, extra env vars (`GITEA_TOKEN_OFFICE`, `GITEA_TOKEN_OBSERVER`,
`TUBER7_STREAM_KEY`, `OBSERVER_*`) and volumes are in
[docs/office_deployment.md](office_deployment.md).

> **Not in the repo:** the exact Portainer-side steps for redeploying the
> `virtualtubers` stack (which Portainer action to use after a rebuild, and
> whether the stack's env lives in Portainer or in `.env`) are not
> documented here — check with the operator before relying on either
> `redeploy.sh` or a Portainer stack update for a live change. Either way
> the image must be rebuilt first; recreating containers on a stale
> `vtube-worker:latest` changes nothing.

> Env-only change (e.g. a new stream key)? No rebuild — update the env and
> recreate the affected containers.

**Legacy d2000 (Windows) flow**, kept for reference:

```powershell
cd C:\Users\matt\PycharmProjects\virtualTubers
git pull
.\install.ps1
docker compose up -d
```

`install.ps1` builds every image the stack needs directly (`docker build -f
services/<name>/Dockerfile -t virtualtubers-<name>:latest .`), the same way it
builds `vtube-worker:latest`. **No service in `docker-compose.yml` may use a
`build:` block** — every service is built explicitly via `install.ps1` (or its
bash equivalent, `install.sh`) so builds stay scriptable and reproducible
across every service in one pass. (Current exception, as of 2026-09-27:
`campaign-manager` and `3layer-generator` *do* carry `build:` blocks in
`docker-compose.yml`; `redeploy.sh` builds them with `docker compose build`.) Every service must be `image:` +
`pull_policy: never`. **Whenever a new service is added to the stack, add its
`docker build` line to both `install.ps1` and `install.sh` in the same
change** — a service missing from both scripts has no image on the host, so
`docker compose up -d` recreates its container from a stale or nonexistent
image. `install.ps1`'s header comment is the single source of truth for what
it currently builds — keep it and this paragraph in sync with the file.

**On a Linux/macOS host, or a Windows host with WSL/Git Bash** — `install.sh`
is the bash equivalent, building the same tags from the same Dockerfiles:

```bash
git pull && ./install.sh
```

Keep `install.sh` in sync with `install.ps1` — any new service's build line
goes in both.

## Verify a worker is streaming to the right place

Compose prefixes container names with the project, so they are
`virtualtubers-worker-coder-1`, `virtualtubers-worker-manager-1`, and so on for
every `worker-*` service:

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
(see the image-never-built gotcha above).

## Health check and emergency stop

- **Health view:** `curl http://192.168.1.23:8090/workers/health` (or the
  health column in the control panel at `:8091`) — one row per worker with
  `state` `alive | stale | down | unknown`, last-seen age, and whether that
  worker reports its local kill switch engaged. Backed by each agent's Redis
  liveness key `worker:{id}:alive` (docs/worker_control.md).
- **Emergency stop without Redis/message-api:** on the host,
  `scripts/emergency_stop.sh [worker ...]` creates the kill file in each
  worker container via `docker exec` (ffmpeg stops within ~0.5 s, the agent
  pauses); `scripts/emergency_resume.sh [worker ...]` removes it. From a
  Windows PC, `scripts/emergency_stop.ps1` / `emergency_resume.ps1` SSH to
  `secus@192.168.1.23` and run the same thing. The kill file survives
  `docker restart` but **not** container re-creation (`redeploy.sh`, a
  stack update).
