# virtualTubers — High-Level Component & Data Flow

Two largely independent systems share this repo, joined by a handful of
**manual upload paths** — all of them human-run, all of them going through
`message-api` (there is no direct DB write from outside it).
Render the Mermaid block with any Mermaid-capable viewer (VS Code preview,
GitHub, Obsidian, mermaid.live). A rendered PNG lives alongside this file
(`architecture_flow_diagram.png`, via `scripts/render_architecture_graph.py`).

```mermaid
flowchart TB
    subgraph LIVE["LIVE STREAM SYSTEM — docker-compose.yml, always-on"]
        direction TB

        Operator["Operator (you)\ncurl / browser"]
        CP["control-panel\n:8091 — HTTP GUI\n+ /replays upload UI"]
        MAPI["message-api\n:8090 — FastAPI\nsole Kafka publisher ·\nsole INSERT into replay_episodes"]
        Kafka[("Kafka\nvtuber.messages (single topic)")]
        Redis[("Redis\nworker on/off flags + log-filter excludes\n(reads fail-open) — NOT world-state")]
        MLog["message-logger\nsole durable bus archive"]
        AppPG[("Postgres (app)\nvirtualtubers @ 192.168.1.120:5432\nmessages · replay_episodes · voiced_narration\ncontainer_logs · coding_backend_runs")]
        LogShip["log-shipper\n(reads docker.sock)"]
        TwitchPres["twitch-presence\n(watches Twitch)"]

        subgraph Workers["6 character workers + GM — each container = agent (Kafka prod+cons) + replay_pane + streaming stack"]
            Coder["worker-coder\n(+ -native/-opencode/-aider)"]
            Manager["worker-manager"]
            Tester["worker-tester"]
            GM["worker-gm (tuber_0)\nroundtable director: LLM+TTS for all speakers\nhosts all 8 tiles LOCALLY"]
        end

        Stream["streaming per worker: Xvfb + xterm + tmux + PulseAudio (vout)\n+ ffmpeg → RTMP — 6 Twitch channels, 1 per worker"]
    end

    subgraph GEN["OFFLINE CONTENT GENERATOR — separate stack, on-demand"]
        direction TB
        CampMgr["campaign-manager\n:8082 — job GUI"]
        GenAPI["3layer-generator\n:8001 — Arc → Segment → Dialogue\nLLM planner layers"]
        GenPG[("Postgres (generator)\ngeneration @ 127.0.0.1:5455\njob state only — NOT the app DB")]
        Packs["campaigns/*.yaml\n(pack input)"]
        Output["utilities/3LayersWeeklyGeneration/output\n(generated dialogue library)"]
    end

    Bridge["Manual upload paths (human-run, none automated):\nbuild_campaign_episode.py (authored pack) · build_generated_episode.py (generator run)\nbuild_roundtable_test_episode.py (test) · scripts/build_replay_library.py (session logs)\n+ control-panel /replays/upload UI"]

    Operator -->|instructions| CP
    Operator -->|curl POST /messages| MAPI
    CP -->|HTTP, incl. /replays upload| MAPI
    MAPI -->|publish (sole Kafka producer)| Kafka
    Kafka -->|consume/produce on every worker;\nduet relay: replay_invite·ready·cue·end| Workers
    Workers <-->|narration cache r/w — dual-write into voiced_narration;\nepisode READ — all workers (Rerun Theater)| AppPG
    MAPI -->|sole INSERT into replay_episodes\n(episode_store.py:55)| AppPG
    MLog -->|writes: messages · voiced_narration (text)\n· coding_backend_runs| AppPG
    MAPI -->|set on/off flags + log-filter excludes| Redis
    Workers <-->|on/off flag worker:{id}:enabled\n(fails open on read)| Redis
    MLog -.->|reads log-filter excludes before persisting| Redis
    Workers -->|tmux session capture| Stream
    LogShip -->|writes container_logs (+ retention)| AppPG
    TwitchPres -->|viewer_joined → bus broadcast;\nworkers may auto-pick a random episode (agent.py:712)| MAPI
    GM -.->|TILE_RELAY_DIR relay files: cues its OWN 8 tile seats\n(in the GM container only — not the other workers)| GM

    CampMgr -->|submit/watch job, HTTP| GenAPI
    GenAPI -->|reads| Packs
    GenAPI -->|job state, artifacts| GenPG
    GenAPI -->|writes| Output

    Output -.->|run artifacts| Bridge
    Bridge -.->|urllib/curl → POST /replays| MAPI

    style GEN fill:#2a2a3d,stroke:#888
    style LIVE fill:#1f2a1f,stroke:#888
    style Bridge fill:#4a1f1f,stroke:#f66,stroke-width:2px
```

## Reading it

**Live stream system** (what's actually running 24/7 via `docker compose up`):
- `message-api` is the single door into the bus and into the replay-library
  writes: the operator, the browser control panel (incl. its `/replays/upload`
  UI), and `twitch-presence` all go through it. It is the **sole Kafka
  publisher** and the **sole writer** of `replay_episodes` (one `INSERT` in the
  whole repo, `app/episode_store.py:55`, reached via `POST /replays`).
- Everything downstream is Kafka fan-out on ONE topic (`vtuber.messages`):
  every worker container's `agent.py` is both producer and consumer
  (`agent.py:1127-1138`). Cross-worker duet shows ride the same bus via the
  `replay_invite`/`replay_ready`/`replay_cue`/`replay_end` relay handlers
  (`agent.py:955-1049`).
- `message-logger` is the sole durable bus archive → **app** Postgres
  (`messages`, `voiced_narration` text rows, `coding_backend_runs`). Note the
  dual-write on `voiced_narration`: workers upsert the full row (text + WAV)
  via `app/narration_store.py` without touching the bus; both writers converge
  on `ON CONFLICT (message_id, scene_index)`.
- **Episode reads are a universal worker capability** — `replay_pane` and
  `agent.py` in every container read `replay_episodes` (viewer-join can
  trigger a random rerun, `agent.py:712-715`). Only the *write* path is
  centralized.
- Redis is small shared state with exactly TWO jobs: the per-worker on/off
  flag (`worker_control.py`, fail-open on read) and per-message-type log
  excludes (`log_filter_control.py`, checked by message-logger). **World-state
  is NOT in Redis** — it's a local file per worker (`world_state.backend: file`,
  `/data/world-state`, e.g. `config/workers/tuber_0.yaml:197-199`).
- Output is each worker's full streaming stack — Xvfb + xterm + tmux layout +
  PulseAudio `vout` sink + ffmpeg — captured to its own Twitch channel (or
  local `rtmp-preview` for testing). This pipeline has no dependency on Kafka;
  its only control surface is the Redis on/off flag.
- The GM worker (`tuber_0`) is special, but in a LOCAL sense: it runs the LLM
  narration + TTS for *every* speaker and hosts **all 8 roundtable seats
  in its own container**, cueing them with `TILE_RELAY_DIR` relay files
  (default `/tmp/tiles`). That is intra-container plumbing — the other six
  worker containers run their own single-tile solo shows and are only
  reachable as show partners over the Kafka duet-protocol, never via relay files.

**Offline content generator** (separate stack, run on demand, GPU-hours job):
- `campaign-manager` is just a job-submission GUI over `3layer-generator`'s
  HTTP API (it reads the generator's docker logs for the detail page — the
  only cross-stack touch, and it's log-only).
- `3layer-generator` reads a campaign pack (`campaigns/*.yaml`), runs the
  Arc → Segment → Dialogue LLM planner layers, and writes generated output —
  using its **own separate Postgres** (`generation` @ 127.0.0.1:5455, NOT the
  app DB). No Kafka, no app-DB reference anywhere in the generator tree.
- **Nothing here is automatically wired into the live stream.** Generator
  output just sits in `utilities/3LayersWeeklyGeneration/output/`.

**The bridges** (red — where generated/external content enters the live
system): every route is manual and every route goes `urllib`/`curl` →
`POST /replays` on message-api → the single `INSERT` in `episode_store.py`:
- `.claude/prompts/build_campaign_episode.py` — from an authored campaign pack
- `.claude/prompts/build_generated_episode.py` — from a 3layer-generator run
- `.claude/prompts/build_roundtable_test_episode.py` — test episodes
- `scripts/build_replay_library.py` — batch-parses dev-box session logs
- the control-panel `/replays/upload` web UI

Until one of these runs, off-line content and live-airable content are two
disconnected worlds.

## Two Postgres instances — don't confuse them

| | App DB | Generator DB |
|---|---|---|
| Name | `virtualtubers` | `generation` |
| Host | `192.168.1.120:5432` (mafober) | `127.0.0.1:5455` (bundled, hardcoded) |
| Holds | messages, replay_episodes, voiced_narration, container_logs, coding_backend_runs | generator job state + artifacts only |
| Used by | live workers, message-logger, log-shipper, message-api | 3layer-generator only |

## Corrections applied in the 2026-09 code audit

This diagram previously (built from docs, not code) said:
1. ~~GM cues the other six workers via `TILE_RELAY_DIR`~~ → corrected to an
   in-GM-container tile relay; cross-worker duet coordination is the Kafka
   `replay_invite/ready/cue/end` protocol (`app/agent.py:955-1049`;
   `app/replay_pane.py:489-551`; `config/workers/tuber_0.yaml:163-190`).
2. ~~only the GM reads episode data~~ → every worker reads
   `replay_episodes` (`app/replay_pane.py:222-267`, `app/agent.py:712-715`);
   the single *write* stays in `app/episode_store.py:55` via `message-api`
   (`services/message-api/api.py:193,235`).
3. ~~Redis world-state~~ → world-state is file-backed
   (`config/workers/tuber_0.yaml:197-199`); Redis's real second job is the
   log-filter excludes (`app/log_filter_control.py`).
4. ~~one manual bridge script inserting directly~~ → five manual routes, all
   via `POST /replays` (see "The bridges" above), none of them touching
   Postgres directly.
