# Agent Flow Reference

> **Audience: AI coding agents working in this repo.** Read this after
> `README.md` to understand how the system flows before changing code.
> It is plain text on purpose (no diagrams needed) and every claim points at a
> file. Humans wanting pictures: [feature_flow_diagram.md](feature_flow_diagram.md)
> (features) and [architecture_flow_diagram.md](architecture_flow_diagram.md)
> (data ownership).
>
> If you change a flow described here, update this file in the same commit.

---

## 1. System at a glance

```
Operator ──curl──► message-api :8090 ──► Kafka (vtuber.messages) ◄──► agent.py (every worker)
   │                    ▲   │                   │
   └─► control-panel ───┘   └─► Redis ◄─────────┤  (liveness key worker:{id}:alive every tick)
       :8091                    (on/off flags,  └─► message-logger ──► Postgres (app)
twitch-presence ──viewer_joined─┘ log excludes,
                                  liveness keys)

Worker container:  agent.py ─► agent_handlers/ ─► LLM / coding backend / test runner / git
                   agent.py ─► agent_state file ─► avatar.py ─► tmux avatar pane
                   agent_handlers ─► relay_io files ─► replay_pane.py ─► TTS ─► PulseAudio
                   tmux (Xvfb+xterm) + PulseAudio vout ─► ffmpeg (stream_supervisor.py) ─► Twitch
                   kill file (WORKER_KILL_FILE) ─► forces agent + ffmpeg OFF, no Redis needed

Offline:  source_pipeline ─► campaigns/<pack> ─► app/campaign | 3layer-generator :8001
          ─► builder scripts / generator publish (+ opt-in auto-submit as DRAFT)
          ─► POST /replays (message-api) ─► replay_episodes (draft | approved)
```

Two independent halves: the **live stream stack** (always on) and the
**offline content generator** (on demand). They meet only through
`POST /replays` on `message-api`.

---

## 2. Components and where they live

| Component | Path | Role |
|---|---|---|
| Agent loop | `app/agent.py` | Consumes Kafka, dispatches by `type` via `MESSAGE_HANDLERS`, calls the role's `IDLE_TICK_HOOKS` hook, writes the Redis liveness key every tick, sends the `status_update` bus heartbeat every `agent.bus_heartbeat_every` ticks |
| Message handlers | `app/agent_handlers/` | One module per role/concern: `coder.py`, `tester.py`, `manager.py` (+ task backlog dispatcher), `operator.py`, `viewer.py`, `replay_relay.py`, `relay_files.py`, `common.py`; `__init__.py` builds `MESSAGE_HANDLERS` and `IDLE_TICK_HOOKS` |
| Task backlog | `app/task_backlog.py` | Opt-in task sources (`file` \| `gitea`) the manager pulls from when idle |
| LLM client | `app/llm_client.py` | `llm.provider: ollama \| claude` |
| Coding backends | `app/coding_backend.py`, `app/coding_backends/` | `native \| opencode \| aider`, returns `TaskResult` |
| Git | `app/git_client.py` | Per-persona local commits; push/PR is a no-op until `GIT_SERVER_URL` |
| Workspace seeding | `app/workspace_setup.py`, `sandbox/` | Seeds coder volumes from the seeded-bug sandbox |
| Test runner | `app/test_runner.py` | Tester runs real pytest on a tmpdir copy of a coder workspace |
| Message bus | `app/message_bus.py` | Kafka producer/consumer + `build_message()`, `reply_ids()`, `correlation_of()` |
| Worker on/off + health | `app/worker_control.py` | Redis `worker:{id}:enabled` (reads fail open), local kill-file override, liveness key `worker:{id}:alive`, `health()`/`health_many()` rows |
| Log filter | `app/log_filter_control.py` | Redis key `logfilter:{type}:excluded` |
| Stream | `app/stream_supervisor.py` | Starts/stops ffmpeg from the on/off flag; checks the kill file every 0.5 s; SIGUSR1 = emergency stop |
| Relay files | `app/relay_io.py` | The one race-safe implementation of in-container JSON relay files (atomic write, tolerant read, claim-then-consume) |
| Layout | `app/build_layout.py`, `config/layouts/`, `config/panels/` | Emits the tmux session + pane commands |
| Pane control | `app/tmux_control.py` | Agent "types" into panes by name |
| Avatar | `app/avatar.py`, `app/avatar_providers/` | `builtin \| ascii_avatar \| termgl_avatar \| codec_avatar` |
| Avatar inputs | `app/agent_state.py`, `app/emotion.py`, `app/gaze.py` | State file, emotion, synced head turns |
| 3D heads | `app/character_schema.py`, `app/head_mesh.py`, `app/codec_head.py`, `app/character_preview.py` | Slider-driven character generator |
| Rerun Theater | `app/replay_pane.py`, `app/replay.py`, `app/revoice.py` | Episode playback, narration, duets, roundtable |
| Episode library | `app/episode_store.py`, `app/episode_validator.py` | Postgres `replay_episodes` (`status` `draft \| approved`) + upload gate |
| Narration cache | `app/narration_store.py` | Postgres `voiced_narration` |
| Voice | `app/tts_client.py`, `app/voice_registry.py`, `app/voice_gate.py`, `app/audio_player.py` | `piper \| openai \| elevenlabs`, one-line-at-a-time gate |
| Roundtable tiles | `app/tile_pane.py`, `app/tile_avatar.py` | Character tiles hosted by the roundtable container |
| Session logs → scripts | `app/session_log_parser.py`, `scripts/build_replay_library.py` | Redacted replay scripts from Claude Code logs |
| Campaign layer | `app/campaign/` | Pack loading, validation, scene graph, runtime, improviser |
| Services | `services/message-api`, `message-logger`, `control-panel`, `twitch-presence`, `log-shipper`, `campaign-manager`, `3layer-generator` | See §6 |
| Emergency scripts | `scripts/emergency_stop.sh\|.ps1`, `scripts/emergency_resume.sh\|.ps1` | Create/remove the kill file via `docker exec` (§5.5) |
| Entrypoint | `startup.sh` | Boot order in §7 |

---

## 3. Message contract

Every bus message is built by `app/message_bus.py:build_message()`:

```json
{"id": "<uuid4>", "from": "<worker_id>", "to": "<worker_id|manager|tester|operator|broadcast>",
 "type": "<message type>", "payload": {...}, "timestamp": "<ISO-8601 UTC>",
 "correlation_id": "<uuid: id of the chain's first message>", "causation_id": "<uuid of the direct cause | null>"}
```

- One topic: `vtuber.messages` (`KAFKA_TOPIC`). Every agent is both producer
  and consumer and filters on `to`.
- External injection goes only through `message-api` `POST /messages`
  (always `from: "operator"`, starts a new chain).
- Handlers are role-gated: a handler checks `agent_config["role"]` and ignores
  messages meant for another role.
- **Correlation rule** (`app/message_bus.py` `reply_ids()`, docs/message_bus.md
  "Correlation IDs"): a message sent *because of* `msg` passes
  `**reply_ids(msg)` → same `correlation_id` as `msg` (falling back to
  `msg["id"]` for older senders), `causation_id = msg["id"]`. A message sent
  without a cause (operator injection, heartbeat, replay relay, viewer rerun,
  a backlog dispatch) starts a new chain: `correlation_id == id`,
  `causation_id == null`. The dev-team handlers (`coder`, `tester`,
  `manager`, `operator`, `common._send_manager_report`) all follow it, so a
  task and all its bug-fix retries share one `correlation_id`.
  `message-logger` stores both ids in `messages` (UUID columns, indexed on
  `correlation_id`); the feed pane can show a correlation tag
  (`content.correlation.show`, off by default).

### Handled types (`MESSAGE_HANDLERS`, `app/agent_handlers/__init__.py`)

| Type | Handled by role | Effect |
|---|---|---|
| `task_assignment` | coder (`coder.py`) | Narrate → coding backend → commit → send `coding_run_report` (broadcast), `task_complete` → **the task's sender** (`msg.from`: `operator` for an operator task, `manager` for a fix re-assignment or backlog task), `commit_notification` → tester. Backend failure: `clarification_request` → `manager` (always). Narration-LLM failure with no successful backend run: `clarification_request` → **the task's sender** (see note below) |
| `commit_notification` | tester (`tester.py`) | Run pytest → `test_passed` or `bug_report` → manager |
| `retest_request` | tester | Same as above, re-run tests |
| `test_passed` | manager (`manager.py`) | `manager_report` (`milestone`) → operator (fallback narration if the manager's LLM fails) |
| `bug_report` | manager | `task_assignment` (fix, `retry_count+1`, same chain) → originating coder (`payload.coder_id`), or `manager_report` (`escalation`) once `retry_count >= MAX_BUG_RETRIES` (3) or the manager's LLM fails |
| `task_complete` | manager | Acknowledge only. **Deliberately no send** — the coder's `commit_notification` already drives the tester. Only reaches the manager when the manager sent the task |
| `clarification_request` | manager | `manager_report` (`blocker`) → operator. Deliberately does not auto-reassign |
| `operator_message` | any (`operator.py`) | Direct operator → worker chat (message-api's default type); answers `operator_reply` → operator |
| `replay_request` / `replay_stop` | any (`replay_relay.py`) | Writes the replay request/stop file for `replay_pane.py` |
| `viewer_joined` | any (`viewer.py`) | Queues a rerun for the viewer (`payload.episode` or a random **approved** library pick) and greets them |
| `replay_invite` / `replay_ready` / `replay_cue` / `replay_end` | any (`replay_relay.py`) | Duet relay (see §5.3) |

Emitted but not handled by agents: `status_update` (bus heartbeat,
broadcast, every `agent.bus_heartbeat_every` ticks — default 12, 0 = off),
`coding_run_report` (logged by message-logger into `coding_backend_runs`),
`manager_report` and `operator_reply` (to operator). A `task_complete` or
`clarification_request` addressed to `operator` is also unhandled (the
operator reads it in the feed / `messages` table).

> **Known quirk (current behaviour, low priority):** a narration-only coder
> (no coding backend) whose LLM fails sends `clarification_request` to the
> task's sender (`app/agent_handlers/coder.py:148-152`). For an
> operator-assigned task that is `operator`, so the manager never raises a
> `blocker`. The backend-failure path (`coder.py:115-119`) always goes to
> `manager`. `clarification_request` is not in active use; the strict xfail
> `tests/test_e2e_dev_loop.py::test_narration_only_coder_llm_failure_reaches_manager`
> is kept as the record (docs/e2e_tests.md).

---

## 4. Workers

All workers run the same image `vtube-worker:latest` (`pull_policy: never` —
rebuild after code changes). Config is `config/worker.yaml` (full template)
overridden by the mounted `config/workers/<name>.yaml`, overridden by env vars.

| Compose service | `WORKER_ID` | Config | Stream key env |
|---|---|---|---|
| `worker-coder` | `coder` | `worker.yaml` / `coder.yaml` | `TUBER1_STREAM_KEY` |
| `worker-coder-native` | `coder-native` | `coder-native.yaml` | `TUBER2_STREAM_KEY` |
| `worker-coder-opencode` | `coder-opencode` | `coder-opencode.yaml` | `TUBER3_STREAM_KEY` |
| `worker-coder-aider` | `coder-aider` | `coder-aider.yaml` | `TUBER4_STREAM_KEY` |
| `worker-tester` | `tester` | `tester.yaml` | `TUBER5_STREAM_KEY` |
| `worker-manager` | `manager` | `manager.yaml` | `TUBER6_STREAM_KEY` |
| `worker-gm` | `tuber_0` | `tuber_0.yaml` | `TUBER0_STREAM_KEY` |
| `worker-roundtable` | `roundtable` | `roundtable.yaml` | `ROUNDTABLE_STREAM_KEY` |

Only `worker-manager` receives `GITEA_TOKEN` (gitea task backlog, §5.8).

---

## 5. Feature flows

### 5.1 Dev-team task loop

1. A task arrives: operator → `POST /messages {"to":"coder","type":"task_assignment","payload":{"task":...}}`,
   or the manager's backlog dispatcher (§5.8). Either starts a new
   correlation chain.
2. Coder (`agent_handlers/coder.py` `handle_task_assignment`): coding backend
   run in the workspace → `git_client` commit → broadcast `coding_run_report`
   → LLM narration → `task_complete` to the task's sender →
   `commit_notification` to tester. All sends carry `reply_ids(msg)`.
3. Tester (`agent_handlers/tester.py` → `test_runner`): pytest on a copy →
   `test_passed` or `bug_report` to manager.
4. Manager (`agent_handlers/manager.py`): pass → `manager_report milestone`;
   fail → re-assign fix to `payload.coder_id` up to 3 times (same chain),
   then `manager_report escalation`.
5. Blockers → `clarification_request` → `manager_report blocker`. A coding
   backend failure always reaches the manager; a narration-LLM failure goes
   to the task's sender (quirk in §3).

The whole chain — task, retries, reports — shares one `correlation_id`;
query `messages WHERE correlation_id = ...` to follow it.

Throughout, each agent writes `agent_state` (thinking/speaking/listening/
error + emotion + bubble text) which `avatar.py` renders.

### 5.2 Rerun Theater (solo)

1. **Ingest** — session logs (`session_log_parser`), campaign/generated
   episodes (builder scripts, generator publish/auto-submit), or
   control-panel upload → `POST /replays[?status=draft]` →
   `episode_validator` (shape, names, leak audit, dry-run render) →
   `episode_store` INSERT into `replay_episodes`. This is the only write
   path. `status` defaults to `approved` (airs immediately); `draft` is held
   for review.
2. **Review (drafts only)** — the control panel's "Drafts awaiting review"
   table (`GET /replays?status=draft`) → Approve (`POST /replays/{name}/approve`,
   idempotent) or Reject (`DELETE /replays/{name}`). `GET /replays/{name}`
   includes drafts so they can be read before approving.
3. **Request** — `replay_request` message (operator, control panel, or random
   pick on `viewer_joined`) → `agent_handlers/replay_relay.py` writes
   `REPLAY_REQUEST_FILE` (default `/tmp/replay_request.json`) via
   `app/relay_io.py`.
4. **Prepare** — `replay_pane.py` consumes the request file
   (`relay_io.consume_json`), loads the episode — **approved only**
   (`episode_store.load_episode` / `list_episodes` filter `status =
   'approved'`, so a draft can never be requested or randomly picked) — if the
   airing is cached in `narration_store` it reuses it, else
   `revoice.prepare_show()` plans scenes, LLM writes spoken lines,
   `tts_client` synthesizes per-character voices, and the airing is persisted.
5. **Perform** — `replay.Performer` plays scenes paced to audio; each line goes
   through `voice_gate` (shared lock files, one voice at a time unless the
   episode sets `show.audio.max_concurrent`) → `audio_player` → PulseAudio
   `vout`.
6. **Stop** — `replay_stop` → `REPLAY_STOP_FILE` (default `/tmp/replay_stop.json`).

### 5.3 Duet (multi-worker)

Director and followers are separate containers coordinating over Kafka
(details: [duet_replay.md](duet_replay.md)):
prepare → persist airing → `replay_invite` → followers load the same airing →
`replay_ready` → director ratchets `replay_cue` per scene → `replay_end`.
Inside each container, `agent_handlers/replay_relay.py` relays these to
`replay_pane.py` via the cue and ready files (`REPLAY_CUE_FILE`,
`REPLAY_READY_FILE`), all written/read through `app/relay_io.py`
(docs/relay_io.md). **Duets never degrade**: if a follower can't join, the
show is refused rather than aired partially.

### 5.4 Roundtable

The roundtable container (`worker-roundtable`, `WORKER_ID=roundtable`,
`config/workers/roundtable.yaml`) is the one roundtable director: it runs
narration + TTS for every speaker and hosts up to 8 character tiles **inside
its own container**, cueing them through files in `TILE_RELAY_DIR` (default
`/tmp/tiles`, `app/tile_pane.py`, via `app/relay_io.py`). Director gates
(`replay_pane.py` `_resolve_local_tiles`): `TILE_RELAY_DIR` set **and**
`layout.preset == roundtable` or `agent.role == roundtable`; exactly one
config may satisfy them (`tests/test_roundtable_layout.py`). The GM
(`worker-gm`, `tuber_0`) is an ordinary character channel, not the director.
The tile relay is not a cross-container mechanism — other workers are only
reachable via the §5.3 Kafka protocol. `gaze.py` turns tile heads toward the
active speaker.

### 5.5 Worker on/off, kill switch, health

- **Normal on/off:** `control-panel` / `POST /workers/{id}/enable|disable` →
  Redis `worker:{id}:enabled` → `agent.py` pauses its loop (no handlers, no
  idle hooks) and `stream_supervisor.py` stops ffmpeg. Reads fail open
  (Redis down = worker stays on).
- **Local kill switch** (`app/worker_control.py`): while the kill file
  (`WORKER_KILL_FILE` > `worker_control.kill_file` > `/tmp/worker_disabled`)
  exists in a worker container, `is_enabled()` returns False **without
  consulting Redis** — works with Redis/message-api down. Created by
  `scripts/emergency_stop.sh [worker ...]` (`docker exec`; `.ps1` does the
  same over SSH) or `kill -USR1` to `stream_supervisor.py`; removed by
  `scripts/emergency_resume.sh`. The supervisor checks it every 0.5 s.
  Survives `docker restart`, not container re-creation. Removing it does not
  force a worker on — Redis control resumes.
- **Liveness:** every tick, even while disabled, `agent.py` calls
  `WorkerControl.heartbeat()` → `SET worker:{id}:alive <json> EX ttl`, JSON
  `{"ts", "local_override", "ttl_s"}` (readers also accept the pre-1.3 plain
  ISO value). TTL: `agent.liveness_ttl_s`, else `max(3 ticks, 15 s)`. Never
  raises.
- **Health view:** `message-api` `GET /workers/health` (known ids + any id
  with a Redis key) and `GET /workers/{id}/health` → rows with `state`
  `alive | stale | down | unknown`, `last_seen`, `age_s`, `local_override`
  (the worker's own report), `enabled` (raw Redis flag), `ttl_s`. Never 503:
  Redis down reads as `unknown`. The control panel shows a health column and
  a kill-switch badge from it.

### 5.6 Logging

- Bus: `message-logger` consumes everything → Postgres `messages` incl.
  `correlation_id` / `causation_id` (skipping types excluded via
  `logfilter:{type}:excluded`; `status_update` is excluded by default).
- Containers: `log-shipper` reads docker.sock → Postgres `container_logs`.
- Pruning: `POST /logs/prune` (`app/log_prune.py`).

### 5.7 Content generation (offline)

`utilities/source_pipeline` (source work → cast, timeline, profiles) →
`campaigns/<pack>/` (`campaign.yaml`, `cast/`, `lore/`, `scenes/`) →
either `app/campaign` (scene graph runtime/improviser) or `3layer-generator`
(`POST /jobs`, Arc → Segment → Dialogue, own Postgres on :5455, GUI via
`campaign-manager` :8082) → into the library by one of:

- builder scripts in `.claude/prompts/` (`build_campaign_episode.py`,
  `build_generated_episode.py`) or the generator's manual
  `POST /publish/{run}/upload` → `POST /replays` (stored **approved**);
- **opt-in auto-submit** (`services/3layer-generator/draft_submitter.py`,
  `AUTO_SUBMIT_DRAFTS=true`, default off): when a `publish` job completes,
  `runner.dispatch_once` POSTs its `episode.json` to
  `MESSAGE_API_URL/replays?status=draft`. The outcome is recorded on the
  job as `result.auto_submit`; a submit failure never fails the job.

**Nothing airs automatically**: without a builder/upload nothing reaches
the library, and auto-submitted content is a draft that cannot air until an
operator approves it (§5.2 step 2).

### 5.8 Manager task backlog (opt-in)

`agent.backlog.enabled` (off by default; `config/worker.yaml`,
docs/task_backlog.md). `IDLE_TICK_HOOKS["manager"] = manager_idle_tick`
(`app/agent_handlers/manager.py`) runs once per **enabled** tick (a disabled
manager dispatches nothing) and drives one `BacklogDispatcher`:

1. Drop stale chains (no manager-visible activity for `stale_after_s`,
   default 1800) → marked `escalation`, plus a `manager_report escalation`
   (`reason: stale`) for backlog-started chains.
2. If **no chain is in flight** and `cooldown_s` (default 60, also applied
   after startup and after an empty poll) has passed: `source.next_task()`
   (`app/task_backlog.py`: `file` list with a JSON done-state, or `gitea`
   issues carrying `label`, token only from `GITEA_TOKEN`).
3. Send `task_assignment` to the next coder (round-robin over
   `backlog.coders`) with `backlog_id` / `backlog_source` in the payload —
   **no `reply_ids`, so it starts a new chain** — then `mark_started`.
4. The manager handlers report on the chain: `bug_report` / `task_complete`
   keep it open; `test_passed` → `milestone`, a bug escalation →
   `escalation`, `clarification_request` → `blocker` → `mark_done(outcome)`.
   Operator-posted chains the manager sees are tracked too, so the backlog
   never talks over them.
5. `file` source: a done item is never served again, whatever the outcome
   (a started-but-unfinished one is re-served after a restart). `gitea`
   source: `mark_done` comments the outcome and removes `in_progress_label`;
   `escalation`/`blocker` add `blocked_label` (skipped by `next_task` until a
   human removes it — the label must exist on the repo, else WARN); a
   `milestone` closes the issue only when `close_on_success`, otherwise it is
   skipped only by the in-memory handled set until the manager restarts.
   **A blocker is never retried automatically.**

---

## 6. Services and HTTP surface

| Service | Port | Key endpoints |
|---|---|---|
| `message-api` | 8090 | `GET /healthz`; `POST /messages`; `GET /workers/health`, `GET /workers/{id}/health` (never 503); `GET/POST /workers/{id}[/enable\|/disable]`; `/log-filter/{type}[/exclude\|/include]`; `/console-theme[s]`; `POST /logs/prune`, `GET /logs/containers`, `GET /logs/messages`; `POST /replays?status=approved\|draft`, `GET /replays?status=approved\|draft\|all` (default approved), `GET/DELETE /replays/{name}`, `POST /replays/{name}/approve` |
| `control-panel` | 8091 | Browser UI over message-api: worker table with health column + kill-switch badge, drafts-awaiting-review table (`/replays/{name}/approve`, `/replays/{name}/reject` = DELETE), library, log filters, message injection |
| `campaign-manager` | 8082 | Job GUI over 3layer-generator |
| `3layer-generator` | 8001 | `POST/GET /jobs`, `/jobs/{id}[/cancel]`, `POST /preview`, `GET /packs/...`, `POST /publish/{run}/upload`; env `AUTO_SUBMIT_DRAFTS` / `AUTO_SUBMIT_TIMEOUT_S` |
| `message-logger` | — | Kafka → Postgres |
| `twitch-presence` | — | Twitch chat → `viewer_joined` |
| `log-shipper` | — | docker logs → Postgres |
| `rtmp-preview` | 1935 | Local RTMP target for testing |

---

## 7. Worker boot order (`startup.sh`)

1. Clear stale Xvfb lock → start `Xvfb` at `CAPTURE_RESOLUTION`.
2. Restart PulseAudio (`--system`) and create null sink `vout` (failure = silent stream, logged).
3. `build_layout.py --phase session` → tmux session; `console_theme.py` → xterm theme.
4. `build_layout.py --phase panes` → panel commands (avatar, editor, bus feed, htop, ...).
5. `voice_registry.py --verify` (voice models present).
6. `agent.py` in a restart loop.
7. `replay_pane.py`, `theme_watcher.py` in background.
8. `stream_supervisor.py` → ffmpeg → RTMP (optionally tee'd to local preview).

---

## 8. Invariants — do not break

- `message-api` is the **only** external Kafka publisher and the **only**
  writer of `replay_episodes` (the generator's auto-submit goes over HTTP).
- Two Postgres instances: app DB (`POSTGRES_HOST`, messages/episodes/narration/
  logs) and generator DB (`127.0.0.1:5455`, generator jobs only). Never mix them.
- Redis holds only on/off flags, log-filter excludes and liveness keys
  (`worker:{id}:alive`). World state is a file per worker
  (`world_state.backend: file`), not Redis.
- `task_complete` must not trigger a send; the manager does not auto-reassign
  on `clarification_request`.
- **Drafts never air**: every worker read path (`load_episode`,
  `list_episodes`, the random `viewer_joined` pick) is approved-only;
  only message-api's review endpoints opt into drafts. New content paths
  that bypass review must be off by default.
- **The kill file is never created in the `message-api` container** (its
  `WorkerControl` would then report every worker disabled); health rows use
  the raw Redis flag, not `is_enabled()`, for the same reason.
- **The backlog never retries a blocker** (or an escalation): the file
  source marks it done; gitea items get `blocked_label`.
- Every new dev-team send made while handling a message passes
  `**reply_ids(msg)`.
- In-container relay files go through `app/relay_io.py` only (unique temp
  names, atomic publish, claim-then-consume).
- Worker config precedence: env var > `config/workers/<name>.yaml` > `config/worker.yaml`.
- `docker compose up` never builds the worker image — rebuild `vtube-worker:latest` after code changes.
- Duets never degrade; voice gate never withholds audio (it only serializes).

## 9. Where to read next

| Topic | Doc |
|---|---|
| Agent loop | [agent.md](agent.md), [agent_handlers.md](agent_handlers.md) |
| Messaging | [message_bus.md](message_bus.md), [message_api.md](message_api.md), [operator_commands.md](operator_commands.md), [message_bus_feed.md](message_bus_feed.md) |
| On/off, kill switch, health | [worker_control.md](worker_control.md), [stream_supervisor.md](stream_supervisor.md), [control_panel.md](control_panel.md) |
| Manager backlog | [task_backlog.md](task_backlog.md) |
| Rerun Theater | [replay.md](replay.md), [replay_pane.md](replay_pane.md), [revoice.md](revoice.md), [duet_replay.md](duet_replay.md), [voice_gate.md](voice_gate.md), [relay_io.md](relay_io.md), [episode_store.md](episode_store.md) |
| Draft review / auto-submit | [episode_store.md](episode_store.md), [draft_submitter.md](draft_submitter.md) |
| Rendering | [layout_system.md](layout_system.md), [panels.md](panels.md), [avatar.md](avatar.md), [character_generator.md](character_generator.md), [tile_pane_rendering.md](tile_pane_rendering.md) |
| Config & deploy | [configuration.md](configuration.md), [deployment.md](deployment.md) |
| Campaigns | [campaign_module_status.md](campaign_module_status.md), [campaign_pack_format.md](campaign_pack_format.md) |
| Database | [database_schema.md](database_schema.md) |
| Tests | [e2e_tests.md](e2e_tests.md) (multi-agent flow tests with fakes) |
