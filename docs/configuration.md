# Configuration

All runtime behavior is config-driven — no code changes needed to retune an agent.

**`config/worker.yaml` is the canonical template of every worker parameter that exists.** Whenever a code change adds a new config-readable parameter (a new `agent.py`/backend key, a new `coding_backend`/`voice`/etc. field), add it to `config/worker.yaml` too — as a real default if every worker should get it, or commented-out with an explanation if it's optional/per-worker — with a comment describing what it controls, its default, and any env var override. Per-worker configs (`config/workers/*.yaml`) should only ever be a subset/override of what's documented there; `config/worker.yaml` must never fall behind what the code actually reads.

- `config/worker.yaml` — the annotated template/default worker config (role, name, system prompt, LLM/voice/avatar/stream/world-state/message-bus settings)
- `config/workers/coder.yaml`, `manager.yaml`, `tester.yaml` — per-role configs mounted into each container at `/config/worker.yaml`
- Environment variables (set via `docker-compose.yml` or `.env`) override config file values at runtime, notably: `STREAM_RTMP_URL`, `TUBER1_STREAM_KEY` / `TUBER6_STREAM_KEY` / `TUBER5_STREAM_KEY`, `TUBER0_STREAM_KEY` / `GM_LAYOUT_PRESET` (the GM / roundtable channel), `LLM_BASE_URL`, `DISPLAY_NUM`, `WORKER_ID`, `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`, `REDIS_URL`, `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`, `WORKER_KILL_FILE` (local kill switch path, default `/tmp/worker_disabled`), `GITEA_TOKEN` (manager only, gitea task backlog)
- Service-only env vars (not worker config): `AUTO_SUBMIT_DRAFTS` (default `false`) / `AUTO_SUBMIT_TIMEOUT_S` (default `60`) on `3layer-generator` — auto-submit finished publish jobs to message-api as review drafts ([docs/draft_submitter.md](draft_submitter.md)); it reuses `MESSAGE_API_URL`

Key sections inside a worker config:

| Section | Controls |
|---|---|
| `agent` | Role, display name, system prompt, tick rate, context window; `liveness_ttl_s` (TTL of the Redis liveness key, unset = `max(3 ticks, 15s)`), `bus_heartbeat_every` (send the `status_update` bus heartbeat every N ticks, default 12, 0 = off); `backlog.*` (manager task backlog, below) |
| `worker_control` | `kill_file` — the local emergency kill-switch path (default `/tmp/worker_disabled`; `WORKER_KILL_FILE` env wins). See "Worker on/off control" below |
| `llm` | Provider (`ollama` \| `claude`), base URL, model, temperature |
| `voice` | TTS for spoken replay narration: provider (`piper` \| `kokoro` \| `openai` \| `elevenlabs` \| `fake` \| `null`), Piper model path, per-speaker (boss/coder) voice overrides. `model_path` doubles as this worker's own distinct persona voice, since `speakers.coder` is empty by default — see [Rerun Theater](usage.md#rerun-theater--replaying-past-sessions-with-voices). Piper synthesizes locally by default (one loaded model kept resident per worker) or against a remote `piper.http_server` if `base_url` is set. See [docs/tts_client.md](tts_client.md) |
| `avatar` | Name, title, ASCII expression states, speech bubble sizing |
| `layout` | Which tmux layout preset to use (`layout.preset`: `coder` \| `tester` \| `manager`; `LAYOUT_PRESET` env overrides). Presets live in `config/layouts/`; reusable panel-type defaults in `config/panels/`. Optional per-pane overrides under `layout.panes.<id>`. |
| `stream` | RTMP URL/key, resolution, bitrate, fps |
| `world_state` | Shared state backend (`file` \| `redis`) and connection info |
| `message_bus` | Kafka backend, bootstrap servers, topic, and this worker's ID |
| `coding_backend` | Which tool writes real code (`provider`: `native` \| `opencode` \| `aider` \| `none`; `workspace`, `timeout_s`, optional `model` override). See [docs/coding_backend.md](coding_backend.md). |

## Task backlog (manager, opt-in)

`agent.backlog` (annotated in `config/worker.yaml`, **off by default**) lets
the manager pull the next task itself whenever the team has no task chain
in flight, instead of waiting for the operator to POST a `task_assignment`.
Keys: `enabled`, `source` (`file` | `gitea`), `file_path`, `state_path`,
`gitea.{base_url, owner, repo, label, in_progress_label, blocked_label,
close_on_success}`, `coders` (round-robin), `cooldown_s` (default 60),
`stale_after_s` (default 1800). The gitea source's token comes only from the
`GITEA_TOKEN` env var, which `docker-compose.yml` passes to `worker-manager`
alone. A file backlog needs its task list mounted into the manager (example:
`config/backlog.example.yaml`). Full behaviour: [docs/task_backlog.md](task_backlog.md).

## Worker on/off control (what's set up)

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
  [Turning a worker on/off](usage.md#turning-a-worker-onoff-no-redeploy) for
  `curl` examples. This is the integration point for a planned web GUI
  worker manager.
- **Failure behavior**: reads fail open — a worker with no key yet, or a
  temporarily unreachable Redis, is treated as *enabled*. A control-plane
  hiccup can never silently take a live stream down. Writes do not fail
  open — the API returns HTTP 503 if a toggle couldn't be persisted.
- **Local kill switch**: while the file at `worker_control.kill_file`
  (`WORKER_KILL_FILE`, default `/tmp/worker_disabled`) exists inside a
  worker container, that worker is OFF without consulting Redis — so it
  works with Redis/message-api down. Created/removed by
  `scripts/emergency_stop.sh` / `emergency_resume.sh` (or `.ps1` over SSH),
  or `kill -USR1` to `stream_supervisor.py`. Survives `docker restart`, not
  container re-creation. Never create it in the `message-api` container.
- **Liveness**: every tick (even while disabled) the agent writes
  `worker:{id}:alive` (JSON `{ts, local_override, ttl_s}`, TTL
  `agent.liveness_ttl_s`); `GET /workers/health` and the control panel's
  health column read it.
- **Full design**: [docs/worker_control.md](worker_control.md) and
  [docs/stream_supervisor.md](stream_supervisor.md).

## Tmux layout (config-driven)

The worker's tmux panes are declarative config, not baked into `startup.sh`. A
worker config picks a preset (`layout.preset`) from `config/layouts/*.yaml`, which
places and sizes reusable panel types from `config/panels/*.yaml`. **Reorder,
resize, retitle, or disable a pane by editing config only** — no `startup.sh` edit
or image rebuild. The rich Kafka "Message Bus" feed pane (`config/panels/kafka_feed.yaml`)
is configured the same way (colors, type filters, payload controls, and the
optional correlation-tag column `content.correlation.show|chars|color`, off by
default — [docs/message_bus_feed.md](message_bus_feed.md)). See
[docs/layout_system.md](layout_system.md) and [docs/panels.md](panels.md).

The layered config maps directly onto **Kubernetes ConfigMaps** — `config/panels/`
becomes one shared ConfigMap, each `config/layouts/*.yaml` a small per-role
ConfigMap; reconfigure a role by editing its layout ConfigMap and restarting the
pod. Details in [docs/layout_system.md](layout_system.md#kubernetes-configmap-mapping).
