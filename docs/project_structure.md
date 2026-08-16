# Project Structure

```
virtualTubers/
├── app/
│   ├── agent.py          # Agent loop (perceive/think/act): heartbeats, task narration, real coding + testing flows, duet replay relay
│   ├── llm_client.py     # Provider-switchable LLM client (Ollama | Claude)
│   ├── coding_backend.py # Swappable coding backend layer (native | opencode | aider) + TaskResult
│   ├── coding_backends/  # One adapter per backend provider
│   ├── git_client.py     # Local git ops per persona; push/PR no-op until GIT_SERVER_URL
│   ├── workspace_setup.py# Seeds coder workspace volumes from the sandbox template
│   ├── test_runner.py    # Tester's real pytest execution (copy-to-tmpdir, ro mounts)
│   ├── worker_control.py # Redis-backed per-worker on/off flag (agent + stream pause/resume)
│   ├── stream_supervisor.py # Starts/stops ffmpeg based on the on/off flag (replaces startup.sh's raw ffmpeg call)
│   ├── avatar.py         # Terminal ASCII avatar dispatcher — polls agent_state.py, hands frames to an avatar_providers/ backend
│   ├── avatar_providers/ # Pluggable avatar rendering backends (builtin static face | ascii_avatar animated adapter)
│   ├── avatar_display.py # display_width()/build_bubble_box() shared by avatar.py and every avatar provider
│   ├── agent_state.py    # Small local state file bridging agent.py's activity to avatar.py's display
│   ├── session_log_parser.py # Saved Claude session logs -> redacted replay scripts
│   ├── replay.py         # Performs a replay script as a paced show (display-only, audio-synced, duet cue hooks)
│   ├── replay_pane.py    # "Rerun Theater" pane: idles, plays operator-requested episodes solo or as a duet director/follower
│   ├── revoice.py        # Per-airing narration pass: scenes + LLM-written spoken lines
│   ├── narration_store.py # Postgres cache for voiced airings; duet director persists, followers load the same airing
│   ├── episode_store.py   # Postgres-backed Rerun Theater episode library (replaced the /data/replays mount)
│   ├── episode_validator.py # Upload gate: shape + name + leak audit + dry-run render, before an episode is stored
│   ├── tts_client.py     # Provider-switchable TTS (Piper | OpenAI | ElevenLabs), measured durations
│   ├── audio_player.py   # Best-effort WAV playback into the streamed PulseAudio sink
│   ├── build_layout.py   # Config-driven tmux layout engine (emits the tmux command sequence)
│   ├── tmux_control.py   # Agent's "hands": select a pane by name, type text/commands into it
│   ├── message_bus.py    # Shared Kafka producer/consumer/schema helper
│   └── tail_bus.py       # Rich configurable Kafka feed for the tmux "Message Bus" pane
├── services/
│   ├── message-logger/    # Consumes every bus message, logs it to Postgres
│   ├── message-api/       # FastAPI service: injects test messages onto the bus, and owns /replays episode upload
│   └── twitch-presence/   # Watches Twitch chat, announces arriving viewers (viewer_joined)
├── sandbox/               # Seeded-bug workspace template the coder agents actually code on
├── repos/                 # Vendored third-party avatar repos (see repos/README.md) — e.g. ascii-avatar, used by avatar_providers/ascii_avatar.py
├── config/
│   ├── worker.yaml        # Annotated default/template worker config (selects a layout preset)
│   ├── workers/           # Per-role configs (coder, manager, tester + coder-native/-opencode/-aider)
│   ├── panels/             # Reusable panel-TYPE defaults (kafka_feed, avatar, filetree, editor, htop)
│   └── layouts/            # Composition presets that place & size panels (coder, tester, manager)
├── docs/
│   ├── VTuber_AI_Dev_Team_Concept.md   # Full architecture & roadmap doc
│   ├── agent.md, llm_client.md         # Agent loop and LLM client docs
│   ├── layout_system.md, panels.md, build_layout.md   # Config-driven panel system
│   ├── message_bus.md, message_bus_feed.md, message_logger.md, message_api.md   # Per-module docs
├── tests/                  # pytest suite (agent, llm_client, message_bus, message-api, build_layout, tail_bus)
├── Dockerfile              # Worker container image (Xvfb, tmux, ffmpeg, Python, etc.)
├── docker-compose.yml      # Local dev stack: 3 workers + message-logger + message-api + Redis + RTMP preview
├── startup.sh              # Container entrypoint: sets up display, tmux layout, avatar, agent loop, and ffmpeg broadcaster
├── requirements.txt        # Python dependencies (worker image)
└── .env.example            # Template for stream keys, Kafka, and Postgres config
```
