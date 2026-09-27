# Project Structure

```
virtualTubers/
├── app/
│   ├── agent.py          # Agent loop: Redis liveness key every tick, rate-limited bus heartbeat, dispatch via agent_handlers, per-role idle hooks
│   ├── agent_handlers/   # Every bus message handler, one module per role/concern (coder, tester, manager + backlog, operator, viewer, replay_relay, relay_files, common) + MESSAGE_HANDLERS / IDLE_TICK_HOOKS
│   ├── task_backlog.py   # Opt-in manager task sources (file | gitea) the backlog dispatcher pulls from
│   ├── relay_io.py       # The one race-safe implementation of in-container JSON relay files (agent -> replay pane, director -> tiles)
│   ├── llm_client.py     # Provider-switchable LLM client (Ollama | Claude)
│   ├── coding_backend.py # Swappable coding backend layer (native | opencode | aider) + TaskResult
│   ├── coding_backends/  # One adapter per backend provider
│   ├── git_client.py     # Local git ops per persona; push/PR no-op until GIT_SERVER_URL
│   ├── workspace_setup.py# Seeds coder workspace volumes from the sandbox template
│   ├── test_runner.py    # Tester's real pytest execution (copy-to-tmpdir, ro mounts)
│   ├── worker_control.py # Redis-backed per-worker on/off flag + local kill-file override + liveness key / health rows
│   ├── stream_supervisor.py # Starts/stops ffmpeg based on the on/off flag (kill file checked every ~0.5s; SIGUSR1 = emergency stop)
│   ├── avatar.py         # Terminal ASCII avatar dispatcher — polls agent_state.py, hands frames to an avatar_providers/ backend
│   ├── avatar_providers/ # Pluggable avatar rendering backends (builtin static face | ascii_avatar animated adapter)
│   ├── avatar_display.py # display_width()/build_bubble_box() shared by avatar.py and every avatar provider
│   ├── agent_state.py    # Small local state file bridging agent.py's activity to avatar.py's display
│   ├── session_log_parser.py # Saved Claude session logs -> redacted replay scripts
│   ├── replay.py         # Performs a replay script as a paced show (display-only, audio-synced, duet cue hooks)
│   ├── replay_pane.py    # "Rerun Theater" pane: idles, plays operator-requested episodes solo or as a duet director/follower
│   ├── revoice.py        # Per-airing narration pass: scenes + LLM-written spoken lines
│   ├── narration_store.py # Postgres cache for voiced airings; duet director persists, followers load the same airing
│   ├── episode_store.py   # Postgres-backed Rerun Theater episode library, with draft/approved review status (drafts never air)
│   ├── episode_validator.py # Upload gate: shape + name + leak audit + dry-run render, before an episode is stored
│   ├── tts_client.py     # Provider-switchable TTS (Piper | OpenAI | ElevenLabs), measured durations
│   ├── audio_player.py   # Best-effort WAV playback into the streamed PulseAudio sink
│   ├── build_layout.py   # Config-driven tmux layout engine (emits the tmux command sequence)
│   ├── tmux_control.py   # Agent's "hands": select a pane by name, type text/commands into it
│   ├── message_bus.py    # Shared Kafka producer/consumer/schema helper
│   └── tail_bus.py       # Rich configurable Kafka feed for the tmux "Message Bus" pane
├── services/
│   ├── message-logger/    # Consumes every bus message, logs it to Postgres
│   ├── message-api/       # FastAPI service: injects messages onto the bus, worker on/off + /workers/health, owns /replays upload + draft approve
│   ├── control-panel/     # Browser dashboard over message-api's HTTP surface: worker health column, drafts-awaiting-review table (docs/control_panel.md)
│   ├── twitch-presence/   # Watches Twitch chat, announces arriving viewers (viewer_joined)
│   ├── log-shipper/, campaign-manager/
│   └── 3layer-generator/  # Offline Arc -> Segment -> Dialogue generator; draft_submitter.py = opt-in auto-submit as review draft
├── scripts/
│   ├── emergency_stop.sh / .ps1     # Local kill switch: docker exec a kill file into worker containers (works with Redis down)
│   ├── emergency_resume.sh / .ps1   # Remove the kill file (back under Redis on/off control)
│   ├── send_test_message.sh / .ps1  # Preset POST /messages payloads (default target argyre 192.168.1.23)
│   └── render_architecture_graph.py # Renders docs/architecture_flow_diagram.png
├── sandbox/               # Seeded-bug workspace template the coder agents actually code on
├── repos/                 # Vendored third-party avatar repos (see repos/README.md) — e.g. ascii-avatar, used by avatar_providers/ascii_avatar.py
├── config/
│   ├── worker.yaml        # Annotated default/template worker config (selects a layout preset)
│   ├── backlog.example.yaml # Example task list for the manager's file backlog
│   ├── workers/           # Per-role configs (coder, manager, tester + coder-native/-opencode/-aider)
│   ├── panels/             # Reusable panel-TYPE defaults (kafka_feed, avatar, filetree, editor, htop)
│   └── layouts/            # Composition presets that place & size panels (coder, tester, manager)
├── docs/
│   ├── VTuber_AI_Dev_Team_Concept.md   # Full architecture & roadmap doc
│   ├── agent_flow_reference.md         # Text flow reference for AI agents (components, message types, flows, invariants)
│   ├── feature_flow_diagram.md         # Feature-level flow diagrams (overview, dev loop, Rerun Theater, rendering, content gen)
│   ├── architecture_flow_diagram.md    # Infra/data-ownership flow diagram
│   ├── agent.md, llm_client.md         # Agent loop and LLM client docs
│   ├── layout_system.md, panels.md, build_layout.md   # Config-driven panel system
│   ├── message_bus.md, message_bus_feed.md, message_logger.md, message_api.md   # Per-module docs
│   ├── agent_handlers.md, relay_io.md, task_backlog.md, worker_control.md, draft_submitter.md   # Handlers, relay IPC, backlog, on/off + health, draft auto-submit
│   ├── e2e_tests.md                     # Multi-agent end-to-end flow tests (fakes only)
├── tests/                  # pytest suite (incl. e2e_harness.py + test_e2e_dev_loop.py / test_e2e_duet.py)
├── Dockerfile              # Worker container image (Xvfb, tmux, ffmpeg, Python, etc.)
├── docker-compose.yml      # Full stack: 8 worker-* services + message-logger/-api, control-panel, twitch-presence, log-shipper, Redis, RTMP preview, generator; Kafka/Postgres behind opt-in profiles
├── install.sh / install.ps1, redeploy.sh   # Image builds; one-shot rebuild + force-recreate on the host
├── startup.sh              # Container entrypoint: sets up display, tmux layout, avatar, agent loop, and ffmpeg broadcaster
├── requirements.txt        # Python dependencies (worker image)
└── .env.example            # Template for stream keys, Kafka, and Postgres config
```
