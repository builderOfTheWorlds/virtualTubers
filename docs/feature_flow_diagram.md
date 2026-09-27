# virtualTubers — Feature & Component Flow

A feature-level map of the whole project: what each major component does and
how work, content, and media flow between them. It complements
[architecture_flow_diagram.md](architecture_flow_diagram.md), which zooms in on
infrastructure (who writes which database table, which service is the sole
Kafka producer). This file zooms in on **features**.

Render the Mermaid blocks with any Mermaid-capable viewer (GitHub, VS Code
preview, Obsidian, mermaid.live).

1. [System overview](#1-system-overview) — every major component on one page
2. [Dev-team task loop](#2-dev-team-task-loop) — coder → tester → manager
3. [Rerun Theater](#3-rerun-theater) — session logs → voiced shows (solo, duet, roundtable)
4. [On-screen rendering](#4-on-screen-rendering) — tmux layout, avatar, stream out
5. [Content generation](#5-content-generation) — source works → campaign packs → episodes

---

## 1. System overview

```mermaid
flowchart LR
    subgraph IN["Inputs & control"]
        Op(["Operator"])
        CP["control-panel :8091<br/>worker on/off · log filter<br/>inject messages · replay library"]
        TP["twitch-presence<br/>watches Twitch chat"]
    end

    subgraph CORE["Messaging & state"]
        MAPI["message-api :8090<br/>POST /messages · /replays"]
        K[("Kafka<br/>vtuber.messages")]
        R[("Redis<br/>on/off flags · log excludes")]
        PG[("Postgres (app)<br/>messages · replay_episodes<br/>voiced_narration · logs")]
        MLOG["message-logger"]
        LSHIP["log-shipper"]
    end

    subgraph WORKER["Each worker container (vtube-worker image)"]
        direction TB
        AG["agent.py<br/>perceive · think · act"]
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
```

**How to read it**

- **Inputs & control** — the operator drives everything through
  `message-api` (curl) or the browser `control-panel`; `twitch-presence` turns
  arriving viewers into `viewer_joined` bus events.
- **Messaging & state** — one Kafka topic carries every inter-agent message;
  `message-logger` archives it to Postgres. Redis holds only the per-worker
  on/off flag and log-filter excludes.
- **Worker container** — every character (coder variants, manager, tester,
  GM `tuber_0`, roundtable) runs the same image; `config/workers/*.yaml` picks
  its role, LLM, coding backend, avatar, voice, and tmux layout.
- **Output** — each worker's virtual screen + audio is encoded by ffmpeg and
  pushed to its own Twitch channel (optionally tee'd to the local preview).
- **Offline content generation** — separate, on-demand stack. Nothing reaches
  the live stream until an episode builder `POST`s it to `/replays`.

---

## 2. Dev-team task loop

The original show: an AI dev team solving real tasks on the seeded-bug
`sandbox/` workspace. Handlers are dispatched from `MESSAGE_HANDLERS` in
`app/agent.py`.

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant API as message-api
    participant C as Coder
    participant T as Tester
    participant M as Manager

    Op->>API: POST /messages (task_assignment)
    API->>C: task_assignment
    Note over C: LLM narrates the plan<br/>coding backend edits workspace<br/>git_client commits
    Note over C: broadcasts coding_run_report<br/>(logged to coding_backend_runs)
    C->>M: task_complete (acknowledged, no send)
    C->>T: commit_notification
    Note over T: test_runner runs pytest<br/>on a copy of the coder workspace
    alt tests pass
        T->>M: test_passed
        M->>Op: manager_report (milestone)
    else tests fail
        T->>M: bug_report
        alt retry_count < 3
            M->>C: task_assignment (fix, retry_count + 1)
        else retry cap reached
            M->>Op: manager_report (escalation)
        end
    end
    opt coder or tester is blocked
        C->>M: clarification_request
        M->>Op: manager_report (blocker)
    end
```

Every agent also publishes a heartbeat each tick and writes `agent_state` so
its avatar shows thinking / speaking / listening / error.

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
        VAL["episode_validator<br/>shape · names · leak audit · dry-run"] --> ES[("episode_store<br/>replay_episodes")]
    end

    subgraph AIR["Air an episode"]
        REQ["replay_request<br/>operator · control-panel · random on viewer_joined"] --> RP["replay_pane"]
        ES --> RP
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
        RT["Roundtable — GM hosts up to 8 tiles<br/>locally via TILE_RELAY_DIR files"]
    end
    PERF --- SOLO
    PERF --- DUET
    PERF --- RT
```

Details: [replay.md](replay.md), [replay_pane.md](replay_pane.md),
[revoice.md](revoice.md), [duet_replay.md](duet_replay.md),
[voice_gate.md](voice_gate.md), [episode_validator.md](episode_validator.md).

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
    EB -->|POST /replays| API["message-api"] --> LIB[("replay_episodes")]
    LIB --> RT["Rerun Theater<br/>roundtable / GM"]

    BENCH["utilities/benchmarker<br/>LLM tok/s for campaign prompts"] -.-> G3
    QW["tools/qwen_worker<br/>local-model code harness"] -.->|built the module| CM
```

Details: [campaign_module_status.md](campaign_module_status.md),
[campaign_pack_format.md](campaign_pack_format.md),
[generator_retarget_flow.md](generator_retarget_flow.md).
