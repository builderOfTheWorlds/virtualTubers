# virtualTubers — Feature & Component Flow

A feature-level map of the whole project: what each major component does and
how work, content, and media flow between them. It complements
[architecture_flow_diagram.md](architecture_flow_diagram.md), which zooms in on
infrastructure (who writes which database table, which service is the sole
Kafka producer). This file zooms in on **features**.

Render the Mermaid blocks with any Mermaid-capable viewer (GitHub, VS Code
preview, Obsidian, mermaid.live).

1. [System overview](#1-system-overview) — every major component on one page
2. [Dev-team task loop](#2-dev-team-task-loop) — coder → tester → manager (operator or backlog as the task source)
3. [Rerun Theater](#3-rerun-theater) — session logs → voiced shows (solo, duet, roundtable)
4. [On-screen rendering](#4-on-screen-rendering) — tmux layout, avatar, stream out
5. [Content generation](#5-content-generation) — source works → campaign packs → episodes

---

## 1. System overview

```mermaid
flowchart LR
    subgraph IN["Inputs & control"]
        Op(["Operator"])
        CP["control-panel :8091<br/>worker on/off + health · log filter<br/>inject messages · replay library<br/>drafts awaiting review"]
        TP["twitch-presence<br/>watches Twitch chat"]
        ES_SH["emergency_stop / resume scripts<br/>docker exec, no Redis needed"]
    end

    subgraph CORE["Messaging & state"]
        MAPI["message-api :8090<br/>POST /messages · /replays<br/>GET /workers/health"]
        K[("Kafka<br/>vtuber.messages")]
        R[("Redis<br/>on/off flags · log excludes<br/>liveness worker:id:alive")]
        PG[("Postgres (app)<br/>messages · replay_episodes<br/>voiced_narration · logs")]
        MLOG["message-logger"]
        LSHIP["log-shipper"]
    end

    subgraph WORKER["Each worker container (vtube-worker image)"]
        direction TB
        AG["agent.py + agent_handlers<br/>perceive · think · act<br/>manager: opt-in task backlog"]
        KF["kill file<br/>WORKER_KILL_FILE"]
        LLM["llm_client<br/>Ollama | Claude"]
        CB["coding backends<br/>native | OpenCode | aider"]
        TR["test_runner<br/>real pytest"]
        GIT["git_client<br/>per-persona commits"]
        RP["replay_pane<br/>Rerun Theater"]
        TTS["tts_client + voice_gate<br/>Piper | OpenAI | ElevenLabs"]
        AV["avatar.py + providers<br/>emotion · gaze · 3D heads"]
        TMUX["tmux layout<br/>panels: avatar · editor · bus feed<br/>htop · radar · thinking · tiles"]
        SS["stream_supervisor<br/>Xvfb + PulseAudio + ffmpeg"]
    end

    subgraph OUT["Output"]
        TW(["Twitch<br/>1 channel per worker"])
        PREV(["rtmp-preview :1935<br/>local testing"])
    end

    subgraph GEN["Offline content generation"]
        SRC["source_pipeline<br/>books/notes → cast & profiles"]
        CAMP["campaigns/*<br/>packs: cast · lore · scenes"]
        CMGR["campaign-manager :8082"]
        G3["3layer-generator :8001<br/>Arc → Segment → Dialogue"]
        BLD["episode builder scripts"]
        BKL["task backlog<br/>file | Gitea issues"]
    end

    Op --> CP --> MAPI
    Op -->|curl| MAPI
    TP -->|viewer_joined| MAPI
    MAPI --> K
    MAPI --> R
    K <--> AG
    K --> MLOG --> PG
    LSHIP --> PG
    R -->|enabled?| AG
    R -->|enabled?| SS
    AG -->|liveness every tick| R
    R -->|health rows| MAPI
    ES_SH --> KF
    KF -->|forces OFF, checked before Redis| AG
    KF -->|forces OFF| SS
    BKL -.->|idle manager pulls next task| AG

    AG --> LLM
    AG --> CB --> GIT
    AG --> TR
    AG -->|replay_request / duet relay| RP
    RP --> TTS
    RP <-->|episodes · narration cache| PG
    AG -->|agent_state| AV
    RP --> AV
    AV --> TMUX
    CB --> TMUX
    TTS -->|audio| SS
    TMUX -->|screen| SS
    SS --> TW
    SS -.-> PREV

    SRC --> CAMP
    CMGR --> G3
    CAMP --> G3 --> BLD
    CAMP --> BLD
    BLD -->|POST /replays| MAPI
    G3 -.->|opt-in auto-submit<br/>POST /replays?status=draft| MAPI
```

**How to read it**

- **Inputs & control** — the operator drives everything through
  `message-api` (curl) or the browser `control-panel`; `twitch-presence` turns
  arriving viewers into `viewer_joined` bus events.
- **Messaging & state** — one Kafka topic carries every inter-agent message;
  `message-logger` archives it to Postgres (with each message's
  `correlation_id` / `causation_id`). Redis holds only the per-worker on/off
  flag, log-filter excludes and the per-worker liveness key, which
  `GET /workers/health` and the control panel's health column read.
- **Kill switch** — `scripts/emergency_stop.sh` (or `.ps1` over SSH) drops a
  kill file into worker containers with `docker exec`; while it exists the
  agent pauses and ffmpeg stops without asking Redis, so it works when
  Redis/message-api are down. `emergency_resume` removes it.
- **Worker container** — every character (coder variants, manager, tester,
  GM `tuber_0`, roundtable) runs the same image; `config/workers/*.yaml` picks
  its role, LLM, coding backend, avatar, voice, and tmux layout.
- **Output** — each worker's virtual screen + audio is encoded by ffmpeg and
  pushed to its own Twitch channel (optionally tee'd to the local preview).
- **Offline content generation** — separate, on-demand stack. Nothing reaches
  the live stream until an episode builder `POST`s it to `/replays`. The
  generator's opt-in auto-submit only ever posts **drafts**, which cannot air
  until an operator approves them in the control panel.
- **Task backlog** — optional: an idle manager pulls its next task from a
  file or Gitea issues instead of waiting for the operator (off by default).

---

## 2. Dev-team task loop

The original show: an AI dev team solving real tasks on the seeded-bug
`sandbox/` workspace. Handlers live in `app/agent_handlers/` and are
dispatched from `MESSAGE_HANDLERS` by `app/agent.py`.

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant API as message-api
    participant C as Coder
    participant T as Tester
    participant M as Manager

    alt operator task
        Op->>API: POST /messages (task_assignment)
        API->>C: task_assignment (new chain)
    else backlog task (opt-in, manager idle)
        Note over M: no chain in flight + cooldown passed<br/>next_task() from file or Gitea
        M->>C: task_assignment (new chain, backlog_id)
    end
    Note over Op,M: correlation chain: every later message carries the task's<br/>correlation_id and causation_id = the message that caused it
    Note over C: coding backend edits workspace<br/>git_client commits, LLM narrates
    Note over C: broadcasts coding_run_report<br/>(logged to coding_backend_runs)
    C->>Op: task_complete to the task's sender (operator task)
    C->>M: task_complete to the task's sender (backlog or fix task, acknowledged, no send)
    C->>T: commit_notification
    Note over T: test_runner runs pytest<br/>on a copy of the coder workspace
    alt tests pass
        T->>M: test_passed
        M->>Op: manager_report (milestone)
    else tests fail
        T->>M: bug_report
        alt retry_count < 3
            M->>C: task_assignment (fix, retry_count + 1, same chain)
        else retry cap reached
            M->>Op: manager_report (escalation)
        end
    end
    opt coder backend fails or tester is blocked
        C->>M: clarification_request
        M->>Op: manager_report (blocker, never auto-reassigned)
    end
    Note over M: backlog chains end on milestone, escalation or blocker<br/>and the item is marked done, never retried
```

`task_complete` goes to whoever sent the task (the operator for an operator
task, the manager for a fix or backlog task). A narration-only coder whose
LLM fails also sends its `clarification_request` to the task's sender — a
known, low-priority quirk (docs/agent_flow_reference.md §3). Every agent
refreshes its Redis liveness key each tick, sends a `status_update` bus
heartbeat every `agent.bus_heartbeat_every` ticks (default 12), and writes
`agent_state` so its avatar shows thinking / speaking / listening / error.

---

## 3. Rerun Theater

Past real sessions (and generated campaign episodes) replay as paced,
redacted, voiced shows.

```mermaid
flowchart TB
    subgraph INGEST["Build the library"]
        LOGS["Claude Code session logs"] --> SLP["session_log_parser<br/>redact → replay script"]
        GENEP["generated / campaign episodes"]
        SLP --> VAL
        GENEP --> VAL
        UP["control-panel upload"] --> VAL
        AUTO["3layer-generator auto-submit<br/>opt-in, AUTO_SUBMIT_DRAFTS"] -->|status=draft| VAL
        VAL["episode_validator<br/>shape · names · leak audit · dry-run"] --> STATUS{"status?"}
        STATUS -->|approved, the default| ES[("episode_store<br/>replay_episodes")]
        STATUS -->|draft| DRAFT[("draft row<br/>never airs")]
        DRAFT --> REVIEW{"control-panel<br/>Drafts awaiting review"}
        REVIEW -->|Approve| ES
        REVIEW -->|Reject = DELETE| GONE["removed"]
    end

    subgraph AIR["Air an episode"]
        REQ["replay_request<br/>operator · control-panel · random on viewer_joined"] -->|relay_io request file| RP["replay_pane"]
        ES -->|approved episodes only| RP
        RP --> CACHE{"narration cached?"}
        CACHE -->|yes| LOAD["load airing<br/>narration_store"]
        CACHE -->|no| REV["revoice<br/>plan scenes → LLM writes spoken lines"]
        REV --> TTS["tts_client<br/>per-character voices"] --> SAVE["persist airing<br/>narration_store"]
        LOAD --> PERF
        SAVE --> PERF
        PERF["replay.Performer<br/>paced, audio-synced playback"]
        PERF --> GATE["voice_gate<br/>one line at a time"] --> AUD["audio_player → PulseAudio"]
        PERF --> SCREEN["pane text + avatar speaking state"]
    end

    subgraph MODES["Show modes"]
        SOLO["Solo — one worker plays both voices"]
        DUET["Duet — director + followers over Kafka<br/>replay_invite → ready → cue → end"]
        RT["Roundtable — worker-roundtable hosts up to 8 tiles<br/>locally via TILE_RELAY_DIR files"]
    end
    PERF --- SOLO
    PERF --- DUET
    PERF --- RT
```

Details: [replay.md](replay.md), [replay_pane.md](replay_pane.md),
[revoice.md](revoice.md), [duet_replay.md](duet_replay.md),
[voice_gate.md](voice_gate.md), [episode_validator.md](episode_validator.md),
[episode_store.md](episode_store.md) (draft/approved), [draft_submitter.md](draft_submitter.md),
[relay_io.md](relay_io.md).

---

## 4. On-screen rendering

```mermaid
flowchart LR
    CFG["config/worker.yaml<br/>+ workers/*.yaml<br/>+ env vars"] --> BL["build_layout<br/>layouts/*.yaml + panels/*.yaml"]
    BL --> TMUX["tmux session"]

    subgraph PANES["Panels"]
        P1["avatar"]
        P2["editor / filetree"]
        P3["kafka_feed (tail_bus)"]
        P4["htop"]
        P5["thinking · radar · knowledge_graph"]
        P6["chat_list"]
        P7["replay / tile"]
    end
    TMUX --- PANES

    ST["agent_state"] --> AVD["avatar.py dispatcher"]
    EMO["emotion"] --> AVD
    GZ["gaze<br/>synced head turns"] --> AVD
    AVD --> PROV{"avatar provider"}
    PROV --> B1["builtin face"]
    PROV --> B2["ascii_avatar"]
    PROV --> B3["termgl 3D"]
    PROV --> B4["codec 3D head<br/>character generator sliders"]
    B1 --> P1
    B2 --> P1
    B3 --> P1
    B4 --> P1

    TC["tmux_control<br/>agent types into panes"] --> P2

    TMUX --> X["Xvfb + xterm"] --> FF["ffmpeg"]
    PA["PulseAudio vout"] --> FF
    FF --> TW(["Twitch / rtmp-preview"])
    FLAG[("Redis on/off")] --> SUP["stream_supervisor"] --> FF
    KILL["kill file<br/>emergency_stop · SIGUSR1"] -->|wins over Redis, checked every 0.5 s| SUP
```

Details: [layout_system.md](layout_system.md), [panels.md](panels.md),
[avatar.md](avatar.md), [avatar_providers.md](avatar_providers.md),
[character_generator.md](character_generator.md), [gaze.md](gaze.md),
[stream_supervisor.md](stream_supervisor.md).

---

## 5. Content generation

The campaign layer turns the dev-team framework into a genre-hopping show
(D&D-style campaigns with a GM and cast). Generation is offline and GPU-heavy.

```mermaid
flowchart LR
    BOOK["source work<br/>novels · campaign notes"] --> SP["utilities/source_pipeline<br/>clean chapters · ranked cast<br/>timeline · profiles"]
    SP --> PACK["campaign pack<br/>campaigns/&lt;name&gt;/<br/>campaign.yaml · cast · lore · scenes"]
    PACK --> CM["app/campaign<br/>pack · validator · scene_graph<br/>runtime · improviser · renderer"]

    CMGR["campaign-manager :8082<br/>job GUI"] --> G3
    PACK --> G3["3layer-generator :8001<br/>Arc → Segment → Dialogue<br/>(own Postgres :5455)"]
    G3 --> OUTP["generated dialogue output"]

    CM --> EB["episode builders<br/>build_campaign_episode.py<br/>build_generated_episode.py"]
    OUTP --> EB
    EB -->|POST /replays, approved| API["message-api"] --> LIB[("replay_episodes")]
    G3 -.->|"opt-in auto-submit, publish jobs<br/>POST /replays?status=draft"| API
    API -->|draft| DR[("draft<br/>never airs")]
    DR -->|operator approves<br/>POST /replays/name/approve| LIB
    LIB --> RT["Rerun Theater<br/>roundtable / GM"]

    BENCH["utilities/benchmarker<br/>LLM tok/s for campaign prompts"] -.-> G3
    QW["tools/qwen_worker<br/>local-model code harness"] -.->|built the module| CM
```

Details: [campaign_module_status.md](campaign_module_status.md),
[campaign_pack_format.md](campaign_pack_format.md),
[generator_retarget_flow.md](generator_retarget_flow.md),
[draft_submitter.md](draft_submitter.md).

Generated content never airs on its own: builder scripts and the manual
generator upload store an **approved** episode only when someone runs them,
and the generator's auto-submit (`AUTO_SUBMIT_DRAFTS`, off by default) only
creates **drafts**, which need an operator's Approve.
