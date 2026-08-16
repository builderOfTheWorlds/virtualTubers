# Deployment (Docker Compose on d2000)

The stack runs on **d2000**, a Windows machine on the local network running Docker
Desktop, via plain `docker compose` — there is no Portainer in front of it. The repo
is checked out directly on that host (e.g.
`C:\Users\matt\PycharmProjects\virtualTubers`), and Kafka, Postgres, and Redis all
run there too (see [Required environment variables](#required-environment-variables-env)
below).

**The worker image is never built by `docker compose up`.**
The three workers use `image: vtube-worker:latest` with `pull_policy: never`, so
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
| `CODER_STREAM_KEY` | `live_xxxxxxxx` | Coder channel's Twitch stream key |
| `MANAGER_STREAM_KEY` | `live_yyyyyyyy` | Manager channel's key |
| `TESTER_STREAM_KEY` | `live_zzzzzzzz` | Tester channel's key |
| `LLM_BASE_URL` | `http://host:11434` | Ollama endpoint |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Only needed if a worker's config sets `llm.provider: claude` |
| `KAFKA_BOOTSTRAP_SERVERS` | `192.168.2.158:9092` | Message-bus broker (runs on d2000 itself) |
| `KAFKA_TOPIC` | `vtuber.messages` | |
| `REDIS_URL` | *(optional)* | Worker on/off flags (docs/worker_control.md). Defaults to `redis://redis:6379`, the bundled `redis` service — only set this if pointing at a different Redis instance |
| `POSTGRES_HOST` … `POSTGRES_PASSWORD` | `192.168.2.158` / `5432` / … | Postgres connection (also on d2000). Backs `message-logger`, `log-shipper`, the narration cache, **and the Rerun Theater episode library** — a worker without these can't perform a rerun at all (docs/episode_store.md) |
| `CODER_NATIVE_STREAM_KEY` etc. | `live_...` | Optional keys for the three A/B coder workers (default to rtmp-preview) |
| `CODER_LAYOUT_PRESET` / `MANAGER_LAYOUT_PRESET` / `TESTER_LAYOUT_PRESET` | `replay` | Optional per-worker layout preset override — set to `replay` to switch that worker into Rerun Theater mode (docs/replay_pane.md). Defaults to the role's normal layout |
| `CODER_NATIVE_LAYOUT_PRESET` / `CODER_OPENCODE_LAYOUT_PRESET` / `CODER_AIDER_LAYOUT_PRESET` | `coder` | Same override for the three A/B coding-backend workers — these three currently **default to `replay`** (Rerun Theater); set one to `coder` to switch that worker back to its normal editor pane |
| `REPLAY_READY_TIMEOUT_S` | `60` | Optional — seconds a duet **director** worker waits for every invited follower's `replay_ready` before refusing the airing outright (docs/duet_replay.md). Passed through to `worker-coder`/`worker-manager`/`worker-tester`; unset keeps the code default (`60.0`) |
| `CODER_AVATAR_PROVIDER` / `CODER_NATIVE_AVATAR_PROVIDER` / `CODER_OPENCODE_AVATAR_PROVIDER` / `CODER_AIDER_AVATAR_PROVIDER` / `MANAGER_AVATAR_PROVIDER` / `TESTER_AVATAR_PROVIDER` | `ascii_avatar` | Optional per-worker avatar renderer override — swaps the avatar pane's provider with no config edit or rebuild (docs/avatar_provider_integration.md, docs/avatar_providers.md). Unset keeps that worker config's `avatar.provider` (defaults to `builtin`) |
| `GIT_SERVER_URL` | *(empty)* | Leave empty for local-commits-only; set when the local git server exists |
| `TWITCH_CHANNEL_MAP` | `mychannel:coder,other:manager` | Twitch channel → worker pairs for viewer greetings (docs/twitch_presence.md). Unset → the twitch-presence service idles |
| `PRESENCE_COOLDOWN_S` | `3600` | Optional — seconds before the same viewer is greeted again |
| `PRESENCE_IGNORE_USERS` | `somebot,otherbot` | Optional — extra chat bots to never greet (extends the built-in list) |

> `.env` is one `NAME=value` pair per line — see `.env.example` for the full
> annotated template.

## Deploy / redeploy after a code change

The `git` and `docker` commands must run **on d2000 itself** (RDP/console
access, or PowerShell remoting into it) — that's where the Docker Desktop
daemon lives:

```powershell
cd C:\Users\matt\PycharmProjects\virtualTubers
git pull                                 # get the latest code
.\install.ps1                            # fetches Piper voices + rebuilds every image the stack needs (see below)
docker compose up -d                     # recreate containers on the freshly built images
```

> Env-only change (e.g. a new stream key)? Skip `install.ps1` — just edit `.env`
> and run `docker compose up -d` to re-inject it and recreate the containers.

`install.ps1` builds every image the stack needs directly (`docker build -f
services/<name>/Dockerfile -t virtualtubers-<name>:latest .`), the same way it
builds `vtube-worker:latest`. **No service in `docker-compose.yml` may use a
`build:` block** — every service is built explicitly via `install.ps1` (or its
bash equivalent, `install.sh`) so builds stay scriptable and reproducible
across every service in one pass. Every service must be `image:` +
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
(see the image-never-built gotcha above).
