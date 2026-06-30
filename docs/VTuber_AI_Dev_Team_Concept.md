# VTuber AI Dev Team — Concept & Architecture Planning Document

*Living Document — v0.3 Draft*

---

## 1. Project Overview

An autonomous AI-powered VTuber streaming system where a team of AI agents act as a live software development team. Each agent has a distinct personality, voice, avatar, and role. They collaborate on real coding tasks in a shared environment, stream their work to Twitch/YouTube, and interact with live audience chat. The entire system is modular and config-driven so new behaviors, features, overlays, or agents can be added without rebuilding the stack.

### 1.1 Core Concept

- 3 AI agents (Manager, Coder, Tester) collaborate autonomously on software projects
- Each agent streams simultaneously — own channel, avatar, voice, and personality
- Agents communicate via a shared message bus and world state
- Audience chat can influence agent behavior in real time
- All components (agents, overlays, behaviors, voices) are hot-configurable via YAML/JSON

### 1.2 Vision Statement

Viewers tune in and watch a dev team — one they can cheer for, troll, or help — ship real software live. The Manager stresses about deadlines, the Coder argues about best practices, the Tester smugly finds bugs. It's part tech demo, part entertainment, part social experiment.

---

## 2. Agent Definitions

Each agent is a self-contained unit with its own LLM context, persona config, voice profile, avatar, and action loop. Agents share a world state but maintain independent memory and decision logic.

### 2.1 The Manager Agent

**Role & Responsibilities**
- Receives project goals from config or audience chat
- Breaks goals into tickets and assigns them to Coder
- Monitors Tester's bug reports and re-prioritizes the queue
- Makes architectural decisions and communicates blockers
- Narrates project status to the stream audience

**Personality Profile**
- Organized but visibly stressed under pressure
- Uses PM/corporate-speak that occasionally cracks
- Has strong opinions about scope creep
- Talks to the audience about the team's progress

**Stream View**
- Displays a live project board (ticket queue, statuses)
- Shows the inter-agent message bus as a chat panel
- Animated avatar reacts to events (ticket created, bug filed, etc.)

### 2.2 The Coder Agent

**Role & Responsibilities**
- Receives task assignments from Manager
- Writes real, executable code in a visible terminal/editor
- Commits to the shared git repo and notifies Manager on completion
- Requests clarification via message bus when blocked
- Narrates code decisions and trade-offs aloud

**Personality Profile**
- Focused and flow-state prone — gets annoyed by interruptions
- Tangent-prone (will explain a concept no one asked about)
- Opinionated about code style, naming, and architecture
- Occasionally expresses frustration or pride in solutions

**Stream View**
- Full terminal/editor view of active coding session
- Syntax-highlighted editor, visible keystrokes (simulated typing)
- Commit log visible as sidebar

### 2.3 The Tester Agent

**Role & Responsibilities**
- Watches for new commits from Coder
- Runs test suites, linters, and manual exploratory tests
- Files structured bug reports to the Manager
- Attempts to break things creatively, not just run happy-path tests
- Marks tickets as passing or failing with evidence

**Personality Profile**
- Methodical, skeptical, slightly smug when finding bugs
- Sympathetic toward edge cases and user error scenarios
- Has a dry sense of humor about code quality
- Celebrates test coverage milestones

**Stream View**
- Test runner output scrolling in real time
- Bug report panel with severity indicators
- Coverage graph overlay

---

## 3. System Architecture

### 3.1 High-Level Layers

| Layer | Description |
|---|---|
| Config Layer | YAML/JSON files defining agents, behaviors, overlays, voices, stream layout |
| World State | Shared JSON/Redis store — tickets, repo state, messages, test results |
| Agent Runtime | Per-agent process: perceive → think → act → speak → update loop |
| Message Bus | Async queue for inter-agent communication (file-based or Redis pub/sub) |
| Execution Sandbox | Docker containers per agent for safe code execution |
| Rendering Layer | OBS scenes, virtual camera, avatar compositor |
| Stream Output | RTMP to Twitch/YouTube per agent, or single split-layout stream |
| Audience Bridge | Chat listener that injects viewer messages into agent context |

### 3.2 Agent Loop

Each agent runs an independent async loop:

1. **perceive()** — Read shared world state, message bus, and chat queue
2. **think()** — LLM call with role system prompt + current context
3. **act()** — Execute action: write file, run test, post message, update ticket
4. **speak()** — Generate TTS narration of current action
5. **update_state()** — Write results back to world state
6. **idle()** — Filler animation/behavior while waiting for LLM or dependencies

### 3.3 Shared World State Schema

| Key | Contents |
|---|---|
| tickets | Task queue with status, assignee, priority, description |
| repo/ | The actual codebase being built (mounted ZFS dataset) |
| messages/ | Inter-agent communication log (from, to, type, payload, timestamp) |
| test_results | Latest test run output, pass/fail per test, coverage % |
| stream_events | Viewer chat events, donations, channel point redemptions |
| agent_state/ | Per-agent memory, current task, mood, last action |

### 3.4 Inter-Agent Communication

Agents communicate via a typed message bus. All messages are logged and displayed on stream:

| Message Type | From | To |
|---|---|---|
| task_assignment | Manager | Coder |
| task_complete | Coder | Manager |
| clarification_request | Coder | Manager |
| commit_notification | Coder | Tester |
| bug_report | Tester | Manager |
| test_passed | Tester | Manager |
| retest_request | Manager | Tester |
| status_update | Any | Broadcast |

---

## 4. Configuration System

Everything configurable lives in a top-level `config/` directory. No hardcoded behavior — add a new agent, overlay, or behavior by dropping a file and reloading.

### 4.1 Config Directory Structure

```
config/agents/      — One YAML per agent (personality, model, voice, avatar)
config/behaviors/   — Pluggable behavior modules (e.g., chat_interaction.yaml)
config/overlays/    — OBS overlay definitions (layout, elements, triggers)
config/stream/      — Stream output settings (RTMP, resolution, layout mode)
config/world/       — World state schema, tick rate, shared repo path
config/voices/      — TTS provider settings per agent
config/chat/        — Audience interaction rules, allowed commands, cooldowns
```

### 4.2 Agent Config Schema (`agents/coder.yaml`)

| Field | Description |
|---|---|
| id | Unique agent identifier (e.g., coder) |
| display_name | Name shown on stream (e.g., KODI-7) |
| role | System role: manager \| coder \| tester \| custom |
| model | LLM to use: claude-sonnet-4-6 \| ollama/mistral \| etc. |
| system_prompt | Path to .txt file defining personality and instructions |
| voice.provider | TTS provider: elevenlabs \| kokoro \| coqui |
| voice.voice_id | Provider-specific voice ID |
| avatar.model | Path to Live2D model or 3D asset |
| avatar.expressions | Map of emotion → expression trigger |
| stream.scene | OBS scene name for this agent |
| behaviors | List of behavior module IDs to enable |
| memory.max_tokens | Max context window for agent memory |
| tick_rate_ms | How often the agent loop runs (default: 5000) |

### 4.3 Behavior Module Config (`behaviors/chat_interaction.yaml`)

Behaviors are pluggable modules that extend agent capabilities. Enable or disable per-agent without touching core code.

| Field | Description |
|---|---|
| id | Unique behavior ID |
| name | Human-readable name |
| description | What this behavior does |
| trigger | Event that activates: chat_message \| ticket_created \| commit \| scheduled |
| cooldown_ms | Minimum time between activations |
| agents | Which agents this applies to (or all) |
| params | Behavior-specific parameters (e.g., response_chance: 0.3) |
| enabled | Boolean — hot-reload safe, change and apply without restart |

### 4.4 Overlay Config (`overlays/ticker.yaml`)

| Field | Description |
|---|---|
| id | Overlay identifier |
| type | ticker \| panel \| popup \| graph \| chat_feed \| alert |
| source | Data source: world_state.tickets \| agent_state.coder \| chat_feed |
| position | OBS scene position: x, y, width, height |
| refresh_ms | How often the overlay polls for new data |
| style | CSS-like styling: background, font, color, opacity |
| trigger | Optional — only show on event: bug_filed \| commit \| chat_command |
| duration_ms | For popup type — how long to display before hiding |
| enabled | Hot-reload toggle |

---

## 5. Stream Layout & Output

### 5.1 Layout Modes

| Mode | Description |
|---|---|
| Multi-stream | 3 separate Twitch/YouTube channels, one per agent. Director stream shows split view. |
| Split-layout | Single stream. Screen divided: Manager top-left, Coder top-right, Tester bottom, message bus ticker at base. |
| Focus mode | Single stream switches active agent view based on current action (most dynamic event wins focus) |
| Hybrid | Primary split layout with picture-in-picture for other agents |

### 5.2 Overlay System

Overlays are independently configurable UI elements rendered over the stream. Each overlay is defined in `config/overlays/` and can be toggled without restarting:

- Message Bus Ticker — scrolling inter-agent messages at bottom of screen
- Ticket Board Panel — live kanban: To Do / In Progress / Testing / Done
- Commit Log — recent git commits with author (agent) and message
- Test Results Bar — pass/fail counts, coverage %, last run time
- Mood Indicators — per-agent emoji/icon showing current emotional state
- Chat Overlay — viewer messages that agents have acknowledged
- Alert Popups — triggered on events: bug found, PR merged, viewer redeemed
- Agent Status — current task + status for each agent (idle / working / waiting)

### 5.3 Adding a New Overlay

To introduce a new overlay (e.g., a leaderboard of bugs found per agent):

1. Create `config/overlays/bug_leaderboard.yaml` with type, source, position, style
2. If the overlay needs a new data source, add the field to the world_state schema
3. The overlay renderer picks up the new config on next hot-reload (no restart)
4. OBS scene is updated via obs-websocket API automatically

---

## 6. Audience Chat Interaction

### 6.1 Chat Integration

A chat bridge process listens to Twitch/YouTube chat and injects events into the world state `stream_events` queue. Agents consume this queue during their `perceive()` step.

### 6.2 Interaction Modes

| Mode | Description |
|---|---|
| Passive | Agents occasionally acknowledge chat without direct responses. Manager might say "chat seems excited about this feature" |
| Active | Agent directly reads and responds to specific messages on a cooldown |
| Command-driven | Viewer chat commands trigger specific behaviors (e.g., `!bug` makes Tester run extra tests) |
| Voting | Chat votes influence Manager's prioritization (e.g., poll: fix bug vs add feature) |
| Channel Points | Custom redemptions: "Give Coder a coffee" (speeds up tick rate), "Distract Manager" (inserts a fake urgent ticket) |

### 6.3 Chat Config (`config/chat/rules.yaml`)

- `allowed_commands` — List of !commands agents will respond to
- `response_cooldown_ms` — Minimum time between agent chat responses
- `agent_response_chance` — Probability an agent responds to a non-command message
- `blocked_patterns` — Regex list of inputs to ignore
- `voting_enabled` — Allow chat polls to affect ticket priority
- `channel_points` — Map redemption names to behavior triggers

---

## 7. Extensibility — Adding New Features

### 7.1 Adding a New Behavior to an Agent

Example: Give the Coder agent a new behavior where it explains code out loud to the audience when starting a complex task.

1. Create `config/behaviors/explain_on_start.yaml`
   - Set `trigger: task_assigned`, `agents: [coder]`
   - Set `params: complexity_threshold: high, explanation_max_tokens: 200`
2. Create `prompts/behaviors/explain_on_start.txt` — the prompt injected into agent context
3. Hot-reload config — behavior activates on next agent loop without restart
4. No core code changes required

### 7.2 Adding a New Agent

Example: Adding a DevOps agent that manages deployments.

1. Create `config/agents/devops.yaml` with `role: custom`, own voice, avatar, behaviors
2. Write `prompts/agents/devops_system.txt` defining persona and responsibilities
3. Define message types this agent sends/receives in the message bus schema
4. Add OBS scene for the new agent's stream view
5. Register agent in `config/world/agents_active.yaml`
6. Agent runtime auto-discovers and spins up the new agent loop

### 7.3 Adding a New Overlay

Example: A live dependency graph showing which parts of the codebase each agent is touching.

1. Create `config/overlays/dependency_graph.yaml`
2. Specify `source: world_state.repo.active_files`
3. Specify `type: graph`, `renderer: d3_force` (built-in renderers or custom HTML/CSS)
4. Set position and `enabled: true`
5. Overlay renderer picks it up on hot-reload

### 7.4 Changing a Voice or Persona

All voice and persona config lives in agent YAML — no code changes:

1. Update `voice.voice_id` in `config/agents/coder.yaml` to a new ElevenLabs voice
2. Update `system_prompt` path to a new personality prompt file
3. Update `avatar.model` to swap Live2D or 3D model
4. Hot-reload applies changes on the next agent loop tick

---

## 8. Infrastructure & Homelab Mapping

| Resource | Usage |
|---|---|
| RTX 3080 #1 | Ollama LLM inference (serves all 3 agents via API) |
| RTX 3080 #2 | TTS synthesis (Kokoro/StyleTTS2) + OBS GPU encode |
| Proxmox | LXC containers: one per agent runtime, isolated execution sandboxes |
| ZFS Dataset | Shared repo mount, world state files, message bus logs, stream recordings |
| Docker (per LXC) | Code execution sandbox per agent (prevents runaway processes) |
| OBS + obs-websocket | Scene management, overlay rendering, RTMP stream output |
| Redis (optional) | Graduate from file-based world state to Redis for lower latency |

### 8.1 Process Map

- `agent_runner.py` — Main orchestrator, spawns one agent process per config entry
- `state_server.py` — Manages shared world state reads/writes with file locking
- `message_bus.py` — Routes inter-agent messages, logs to stream_events
- `chat_bridge.py` — Twitch/YouTube IRC listener, injects to stream_events
- `overlay_server.py` — Watches `config/overlays/`, serves HTML overlays to OBS browser sources
- `obs_controller.py` — obs-websocket client, updates scenes and sources dynamically
- `tts_server.py` — Queues TTS requests, streams audio output to virtual audio device

---

## 9. Agent Environment Evolution Path

Rather than committing to one environment design, agent environments are built in stages — starting CLI-first for reliability and speed, graduating to full GUI desktop control once the core multi-agent system (loops, message bus, streaming pipeline) is proven. This de-risks development by not debugging vision-based GUI control and the entire agent architecture simultaneously.

### 9.1 Why Sequence It This Way

- CLI-driven control is instant and deterministic — no coordinate drift, no vision latency
- The hard problems (agent loops, message bus, state sync, streaming) get solved on a stable foundation
- GUI desktop control is added later as an upgrade to specific agents, not a rewrite
- If GUI control proves too unreliable or costly, the system still works fully on CLI-only agents

### 9.2 Stage 1 — CLI-First Environment (MVP)

**Setup**
- LXC container per agent, no virtual display needed at all
- Coder: tmux session running neovim/vim, driven by direct file writes + CLI commands
- Tester: pytest/test runner CLI, output captured directly via stdout
- Terminal sessions rendered to web via ttyd or xterm.js, captured by OBS as a browser source

**Control Method**
- Agent writes files directly (no typing simulation needed for actual code changes)
- Optional: simulate human-speed typing into the tmux pane via direct key injection for visual effect
- Git, test runners, linters — all invoked as subprocess calls with captured stdout/stderr

**Why This Works for MVP**
- Zero coordinate-based clicking, zero vision LLM calls needed for core function
- Lowest resource footprint — proves the multi-agent architecture cheaply
- Still visually compelling — live terminal output, syntax highlighting, scrolling logs

### 9.3 Stage 2 — Constrained GUI Desktop

**Setup**
- Same LXC containers, add Xvfb (virtual framebuffer) + i3 or openbox (lightweight window manager)
- Fixed resolution and fixed window layout defined in i3 config (e.g., terminal top-left, editor right half)
- Curated, limited app set: terminal, code editor (VSCode now viable without VM overhead), file manager, browser

**Control Method**
- xdotool for click/type/key actions targeting known window positions
- AT-SPI (pyatspi) queried first for exact element positions/labels — reduces guesswork
- Vision LLM (screenshot-based) used only as fallback when AT-SPI can't resolve an element
- wmctrl for window focus/arrangement to keep layout predictable

**Why Constrain It**
- Fixed layout and limited apps minimize the "did the click land correctly" failure mode
- AT-SPI-first approach reduces reliance on slow, expensive vision calls
- Predictable environment makes stream output consistent and debuggable

### 9.4 Stage 3 — Expanded Desktop Autonomy

**Setup**
- Relax constraints — allow agents to open additional apps as needed for broader tasks
- Multi-window workflows: switching between docs, browser research, and editor fluidly
- Potential move from Xvfb to a lightweight full desktop environment if visual polish demands it

**Control Method**
- Full computer-use loop: screenshot → vision LLM decision → xdotool action → verify → repeat
- AT-SPI remains the preferred path; vision is the general-purpose fallback for novel UI
- Recovery logic for misclicks: re-screenshot, re-assess, retry with bounded attempts before escalating to Manager

**When to Move Here**
- Only after Stage 1 and Stage 2 are stable and the agent loop, message bus, and streaming pipeline are proven
- Driven by a real need (e.g., Tester needs to interact with a real browser UI beyond what Playwright scripting covers)

### 9.5 Environment Comparison Table

| Aspect | Stage 1 (CLI) | Stage 2/3 (GUI) |
|---|---|---|
| Resource use | ~150-300MB/agent | ~500MB-1.5GB/agent |
| Control latency | Instant (direct calls) | 1-3s per vision decision |
| Reliability | Deterministic | Possible coordinate drift |
| Visual variety | Terminal only | Multiple real app windows |
| Vision LLM cost | None required | Per-screenshot API cost |
| Best fit | Coder/Tester core logic | Browser testing, broader tasks |

### 9.6 Per-Agent Recommendation Under This Path

| Agent | Stage 1 → Stage 2/3 Path |
|---|---|
| Manager | Stays web app indefinitely — no GUI desktop ever needed |
| Coder | Stage 1 (tmux+neovim) → optionally Stage 2 (real VSCode in constrained desktop) for visual upgrade |
| Tester | Stage 1 (pytest CLI + Playwright headless) → Stage 2/3 when real visible browser UI testing is needed |

---

## 10. Development Roadmap

### Phase 1 — Foundation (MVP)
- Single agent (Coder) with LLM brain, TTS voice, basic VTube Studio avatar
- File-based world state and message log
- OBS stream output with one overlay (commit log)
- Manual task injection via CLI
- Validate end-to-end: task → code → commit → stream

### Phase 2 — Multi-Agent
- Add Manager and Tester agents
- Implement message bus routing
- 3-agent split stream layout
- Ticket board overlay

### Phase 3 — Audience & Overlays
- Chat bridge integration (Twitch IRC)
- Passive and command-driven chat interaction
- Overlay config hot-reload system
- Full overlay suite: ticker, alerts, mood indicators

### Phase 4 — Polish & Config System
- Full YAML config system with hot-reload
- Behavior plugin system
- Avatar animation triggers (expressions, idle animations)
- Agent personality tuning via prompt engineering
- Multi-stream output (separate channels per agent)

### Phase 5 — Advanced
- Channel Points integration
- Voting system for ticket priority
- Additional agents (DevOps, Designer, etc.)
- Persistent agent memory across sessions
- Web dashboard for config management

---

## 11. Open Questions & Design Decisions

| Question | Options / Notes |
|---|---|
| LLM provider | Claude API (quality, cost) vs local Ollama (latency, free). Hybrid possible — local for filler, API for key decisions |
| Stream platform | Twitch (chat API mature) vs YouTube (wider reach). Start with Twitch. |
| Avatar tech | VTube Studio + Live2D (easiest) vs Unreal MetaHuman (high quality, complex) |
| TTS provider | ElevenLabs (best quality) vs Kokoro/StyleTTS2 (local, free). Start with ElevenLabs. |
| World state store | JSON files (simple, debuggable) vs Redis (fast, pub/sub). Start with files. |
| Code execution safety | Docker sandbox per agent — resource limits, network isolation, timeout enforced |
| Dead air handling | Idle animations + pre-generated filler narration bank while LLM responds |
| Human override | Watchdog process + manual CLI commands to pause, redirect, or reset any agent |
| Personality drift | Periodic system prompt reinforcement injected into context every N turns |
| Environment staging trigger | Define concrete criteria for when to graduate an agent from Stage 1 (CLI) to Stage 2 (GUI) — e.g., a specific task type fails repeatedly with CLI-only approach |

---

## 12. Scaling Architecture (Future Reference)

For reference when the system grows beyond the MVP/Phase 1-2 scope:

### 12.1 Service-Oriented Design
Each concern becomes an independent service communicating over a network rather than function calls — agents, LLM routing, TTS, state, and message brokering all decouple so each can scale independently.

### 12.2 Key Services
- **Message Broker** — RabbitMQ or Kafka replacing file-based message bus
- **State Service** — Redis as single source of truth, with pub/sub and replayable event logs (Redis Streams)
- **LLM Service** — Dedicated inference gateway handling rate limiting, retries, and routing between local Ollama and Claude API
- **TTS Service** — Queue-based, multiple workers in parallel so simultaneous agent speech doesn't bottleneck
- **Stream Service** — One controller per agent output, managed by a central orchestrator

### 12.3 Container Path
Docker Compose for local development → Kubernetes/k3s when scaling beyond one Proxmox host. Services are written with clean interfaces from day one so this migration doesn't require code rewrites.

### 12.4 Config as Control Plane
At scale, config moves from manually-edited files to a Config Service with a REST API, change broadcasting via the message broker, and Git-backed history for rollback.


---

## 13. Deployment Architecture

Each worker is a **self-contained Docker image of a Linux environment** — it contains everything it needs to do its job, render its avatar, and stream. Kubernetes deploys N of these worker containers with different configs. The only meaningful differences between workers are their AI prompts, persona, voice, and avatar. Infrastructure (LLM endpoint, stream keys, world state URL) varies by environment, not by worker.

---

### 13.1 The Worker Container Model

One base Docker image. Config drives everything else.

The worker's entire environment is a **tmux session** split into panes. A terminal emulator (xterm) renders this session on a virtual display (Xvfb), and a broadcaster process (ffmpeg) captures that display and pushes it to a streaming service. Everything — the work, the avatar, the inter-agent chat — is visible in the terminal.

```
┌─────────────────────────────────────────────────────┐
│                  Worker Container                   │
│                                                     │
│  Xvfb :99 + xterm                                   │
│  └── tmux session (the full stream view)            │
│                                                     │
│  Agent loop (Python)                                │
│  ├── perceive() → think() → act() → speak()         │
│  ├── writes to editor pane via tmux send-keys       │
│  ├── updates avatar pane state                      │
│  └── LLM + TTS clients                             │
│                                                     │
│  broadcaster (ffmpeg)                               │
│  └── captures Xvfb → encodes → RTMP out             │
└─────────────────────────────────────────────────────┘
```

---

### 13.2 Tmux Layout

Each worker runs one tmux session with a fixed pane layout. The layout is defined in the agent's config and set up by a startup script when the container initializes.

```
┌──────────────┬───────────────────────────────────────┐
│              │                                       │
│  File List   │           Editor (nvim)               │
│  (tree/lsd)  │                                       │
│              │                                       │
├──────────────┤                                       │
│              ├───────────────────────────────────────┤
│   Avatar     │                                       │
│  (ASCII art) │         Agent Chat                    │
│              │   (inter-agent message log)           │
├──────────────┴───────────────────────────────────────┤
│                      htop                            │
└──────────────────────────────────────────────────────┘
```

| Pane | Contents | Updated by |
|---|---|---|
| File List | `watch -n2 tree /workspace` or `lsd --tree` | Refreshes automatically as coder writes files |
| Editor | `nvim` (coder) or a read-only diff view (tester) | Agent loop sends keystrokes via `tmux send-keys` |
| Avatar | ASCII art face + speech bubble | Avatar process (see 13.3) |
| Agent Chat | Tail of inter-agent message log | `tail -f /data/messages/bus.log` |
| htop | `htop` | Always running |

The layout is the same base structure for all workers — what differs is which pane gets the most real estate and what runs in the editor pane (e.g., tester shows pytest output instead of nvim).

---

### 13.3 ASCII Avatar System

The avatar pane runs a persistent Python process (`avatar.py`) that owns the pane and redraws it as state changes. The face has a small set of expressions and can display speech bubbles.

**Example render:**

```
  ╭───────────╮
  │  ◉     ◉  │
  │     ▾     │   ╭─────────────────────────╮
  │  ╰─────╯  │   │ "This function needs    │
  ╰───────────╯   │  more error handling..." │
  [  KODI-7  ]   ╰─────────────────────────╯
  [ thinking ]
```

**Expression states:**

| State | Eyes | Mouth | Trigger |
|---|---|---|---|
| idle | `◉  ◉` | `╰───╯` | Waiting for LLM response |
| thinking | `⊙  ⊙` | `─────` | LLM call in flight |
| typing | `◉  ◉` | `╰───╯` + cursor blink | Agent is writing to editor |
| speaking | `◕  ◕` | `╰▾──╯` | TTS playing |
| frustrated | `◕  ◕` | `╭───╮` | Bug found / test failed |
| happy | `◉  ◉` | `╰▾▾▾╯` | Test passed / commit merged |
| focused | `◔  ◔` | `─────` | Deep in a task |

**Speech bubbles** appear when:
- The agent narrates an action aloud (TTS is triggered)
- A message is received from another agent
- The agent responds to chat

The bubble text is word-wrapped to fit the pane width and auto-dismisses after N seconds or when the next message arrives.

`avatar.py` reads agent state from a small local state file that the agent loop writes to — no inter-process socket needed.

---

### 13.4 What Lives Inside the Image

| Component | Implementation |
|---|---|
| Base OS | Ubuntu 22.04 |
| Virtual display | Xvfb on `:99` |
| Terminal emulator | xterm (renders tmux session, captured by ffmpeg) |
| Terminal multiplexer | tmux — owns the full layout |
| Editor | neovim |
| File viewer | `lsd` or `tree` in a watch loop |
| System monitor | htop |
| Avatar process | `avatar.py` — Python curses/ANSI process in its own pane |
| Agent loop | Python — perceive → think → act → speak |
| TTS | ElevenLabs HTTP client or Kokoro — audio via PulseAudio → ffmpeg audio input |
| LLM client | HTTP client → Ollama or Claude API |
| World state client | Reads/writes shared volume or Redis |
| Broadcaster | ffmpeg: `Xvfb:99` + audio → x264/aac → RTMP |

---

### 13.3 What Differs Between Workers

Workers are instances of the same image. The only differences are injected via a mounted ConfigMap:

| Config field | Manager | Coder | Tester |
|---|---|---|---|
| `AGENT_ROLE` | manager | coder | tester |
| `AGENT_DISPLAY_NAME` | e.g. MAX-1 | e.g. KODI-7 | e.g. TESS-3 |
| `SYSTEM_PROMPT_PATH` | `/config/prompts/manager.txt` | `/config/prompts/coder.txt` | `/config/prompts/tester.txt` |
| `AVATAR_MODEL` | `/config/avatars/manager/` | `/config/avatars/coder/` | `/config/avatars/tester/` |
| `VOICE_ID` | voice-abc | voice-def | voice-ghi |
| `STREAM_KEY` | manager stream key | coder stream key | tester stream key |
| `TERMINAL_LAYOUT` | project board | editor + terminal | test runner output |

Everything else (LLM endpoint, TTS provider, world state URL, RTMP base URL) is shared across all workers and set at the environment level.

---

### 13.4 Shared Services

Two lightweight shared services that all worker pods connect to:

| Service | Purpose | Dev | Prod |
|---|---|---|---|
| `world-state` | Tickets, messages, test results, agent state | JSON files on a shared volume | Redis |
| `chat-bridge` | Twitch/YouTube IRC listener → injects to world state | Single container, mock mode in dev | Single container, real IRC |

These are not the main event — they're small supporting services. The workers are the main event.

---

### 13.5 Environment Overview

| Aspect | Dev | Staging | Production | Cloud |
|---|---|---|---|---|
| Orchestrator | Docker Compose | k3s (Proxmox) | k3s (Proxmox) | EKS / GKE |
| LLM endpoint | `host.docker.internal:11434` (Ollama) | Local Ollama node | Claude API | Claude API |
| TTS provider | Kokoro (local) or null driver | Kokoro (local) | ElevenLabs | ElevenLabs |
| RTMP target | nginx-rtmp (local preview) | Private test channel | Live Twitch/YouTube | Live channels |
| World state | File-based shared volume | File-based shared volume | Redis | Redis |
| Worker replicas | 3 (one per agent role) | 3 | 3 | 3+ |
| GPU (LLM) | Host Ollama outside Docker | Node affinity → RTX 3080 #1 | Node affinity → RTX 3080 #1 | GPU node pool |

---

### 13.6 Docker Compose — Dev

In dev, Docker Compose runs the three worker containers plus the two shared services. Workers connect to Ollama running on the host machine.

```yaml
# docker-compose.yml (simplified)
services:
  worker-manager:
    image: vtube-worker:latest
    env_file: .env.dev
    environment:
      AGENT_ROLE: manager
      SYSTEM_PROMPT_PATH: /config/prompts/manager.txt
      AVATAR_MODEL: /config/avatars/manager
      STREAM_KEY: ${MANAGER_STREAM_KEY}
    volumes:
      - ./config:/config:ro
      - world-state:/data/world-state

  worker-coder:
    image: vtube-worker:latest
    env_file: .env.dev
    environment:
      AGENT_ROLE: coder
      SYSTEM_PROMPT_PATH: /config/prompts/coder.txt
      AVATAR_MODEL: /config/avatars/coder
      STREAM_KEY: ${CODER_STREAM_KEY}
    volumes:
      - ./config:/config:ro
      - world-state:/data/world-state
      - repo:/data/repo               # coder writes code here

  worker-tester:
    image: vtube-worker:latest
    env_file: .env.dev
    environment:
      AGENT_ROLE: tester
      SYSTEM_PROMPT_PATH: /config/prompts/tester.txt
      AVATAR_MODEL: /config/avatars/tester
      STREAM_KEY: ${TESTER_STREAM_KEY}
    volumes:
      - ./config:/config:ro
      - world-state:/data/world-state
      - repo:/data/repo:ro            # tester reads code, runs tests

  chat-bridge:
    image: vtube-chat-bridge:latest
    env_file: .env.dev
    volumes:
      - world-state:/data/world-state

  rtmp-preview:
    image: tiangolo/nginx-rtmp         # local RTMP ingest for dev preview
    ports: ["1935:1935", "8080:80"]

volumes:
  world-state:
  repo:
```

`.env.dev` holds shared env vars:
```
LLM_PROVIDER=ollama
LLM_BASE_URL=http://host.docker.internal:11434
TTS_DRIVER=kokoro
STREAM_RTMP_BASE=rtmp://localhost:1935/live
WORLD_STATE_BACKEND=file
WORLD_STATE_PATH=/data/world-state
```

**Running dev:**
```bash
docker compose up
```

---

### 13.7 Kubernetes / Helm — Staging, Production, Cloud

One Helm chart. One template for the worker Deployment (parameterized by role). Environment differences live entirely in values files.

#### 13.7.1 Helm Chart Structure

```
deploy/helm/vtube/
  Chart.yaml
  values.yaml                    # base defaults
  values.staging.yaml
  values.prod.yaml
  values.cloud.yaml
  templates/
    worker-deployment.yaml       # single template, rendered once per worker role
    world-state-deployment.yaml
    chat-bridge-deployment.yaml
    redis-deployment.yaml        # only enabled in prod/cloud via values
    configmap-prompts.yaml       # system prompts per worker
    configmap-avatars.yaml       # avatar config per worker
    secrets.yaml                 # references to K8s Secrets
    pvcs.yaml
    services.yaml
```

The worker template is rendered three times — once per role — using a Helm `range` loop over the workers defined in values:

```yaml
# values.yaml — workers block
workers:
  - role: manager
    displayName: MAX-1
    promptFile: manager.txt
    avatarModel: manager
    voiceId: voice-abc
  - role: coder
    displayName: KODI-7
    promptFile: coder.txt
    avatarModel: coder
    voiceId: voice-def
  - role: tester
    displayName: TESS-3
    promptFile: tester.txt
    avatarModel: tester
    voiceId: voice-ghi
```

Add a new worker role by adding an entry to this list — no new template files needed.

#### 13.7.2 Deploying to an Environment

```bash
# Staging
helm upgrade --install vtube ./deploy/helm/vtube \
  -f values.yaml -f values.staging.yaml \
  --namespace vtube-staging --create-namespace

# Production
helm upgrade --install vtube ./deploy/helm/vtube \
  -f values.yaml -f values.prod.yaml \
  --namespace vtube-prod --create-namespace
```

Secrets (API keys, stream keys, tokens) are created once per namespace outside Helm and referenced in templates:
```bash
kubectl create secret generic vtube-secrets \
  --from-literal=elevenlabs_api_key=sk-... \
  --from-literal=claude_api_key=sk-ant-... \
  --from-literal=manager_stream_key=... \
  --from-literal=coder_stream_key=... \
  --from-literal=tester_stream_key=... \
  --from-literal=twitch_token=... \
  -n vtube-prod
```

#### 13.7.3 Per-Environment Values (what changes)

```yaml
# values.prod.yaml
llm:
  provider: claude
  baseUrl: https://api.anthropic.com
  model: claude-sonnet-4-6

tts:
  driver: elevenlabs

stream:
  rtmpBase: rtmp://live.twitch.tv/app

worldState:
  backend: redis
  redisUrl: redis://redis-service:6379

redis:
  enabled: true
```

```yaml
# values.staging.yaml
llm:
  provider: ollama
  baseUrl: http://ollama-node:11434
  model: mistral

tts:
  driver: kokoro

stream:
  rtmpBase: rtmp://live.twitch.tv/app   # pointing at staging test channel keys

worldState:
  backend: file
  path: /data/world-state

redis:
  enabled: false
```

---

### 13.8 GPU Scheduling

The LLM inference server (Ollama) is the main GPU consumer. In the homelab it runs outside K8s on the physical node — worker pods call it via HTTP. If you bring Ollama into the cluster:

```bash
kubectl label node proxmox-node-1 vtube/gpu-role=llm
```

```yaml
# In the Ollama deployment template
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
      - matchExpressions:
        - key: vtube/gpu-role
          operator: In
          values: [llm]
resources:
  limits:
    nvidia.com/gpu: 1
```

Worker containers themselves don't need GPUs — they just make HTTP calls to the LLM and TTS endpoints.

---

### 13.9 Persistent Storage

| Volume | Contents | Access |
|---|---|---|
| `world-state-pvc` | Tickets, messages, agent state (file-based mode) | ReadWriteMany — all workers |
| `repo-pvc` | The codebase being built | ReadWriteMany — coder writes, tester reads |
| `config-pvc` | Prompts, avatar assets, behavior configs | ReadOnlyMany — all workers |

In staging/prod on Proxmox, these are backed by the ZFS dataset via NFS. In cloud, EFS (AWS) or Filestore (GCP).

---

### 13.10 Deployment File Tree

```
deploy/
  docker/
    docker-compose.yml
    .env.dev
    .env.staging
    nginx-rtmp/
      nginx.conf
  helm/
    vtube/
      Chart.yaml
      values.yaml
      values.staging.yaml
      values.prod.yaml
      values.cloud.yaml
      templates/
        worker-deployment.yaml
        world-state-deployment.yaml
        chat-bridge-deployment.yaml
        redis-deployment.yaml
        configmap-prompts.yaml
        configmap-avatars.yaml
        secrets.yaml
        pvcs.yaml
        services.yaml
  k8s/
    namespaces.yaml
    storage-classes.yaml
    gpu-node-labels.sh
config/
  prompts/
    manager.txt
    coder.txt
    tester.txt
  avatars/
    manager/
    coder/
    tester/
  behaviors/
    ...
```

---

### 13.11 Open Deployment Questions

| Question | Options / Notes |
|---|---|
| xterm font + color scheme | Pick a monospace font and color theme that reads well on stream at low bitrate — test before locking in |
| Avatar pane sizing | Fixed character-width pane vs dynamic resize — tmux pane sizes are set at session creation; decide on a base resolution and stick to it |
| Audio in container | PulseAudio virtual sink inside container, piped into ffmpeg as audio input — test this early, it's the fiddliest part of the self-contained model |
| Speech bubble persistence | Auto-dismiss after N seconds vs require new message to replace — consider dead-air UX when agent is waiting on LLM |
| TTS + speech bubble sync | Bubble should appear when TTS starts and clear when it finishes — agent loop needs to track TTS playback state |
| Ollama in-cluster vs on-host | On-host (outside K8s) is simpler for homelab; in-cluster gives better resource scheduling if adding more nodes |
| Code execution isolation | Worker runs and executes code inside its own container (simplest) vs spawning an ephemeral K8s Job pod (stronger isolation, more setup) |
| tmux layout per role | Each role has a slightly different pane layout (e.g., tester shows pytest output where coder shows nvim) — define layouts in agent config |
