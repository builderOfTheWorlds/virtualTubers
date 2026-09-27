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
   └─► control-panel ───┘   └─► Redis           └─► message-logger ──► Postgres (app)
       :8091                    (on/off flags,
twitch-presence ──viewer_joined─┘ log excludes)

Worker container:  agent.py ─► LLM / coding backend / test runner / git
                   agent.py ─► agent_state file ─► avatar.py ─► tmux avatar pane
                   agent.py ─► replay request file ─► replay_pane.py ─► TTS ─► PulseAudio
                   tmux (Xvfb+xterm) + PulseAudio vout ─► ffmpeg (stream_supervisor.py) ─► Twitch

Offline:  source_pipeline ─► campaigns/<pack> ─► app/campaign | 3layer-generator :8001
          ─► episode builder scripts ─► POST /replays (message-api) ─► replay_episodes
```

Two independent halves: the **live stream stack** (always on) and the
**offline content generator** (on demand). They meet only through
`POST /replays` on `message-api`.

---

## 2. Components and where they live

| Component | Path | Role |
|---|---|---|
| Agent loop | `app/agent.py` | Consumes Kafka, dispatches by `type` via `MESSAGE_HANDLERS`, heartbeats every tick |
| LLM client | `app/llm_client.py` | `llm.provider: ollama \| claude` |
| Coding backends | `app/coding_backend.py`, `app/coding_backends/` | `native \| opencode \| aider`, returns `TaskResult` |
| Git | `app/git_client.py` | Per-persona local commits; push/PR is a no-op until `GIT_SERVER_URL` |
| Workspace seeding | `app/workspace_setup.py`, `sandbox/` | Seeds coder volumes from the seeded-bug sandbox |
| Test runner | `app/test_runner.py` | Tester runs real pytest on a tmpdir copy of a coder workspace |
| Message bus | `app/message_bus.py` | Kafka producer/consumer + `build_message()` |
| Worker on/off | `app/worker_control.py` | Redis key `worker:{id}:enabled` (reads fail open) |
| Log filter | `app/log_filter_control.py` | Redis key `logfilter:{type}:excluded` |
| Stream | `app/stream_supervisor.py` | Starts/stops ffmpeg from the on/off flag |
| Layout | `app/build_layout.py`, `config/layouts/`, `config/panels/` | Emits the tmux session + pane commands |
| Pane control | `app/tmux_control.py` | Agent "types" into panes by name |
| Avatar | `app/avatar.py`, `app/avatar_providers/` | `builtin \| ascii_avatar \| termgl_avatar \| codec_avatar` |
| Avatar inputs | `app/agent_state.py`, `app/emotion.py`, `app/gaze.py` | State file, emotion, synced head turns |
| 3D heads | `app/character_schema.py`, `app/head_mesh.py`, `app/codec_head.py`, `app/character_preview.py` | Slider-driven character generator |
| Rerun Theater | `app/replay_pane.py`, `app/replay.py`, `app/revoice.py` | Episode playback, narration, duets, roundtable |
| Episode library | `app/episode_store.py`, `app/episode_validator.py` | Postgres `replay_episodes` + upload gate |
| Narration cache | `app/narration_store.py` | Postgres `voiced_narration` |
| Voice | `app/tts_client.py`, `app/voice_registry.py`, `app/voice_gate.py`, `app/audio_player.py` | `piper \| openai \| elevenlabs`, one-line-at-a-time gate |
| Roundtable tiles | `app/tile_pane.py`, `app/tile_avatar.py` | GM-hosted character tiles |
| Session logs → scripts | `app/session_log_parser.py`, `scripts/build_replay_library.py` | Redacted replay scripts from Claude Code logs |
| Campaign layer | `app/campaign/` | Pack loading, validation, scene graph, runtime, improviser |
| Services | `services/message-api`, `message-logger`, `control-panel`, `twitch-presence`, `log-shipper`, `campaign-manager`, `3layer-generator` | See §6 |
| Entrypoint | `startup.sh` | Boot order in §7 |

---

## 3. Message contract

Every bus message is built by `app/message_bus.py:build_message()`:

```json
{"id": "<uuid4>", "from": "<worker_id>", "to": "<worker_id|manager|tester|operator|broadcast>",
 "type": "<message type>", "payload": {...}, "timestamp": "<ISO-8601 UTC>"}
```

- One topic: `vtuber.messages` (`KAFKA_TOPIC`). Every agent is both producer
  and consumer and filters on `to`.
- External injection goes only through `message-api` `POST /messages`.
- Handlers are role-gated: a handler checks `agent_config["role"]` and ignores
  messages meant for another role.

### Handled types (`MESSAGE_HANDLERS`, `app/agent.py`)

| Type | Handled by role | Effect |
|---|---|---|
| `task_assignment` | coder | Narrate → coding backend → commit → send `coding_run_report` (broadcast), `task_complete` → manager, `commit_notification` → tester. On failure: `clarification_request` → manager |
| `commit_notification` | tester | Run pytest → `test_passed` or `bug_report` → manager |
| `retest_request` | tester | Same as above, re-run tests |
| `test_passed` | manager | `manager_report` (`milestone`) → operator |
| `bug_report` | manager | `task_assignment` (fix, `retry_count+1`) → coder, or `manager_report` (`escalation`) once `retry_count >= MAX_BUG_RETRIES` (3) |
| `task_complete` | manager | Acknowledge only. **Deliberately no send** — the coder's `commit_notification` already drives the tester |
| `clarification_request` | manager | `manager_report` (`blocker`) → operator. Deliberately does not auto-reassign |
| `operator_message` | any | Direct operator → worker chat (message-api's default type); answers `operator_reply` → operator |
| `replay_request` / `replay_stop` | any | Writes the replay request/stop file for `replay_pane.py` |
| `viewer_joined` | any | Queues a rerun for the viewer (`payload.episode` or a random library pick) and greets them |
| `replay_invite` / `replay_ready` / `replay_cue` / `replay_end` | any | Duet relay (see §5.3) |

Emitted but not handled by agents: `status_update` (heartbeat, broadcast),
`coding_run_report` (logged by message-logger into `coding_backend_runs`),
`manager_report` and `operator_reply` (to operator).

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

---

## 5. Feature flows

### 5.1 Dev-team task loop

1. Operator → `POST /messages {"to":"coder","type":"task_assignment","payload":{"task":...}}`.
2. Coder (`handle_task_assignment`): LLM narration → `coding_backend.run()` in
   the workspace → `git_client` commit → broadcast `coding_run_report` →
   `task_complete` to manager → `commit_notification` to tester.
3. Tester (`handle_commit_notification` → `test_runner`): pytest on a copy →
   `test_passed` or `bug_report` to manager.
4. Manager: pass → `manager_report milestone`; fail → re-assign fix up to 3
   times, then `manager_report escalation`.
5. Any blocker → `clarification_request` → `manager_report blocker`.

Throughout, each agent writes `agent_state` (thinking/speaking/listening/
error + emotion + bubble text) which `avatar.py` renders.

### 5.2 Rerun Theater (solo)

1. **Ingest** — session logs (`session_log_parser`), campaign/generated
   episodes (builder scripts), or control-panel upload → `POST /replays` →
   `episode_validator` (shape, names, leak audit, dry-run render) →
   `episode_store` INSERT into `replay_episodes`. This is the only write path.
2. **Request** — `replay_request` message (operator, control panel, or random
   pick on `viewer_joined`) → `agent.py` writes `REPLAY_REQUEST_FILE`
   (default `/tmp/replay_request.json`).
3. **Prepare** — `replay_pane.py` loads the episode; if the airing is cached in
   `narration_store` it reuses it, else `revoice.prepare_show()` plans scenes,
   LLM writes spoken lines, `tts_client` synthesizes per-character voices, and
   the airing is persisted.
4. **Perform** — `replay.Performer` plays scenes paced to audio; each line goes
   through `voice_gate` (shared lock files, one voice at a time unless the
   episode sets `show.audio.max_concurrent`) → `audio_player` → PulseAudio
   `vout`.
5. **Stop** — `replay_stop` → `REPLAY_STOP_FILE` (default `/tmp/replay_stop.json`).

### 5.3 Duet (multi-worker)

Director and followers are separate containers coordinating over Kafka
(details: [duet_replay.md](duet_replay.md)):
prepare → persist airing → `replay_invite` → followers load the same airing →
`replay_ready` → director ratchets `replay_cue` per scene → `replay_end`.
Inside each container, `agent.py` relays these to `replay_pane.py` via the cue
and ready files (`REPLAY_CUE_FILE`, `REPLAY_READY_FILE`). **Duets never
degrade**: if a follower can't join, the show is refused rather than aired
partially.

### 5.4 Roundtable (GM)

The GM worker (`tuber_0`) runs narration + TTS for every speaker and hosts up
to 8 character tiles **inside its own container**, cueing them through files
in `TILE_RELAY_DIR` (default `/tmp/tiles`, `app/tile_pane.py`). This is not a
cross-container mechanism — other workers are only reachable via the §5.3
Kafka protocol. `gaze.py` turns tile heads toward the active speaker.

### 5.5 Worker on/off

`control-panel` / `POST /workers/{id}/enable|disable` → Redis
`worker:{id}:enabled` → `agent.py` pauses its loop and `stream_supervisor.py`
stops ffmpeg. Reads fail open (Redis down = worker stays on).

### 5.6 Logging

- Bus: `message-logger` consumes everything → Postgres `messages`
  (skipping types excluded via `logfilter:{type}:excluded`).
- Containers: `log-shipper` reads docker.sock → Postgres `container_logs`.
- Pruning: `POST /logs/prune` (`app/log_prune.py`).

### 5.7 Content generation (offline)

`utilities/source_pipeline` (source work → cast, timeline, profiles) →
`campaigns/<pack>/` (`campaign.yaml`, `cast/`, `lore/`, `scenes/`) →
either `app/campaign` (scene graph runtime/improviser) or `3layer-generator`
(`POST /jobs`, Arc → Segment → Dialogue, own Postgres on :5455, GUI via
`campaign-manager` :8082) → builder scripts in `.claude/prompts/`
(`build_campaign_episode.py`, `build_generated_episode.py`) →
`POST /replays`. **Nothing is automatic** — generated content is not airable
until a builder posts it.

---

## 6. Services and HTTP surface

| Service | Port | Key endpoints |
|---|---|---|
| `message-api` | 8090 | `POST /messages`; `GET/POST /workers/{id}[/enable\|/disable]`; `/log-filter/{type}[/exclude\|/include]`; `/console-theme[s]`; `POST /logs/prune`, `GET /logs/containers`, `GET /logs/messages`; `POST/GET /replays`, `GET/DELETE /replays/{name}` |
| `control-panel` | 8091 | Browser UI over message-api |
| `campaign-manager` | 8082 | Job GUI over 3layer-generator |
| `3layer-generator` | 8001 | `POST/GET /jobs`, `/jobs/{id}[/cancel]`, `POST /preview`, `GET /packs/...` |
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
  writer of `replay_episodes`.
- Two Postgres instances: app DB (`POSTGRES_HOST`, messages/episodes/narration/
  logs) and generator DB (`127.0.0.1:5455`, generator jobs only). Never mix them.
- Redis holds only on/off flags and log-filter excludes. World state is a
  file per worker (`world_state.backend: file`), not Redis.
- `task_complete` must not trigger a send; the manager does not auto-reassign
  on `clarification_request`.
- Worker config precedence: env var > `config/workers/<name>.yaml` > `config/worker.yaml`.
- `docker compose up` never builds the worker image — rebuild `vtube-worker:latest` after code changes.
- Duets never degrade; voice gate never withholds audio (it only serializes).

## 9. Where to read next

| Topic | Doc |
|---|---|
| Agent loop | [agent.md](agent.md) |
| Messaging | [message_bus.md](message_bus.md), [message_api.md](message_api.md), [operator_commands.md](operator_commands.md) |
| Rerun Theater | [replay.md](replay.md), [replay_pane.md](replay_pane.md), [revoice.md](revoice.md), [duet_replay.md](duet_replay.md), [voice_gate.md](voice_gate.md) |
| Rendering | [layout_system.md](layout_system.md), [panels.md](panels.md), [avatar.md](avatar.md), [character_generator.md](character_generator.md), [tile_pane_rendering.md](tile_pane_rendering.md) |
| Config & deploy | [configuration.md](configuration.md), [deployment.md](deployment.md) |
| Campaigns | [campaign_module_status.md](campaign_module_status.md), [campaign_pack_format.md](campaign_pack_format.md) |
| Database | [database_schema.md](database_schema.md) |
