# Blank workers + the coder-campaign overlay

## Overview

`docker-compose.yml` used to define six workers, each with a hardcoded
identity baked into its own env block and its own mounted config file:
`worker-coder`, `worker-coder-native`, `worker-coder-opencode`,
`worker-coder-aider`, `worker-manager`, `worker-tester`. Giving a worker a
different personality meant editing `docker-compose.yml` and rebuilding the
stack.

As of the campaign platform build (`docs/campaign_platform_build.md`),
identity — `agent.role`/`name`/`system_prompt`, voice, avatar — is assigned
**at runtime**, over the API, not baked into compose or `.env`
(`docs/campaign_platform_build.md#blank-workers--runtime-persona-assignment`).
That makes the six hardcoded workers obsolete: this change replaces them with
eight **generic, interchangeable** workers — `worker-1` … `worker-8` — that
are identical except for their container identity (`WORKER_ID`,
`DISPLAY_NUM`) and the *names* of three per-worker override env vars.

A worker with no persona assigned is not a new "blank" mode — it's just
**disabled**, reusing `app/worker_control.py`'s existing fail-open enable
flag exactly as it works today. No code changes were needed to make that
true; it falls out of "no persona means the tick loop and stream supervisor
never got told to do anything."

The one piece of the old six-worker setup that genuinely can't be assigned
at runtime — the A/B coding-backend workspace volumes, and the tester's
read-only view of every coder's workspace — moves to a separate, opt-in
overlay compose file: `docker-compose.coder.yml`. See
[The coder-campaign overlay](#the-coder-campaign-overlay-dockercomposecoderyml)
below for why this couldn't just be a `profiles:` entry.

## Compose service inventory

`docker-compose.yml`, after this change:

| Service | Profile | Notes |
|---|---|---|
| `worker-1` … `worker-8` | *(none — always on)* | Identical generic workers. Blank/disabled until a campaign casts a persona onto them. |
| `message-logger` | *(none)* | Unchanged. |
| `message-api` | *(none)* | Unchanged. |
| `twitch-presence` | *(none)* | Unchanged. |
| `log-shipper` | *(none)* | Unchanged. |
| `redis` | *(none)* | Unchanged. |
| `rtmp-preview` | *(none)* | Unchanged. |
| `kafka-init-perms` | `local-infra` | Unchanged. |
| `kafka` | `local-infra` | Unchanged. |
| `postgres` | `local-infra` | Unchanged. |

`docker-compose.coder.yml` (loaded only if explicitly asked for — see
below) merges additional `environment`/`volumes` fragments into `worker-1`,
`worker-2`, `worker-3`, `worker-4`, and `worker-6`. It defines no new
services and carries no `profiles:` key at all.

Every service in both files has `image:` + `pull_policy: never`; neither
file has ever contained a `build:` block, and this change didn't add one —
`vtube-worker:latest` is still built once by `install.ps1`/`install.sh` and
shared by all eight generic workers (and, when the overlay is loaded, by
the four coder-campaign workers too).

## Generic worker signature

Every `worker-N` (N = 1..8) service block:

```yaml
worker-N:
  image: vtube-worker:latest
  pull_policy: never
  environment:
    CONFIG_PATH: /config/worker.yaml
    STREAM_RTMP_URL: ${STREAM_RTMP_URL:-rtmp://rtmp-preview:1935/live}
    STREAM_KEY: ${WORKER_N_STREAM_KEY:-worker-N}
    LLM_BASE_URL: ${LLM_BASE_URL:-http://host-gateway:11434}
    ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
    DISPLAY_NUM: 98+N
    WORKER_ID: worker-N
    LAYOUT_PRESET: ${WORKER_N_LAYOUT_PRESET:-replay}
    AVATAR_PROVIDER: ${WORKER_N_AVATAR_PROVIDER:-}
    KAFKA_BOOTSTRAP_SERVERS: ${KAFKA_BOOTSTRAP_SERVERS:?...}
    KAFKA_TOPIC: ${KAFKA_TOPIC:-vtuber.messages}
    GIT_SERVER_URL: ${GIT_SERVER_URL:-}
    REDIS_URL: ${REDIS_URL:-redis://redis:6379}
    POSTGRES_HOST: ${POSTGRES_HOST:?...}
    POSTGRES_PORT: ${POSTGRES_PORT:-5432}
    POSTGRES_DB: ${POSTGRES_DB:-virtualtubers}
    POSTGRES_USER: ${POSTGRES_USER:-virtualtubers}
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?...}
    REPLAY_READY_TIMEOUT_S: ${REPLAY_READY_TIMEOUT_S:-}
    REPLAY_SKIP_LLM: ${REPLAY_SKIP_LLM:-}
    TTS_PROVIDER: ${TTS_PROVIDER:-}
    TTS_BASE_URL: ${TTS_BASE_URL:-}
  volumes:
    - ./config:/config:ro
    - world-state:/data/world-state
    - ./replays:/data/replays:ro
    - ./voices:/data/voices:ro
  ipc: host
  shm_size: 256mb
  restart: unless-stopped
  depends_on: [rtmp-preview, redis]
```

`docker-compose.yml` implements this with a YAML anchor (`&worker-env` /
`<<: *worker-env` merge key on `environment`, `&worker-volumes` / `*worker-volumes`
alias on `volumes`) so the eight blocks can't drift from each other by
accident — every field that should be identical literally *is* the same
YAML node. `tests/test_docker_compose_structure.py` asserts this
structurally after parsing (PyYAML resolves anchors/merge keys before your
code ever sees the dict, so the test doesn't need to know anchors were
used).

### What differs, and why

| Field | Differs how | Why it's not persona |
|---|---|---|
| `WORKER_ID` | `worker-1` … `worker-8`, hardcoded | Container identity — the message-bus `from` field and the Redis key namespace (`worker:{id}:enabled`, `worker:{id}:persona`). Fixed for the container's lifetime; a persona is layered *onto* this id, never replaces it. |
| `DISPLAY_NUM` | `99` … `106` | Xvfb display isolation — extends the old scheme (coder=99, manager=100, tester=101, coder-native=102, coder-opencode=103, coder-aider=104) by one slot per worker instead of one slot per role. |
| `WORKER_N_STREAM_KEY` / `WORKER_N_LAYOUT_PRESET` / `WORKER_N_AVATAR_PROVIDER` | Env var *names* embed the index | So `.env` can still target one specific worker without every worker sharing one stream key or one layout override. |

Everything else — image, the whole rest of the `environment` map, `volumes`,
`ipc`, `shm_size`, `restart`, `depends_on` — is byte-for-byte identical.

### `config/worker.yaml`: the blank-worker default

All eight workers mount the **whole** `./config` directory read-only
(`./config:/config:ro`), not a single per-worker file — this is what lets a
worker resolve `config/campaigns/<campaign>/personas.yaml` at runtime
(CONTRACT §8). `CONFIG_PATH=/config/worker.yaml` still selects
`config/worker.yaml` as the base config every worker loads at boot.

`config/worker.yaml` was rewritten to be a genuinely blank default rather
than a copy of the old "coder" persona:

- `agent.role: custom` — never matches any of `app/agent.py`'s role-gated
  handler checks (`"coder"`/`"tester"`/`"manager"`), so every handler is a
  safe no-op until a persona overlay changes it. This is what "inert, not
  crashing" means in practice: nothing in the message-handling dispatch
  table can misfire against a blank worker.
- `agent.name: "WORKER"`, `avatar.name: "WORKER"`, `avatar.title:
  "Unassigned"`, `stream.key: "worker"`, `world_state.worker_id`/
  `message_bus.worker_id: "worker"` — generic placeholders. In practice
  `WORKER_ID`/`STREAM_KEY` env vars from `docker-compose.yml` always
  override these; they only matter for a local `build_layout.py`/`agent.py`
  dry-run with no env set at all.
- `agent.system_prompt` — a short, honest "no persona assigned, stand by"
  line instead of borrowing any one persona's voice.
- `layout.preset: replay` — matches the env default so a freshly cast
  persona can perform episodes immediately without an operator having to
  also flip the layout.
- `voice.speakers` (per-speaker voice overrides for `coder`/`boss`/`tester`/
  `coder-native`/`coder-opencode`/`coder-aider`) was left in place — it's
  needed for ANY worker to correctly direct a multi-persona duet regardless
  of its own identity, so it's genuinely infra, not a persona field.

## The coder-campaign overlay (`docker-compose.coder.yml`)

### What it's for

The old `worker-coder-native`/`-opencode`/`-aider`/`-tester` had things a
persona can't provide: **named Docker volumes** for each coding backend's
workspace (`repo-native`, `repo-opencode`, `repo-aider`, plus the legacy
`repo` for `coder`), and the tester's four read-only cross-mounts of those
same volumes at `/data/repos/<coder_id>`. Volumes are wired at container
creation time; nothing about a `POST /campaigns/coder/start` call can attach
a new bind mount to an already-running container. This is genuinely
infrastructure, not personality — recon confirmed
`coding_backend`/`workspaces`/`world_state`/`message_bus.worker_id`/
`stream.key` were already "infra-identity, not persona" even under the old
per-worker-file model.

### Why an overlay file, not a `profiles:` entry

The initial plan was to gate this "exactly like `local-infra`" — a
`profiles: ["coder-campaign"]` list, opted into via `.env`'s
`COMPOSE_PROFILES`. That doesn't work here, and it's worth spelling out
why, because it's not obvious until you try it:

`local-infra` gates **whole, independent services** (`kafka`, `postgres`,
`kafka-init-perms`) that don't exist at all without the profile — a service
either starts or it doesn't. The coder-campaign wiring is different in
kind: it needs to **add** volumes and env vars to `worker-1`..`worker-4`/
`worker-6`, services that must already be running, unconditionally, as
blank generic workers. A single Compose file cannot define the same service
key twice (the second definition would simply replace the first, not merge
with it), so there is no way to write "`worker-2`, plus these extra mounts,
if profile X is active" inside one file.

The only mechanism Compose actually has for this is **multiple `-f` files**,
which merge same-named services across files (mapping fields like
`environment` merge key-by-key; list fields like `volumes` concatenate —
see [Compose's merge documentation](https://docs.docker.com/compose/multiple-compose-files/merge/)).
So `docker-compose.coder.yml` is a second file containing only the
*additional* `environment`/`volumes` fragments for `worker-1`, `worker-2`,
`worker-3`, `worker-4`, and `worker-6` — nothing it defines conflicts with
`docker-compose.yml`, because it never redefines a key the base file
already sets.

Critically, **this overlay carries no `profiles:` key at all** — not an
oversight, a deliberate rejection. If `docker-compose.coder.yml` set
`profiles: ["coder-campaign"]` on `worker-2`, and the base file's `worker-2`
has no `profiles` (i.e., always-on), Compose's merge rule for the
`profiles` list is *also* concatenation: `[] + ["coder-campaign"] =
["coder-campaign"]`. The moment the overlay file is loaded — regardless of
what `COMPOSE_PROFILES` is set to — the merged `worker-2` would inherit
that gate and **stop starting by default**, silently turning "always-on
generic worker" into "off unless you also remembered to set
`COMPOSE_PROFILES=coder-campaign`". That's a strictly worse failure mode
than what a profile is supposed to prevent, so the overlay file's own
inclusion (via `-f` or `.env`'s `COMPOSE_FILE`) is the *only* opt-in switch.

### Usage

```bash
# One-off:
docker compose -f docker-compose.yml -f docker-compose.coder.yml up -d

# Or, to avoid repeating -f on every command, in .env:
COMPOSE_FILE=docker-compose.yml:docker-compose.coder.yml   # Linux/macOS
COMPOSE_FILE=docker-compose.yml;docker-compose.coder.yml   # Windows
```

Compose reads `COMPOSE_FILE` (like `COMPOSE_PROFILES`) from the project's
`.env` automatically, so `install.ps1`/`install.sh`'s plain
`docker compose up -d --force-recreate` picks the overlay up too once it's
set — no change was needed to either install script for this.

### Role convention

The overlay assigns coding-backend roles to specific worker numbers by
**convention**, not by anything enforced in code:

| Generic worker | Coder-campaign role | Workspace volume | `CODING_BACKEND` |
|---|---|---|---|
| `worker-1` | `coder` (legacy, narration-only) | `repo` → `/data/repo` | *(unset — same as the old `worker-coder`, which never had a coding backend either)* |
| `worker-2` | `coder-native` | `repo-native` → `/data/repo` | `native` |
| `worker-3` | `coder-opencode` | `repo-opencode` → `/data/repo` | `opencode` |
| `worker-4` | `coder-aider` | `repo-aider` → `/data/repo` | `aider` |
| `worker-5` | `manager` | *(none needed)* | — |
| `worker-6` | `tester` | read-only: `repo`→`/data/repos/worker-1`, `repo-native`→`/data/repos/worker-2`, `repo-opencode`→`/data/repos/worker-3`, `repo-aider`→`/data/repos/worker-4` | — |
| `worker-7`, `worker-8` | *(spare/generic)* | — | — |

This lines up exactly with `app/agent.py`'s
`WORKSPACE_MOUNT_PATTERN = "/data/repos/{coder_id}"` **with no
`agent.workspaces` config override needed**: `coder_id` there is
`msg["from"]`, i.e. the sending container's own `WORKER_ID` — since that's
now literally `worker-1`/`worker-2`/`worker-3`/`worker-4`, the pattern's own
default already resolves to exactly the paths `worker-6` mounts above. (This
is a nicer property than the old setup had: the old `_resolve_workspace`
fallback happened to line up with the old hardcoded worker ids too, but only
because they were spelled `coder`/`coder-native`/etc. — this generalizes it
to any worker index, for free.)

Cast the matching coder-campaign personas onto the same worker numbers when
starting the campaign, so the runtime identity (name/role/system_prompt/
voice/avatar, from `config/campaigns/coder/personas.yaml`) lines up with the
static workspace wiring above:

```
POST /campaigns/coder/start
{"cast": {"coder": "worker-1", "coder-native": "worker-2",
          "coder-opencode": "worker-3", "coder-aider": "worker-4",
          "manager": "worker-5", "tester": "worker-6"}}
```

Nothing enforces that mapping — get it wrong (e.g. cast `"coder-native"`
onto `worker-3` instead of `worker-2`) and the failure is specific and
diagnosable, not a crash: the tester's pytest run targets the wrong (or an
empty) workspace, because `CODING_BACKEND`/the repo volume were wired to
`worker-2` regardless of which persona ends up assigned to it.

## Migration from the six named workers

The six old services (`worker-coder`, `worker-coder-native`,
`worker-coder-opencode`, `worker-coder-aider`, `worker-manager`,
`worker-tester`) and their six `config/workers/*.yaml` files are superseded
by `worker-1`..`worker-8` + `config/worker.yaml`. `config/workers/*.yaml`
were left in place on disk (not deleted) as reference for whoever builds
`config/campaigns/coder/personas.yaml` — each old file's
`agent.name`/`system_prompt`/`voice.model_path`/`avatar.*` is exactly the
per-speaker persona data that file needs to carry. They are no longer
referenced by `docker-compose.yml`.

Old env vars removed from `.env.example` (`CODER_STREAM_KEY`,
`MANAGER_STREAM_KEY`, `TESTER_STREAM_KEY`, `CODER_NATIVE_STREAM_KEY`,
`CODER_OPENCODE_STREAM_KEY`, `CODER_AIDER_STREAM_KEY`, and the matching
`*_LAYOUT_PRESET`/`*_AVATAR_PROVIDER` variants) are replaced by the indexed
`WORKER_1_*` .. `WORKER_8_*` forms.

**Known drift accepted for this change**: `README.md` still describes the
old six service names throughout (container names in the "exec into a
worker" examples, the `.env` variable table, the "Deploy / redeploy"
walkthrough, etc.). `README.md` was intentionally left untouched here — it
wasn't in this change's file allowlist and rewriting it is a large,
separate pass. Anyone touching `README.md` next should reconcile it against
this document.

## Error Handling

- **No persona assigned** — `agent.role: custom` never matches a role gate,
  so every message handler no-ops; combined with `worker_control.py`'s
  existing fail-open `is_enabled()` check, a freshly-started blank worker
  is inert but not crash-prone, exactly as CONTRACT §8 requires.
- **Overlay file not loaded, but a coding-backend persona is cast anyway**
  — the worker boots with `coding_backend` absent (narration-only default,
  `app/coding_backend.py`'s existing behavior when the section/env is
  missing); no workspace volume exists, so any code-writing task silently
  produces no commit rather than crashing the container. This is a
  configuration mismatch to fix operationally (load the overlay), not a
  new failure mode this change introduces.
- **Overlay's cast-to-worker convention violated** — see "Role convention"
  above: specific, diagnosable (wrong/empty test workspace), never a crash.
- **Two files define the same environment key with different values** —
  cannot happen today: the overlay only ever sets keys the base file
  doesn't (`CODING_BACKEND`, `WORKSPACE_PATH`), verified by
  `tests/test_docker_compose_structure.py`.

## Dependencies

- `app/worker_control.py` — blank == disabled convention, unchanged.
- `app/agent.py` — `WORKSPACE_MOUNT_PATTERN`, role-gated `MESSAGE_HANDLERS`,
  unchanged (this file's job is to make Compose compatible with them, not to
  modify them).
- `app/campaign_control.py`, `config/campaigns/<campaign>/personas.yaml`,
  and the `/campaigns/*` message-api routes (CONTRACT §8) — owned by a
  parallel change; this document assumes their existence but this change
  did not implement them.
- PyYAML (`tests/test_docker_compose_structure.py`) — already a project
  dependency (`requirements.txt`).

## Usage Examples

```bash
# Default: eight blank, disabled generic workers + always-on infra services.
docker compose up -d

# Same, but also wire up the four coder-campaign workspaces:
docker compose -f docker-compose.yml -f docker-compose.coder.yml up -d

# Then assign personas at runtime (once app/campaign_control.py + the
# /campaigns/* routes land):
curl -X POST http://localhost:8090/campaigns/coder/start \
  -H 'Content-Type: application/json' \
  -d '{"cast": {"coder": "worker-1", "coder-native": "worker-2",
                 "coder-opencode": "worker-3", "coder-aider": "worker-4",
                 "manager": "worker-5", "tester": "worker-6"}}'
```

```bash
# Fully self-contained standalone deployment, both add-ons active:
# .env: COMPOSE_PROFILES=local-infra
#       COMPOSE_FILE=docker-compose.yml:docker-compose.coder.yml
docker compose up -d
```

## Changelog

- v1.0.0 (2026-07-26) — Initial version. Replaced the six hardcoded named
  workers with `worker-1`..`worker-8` generic workers; introduced
  `docker-compose.coder.yml` as the coder-campaign workspace overlay;
  rewrote `config/worker.yaml` as a genuinely blank default.
