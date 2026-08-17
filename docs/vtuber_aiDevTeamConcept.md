# VTuber AI Dev Team — Concept & Architecture Planning Document

*Living Document — v0.2 Draft*

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