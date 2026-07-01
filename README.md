# virtualTubers

## Summary

virtualTubers is an autonomous AI-powered VTuber streaming system where a team of AI agents (Manager, Coder, Tester) act as a live software development team. Each agent runs in its own Docker container, has its own personality and ASCII-art avatar, works inside a live terminal session (tmux + neovim/htop/etc.), and streams that session to Twitch over RTMP via ffmpeg. It's for anyone who wants to run an always-on, config-driven "AI dev team" stream without hand-building the streaming pipeline from scratch.

The project is early-stage: the agent brain (`app/agent.py`) and terminal avatar (`app/avatar.py`) are currently stub implementations that keep the container alive, write heartbeat messages, and cycle through avatar expressions — enough to validate the end-to-end pipeline (container → virtual display → tmux layout → RTMP → Twitch) before real LLM-driven behavior is wired in.

See [docs/VTuber_AI_Dev_Team_Concept.md](docs/VTuber_AI_Dev_Team_Concept.md) for the full architecture and design plan.

## Prerequisites

- Docker and Docker Compose
- An RTMP destination — a Twitch stream key for live streaming, or a local RTMP preview server (bundled via `rtmp-preview` in `docker-compose.yml`) for local testing
- (Optional) A running [Ollama](https://ollama.ai) instance for local LLM inference — the default worker config points at `http://localhost:11434`

## Installation

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd virtualTubers
   ```
2. Build the worker image (the `docker-compose.yml` expects a locally-built image and never pulls):
   ```bash
   docker build -t vtube-worker:latest .
   ```
3. Set your stream keys as environment variables (or in a `.env` file next to `docker-compose.yml`):
   ```bash
   export CODER_STREAM_KEY=your_twitch_stream_key
   export MANAGER_STREAM_KEY=your_twitch_stream_key
   export TESTER_STREAM_KEY=your_twitch_stream_key
   export STREAM_RTMP_URL=rtmp://live.twitch.tv/app   # omit to use the local rtmp-preview server
   ```

## Usage

Start the full stack (three workers + Redis + local RTMP preview):

```bash
docker compose up
```

This launches three containers — `worker-coder`, `worker-manager`, `worker-tester` — plus a shared `redis` instance and an `rtmp-preview` server for local testing. Each worker:

1. Boots a virtual display (Xvfb) and PulseAudio sink
2. Lays out a tmux session (file tree, ASCII avatar, editor/output pane, agent chat log, htop)
3. Opens that session in xterm on the virtual display
4. Starts the agent loop (`app/agent.py`)
5. Captures the display with ffmpeg and pushes it out over RTMP to the configured stream key

To preview locally without a real Twitch key, leave `STREAM_RTMP_URL` unset (it defaults to `rtmp://rtmp-preview:1935/live`) and view the stream with a player like VLC pointed at `rtmp://localhost:1935/live/<stream_key>`.

To run a single worker outside Docker for quick iteration on `app/agent.py` or `app/avatar.py`:

```bash
pip install -r requirements.txt
python3 app/avatar.py --config config/workers/coder.yaml
```

## Configuration

All runtime behavior is config-driven — no code changes needed to retune an agent.

- `config/worker.yaml` — the annotated template/default worker config (role, name, system prompt, LLM/voice/avatar/stream/world-state settings)
- `config/workers/coder.yaml`, `manager.yaml`, `tester.yaml` — per-role configs mounted into each container at `/config/worker.yaml`
- Environment variables (set via `docker-compose.yml` or shell) override config file values at runtime, notably: `STREAM_RTMP_URL`, `CODER_STREAM_KEY` / `MANAGER_STREAM_KEY` / `TESTER_STREAM_KEY`, `LLM_BASE_URL`, `DISPLAY_NUM`

Key sections inside a worker config:

| Section | Controls |
|---|---|
| `agent` | Role, display name, system prompt, tick rate, context window |
| `llm` | Provider (`ollama` \| `claude`), base URL, model, temperature |
| `voice` | TTS provider (`elevenlabs` \| `kokoro` \| `null`), voice ID, verbosity |
| `avatar` | Name, title, ASCII expression states, speech bubble sizing |
| `layout` | Which tmux layout variant to use (`coder` \| `tester` \| `manager`) |
| `stream` | RTMP URL/key, resolution, bitrate, fps |
| `world_state` | Shared state backend (`file` \| `redis`) and connection info |

## Project Structure

```
virtualTubers/
├── app/
│   ├── agent.py          # Agent loop (perceive/think/act loop) — currently a stub
│   └── avatar.py         # Terminal ASCII avatar renderer — currently a stub
├── config/
│   ├── worker.yaml        # Annotated default/template worker config
│   └── workers/           # Per-role configs (coder.yaml, manager.yaml, tester.yaml)
├── docs/
│   └── VTuber_AI_Dev_Team_Concept.md   # Full architecture & roadmap doc
├── Dockerfile              # Worker container image (Xvfb, tmux, ffmpeg, Python, etc.)
├── docker-compose.yml      # Local dev stack: 3 workers + Redis + RTMP preview
├── startup.sh              # Container entrypoint: sets up display, tmux layout, avatar, agent loop, and ffmpeg broadcaster
└── requirements.txt        # Python dependencies
```

## License

This project is licensed under the GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
