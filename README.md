# virtualTubers

## Summary

virtualTubers is an autonomous AI-powered VTuber streaming system where a team of AI agents (Manager, Coder, Tester) act as a live software development team. Each agent runs in its own Docker container, has its own personality and ASCII-art avatar, works inside a live terminal session (tmux + neovim/htop/etc.), and streams that session to Twitch over RTMP via ffmpeg. It's for anyone who wants to run an always-on, config-driven "AI dev team" stream without hand-building the streaming pipeline from scratch.

The project is early-stage: the agent brain (`app/agent.py`) and terminal avatar (`app/avatar.py`) are currently stub implementations that keep the container alive, write heartbeat messages, and cycle through avatar expressions — enough to validate the end-to-end pipeline (container → virtual display → tmux layout → RTMP → Twitch) before real LLM-driven behavior is wired in.

See [docs/VTuber_AI_Dev_Team_Concept.md](docs/VTuber_AI_Dev_Team_Concept.md) for the full architecture and design plan.

## Prerequisites

- Docker and Docker Compose
- An RTMP destination — a Twitch stream key for live streaming, or a local RTMP preview server (bundled via `rtmp-preview` in `docker-compose.yml`) for local testing
- (Optional) A running [Ollama](https://ollama.ai) instance for local LLM inference — the default worker config points at `http://localhost:11434`
- A reachable Kafka broker (agents/services publish and consume inter-agent messages there) and a Postgres instance (every message is durably logged there) — neither is bundled in `docker-compose.yml`; point at existing instances via `.env`

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
3. Copy `.env.example` to `.env` and fill in your stream keys, Kafka bootstrap servers, and Postgres credentials:
   ```bash
   cp .env.example .env
   ```
   ```bash
   CODER_STREAM_KEY=your_twitch_stream_key
   MANAGER_STREAM_KEY=your_twitch_stream_key
   TESTER_STREAM_KEY=your_twitch_stream_key
   STREAM_RTMP_URL=rtmp://live.twitch.tv/app   # omit to use the local rtmp-preview server

   KAFKA_BOOTSTRAP_SERVERS=your_kafka_host:9092
   KAFKA_TOPIC=vtuber.messages

   POSTGRES_HOST=your_postgres_host
   POSTGRES_PORT=5432
   POSTGRES_DB=your_db
   POSTGRES_USER=your_user
   POSTGRES_PASSWORD=your_password
   ```
   `.env` is gitignored — never commit real credentials.

## Usage

Start the full stack (three workers + message-logger + message-api + Redis + local RTMP preview):

```bash
docker compose up
```

This launches three worker containers — `worker-coder`, `worker-manager`, `worker-tester` — plus `message-logger`, `message-api`, a shared `redis` instance, and an `rtmp-preview` server for local testing. Each worker:

1. Boots a virtual display (Xvfb) and PulseAudio sink
2. Lays out a tmux session (file tree, ASCII avatar, editor/output pane, agent chat log, htop)
3. Opens that session in xterm on the virtual display
4. Starts the agent loop (`app/agent.py`), which publishes heartbeats and consumes messages addressed to it over the Kafka bus
5. Captures the display with ffmpeg and pushes it out over RTMP to the configured stream key

To preview locally without a real Twitch key, leave `STREAM_RTMP_URL` unset (it defaults to `rtmp://rtmp-preview:1935/live`) and view the stream with a player like VLC pointed at `rtmp://localhost:1935/live/<stream_key>`.

### Inter-agent messaging (Kafka)

Agents talk to each other over a Kafka topic (`vtuber.messages` by default) instead of a file — see `docs/message_bus.md`. Every message is durably logged to Postgres by the `message-logger` service (`docs/message_logger.md`).

To inject a test message for an agent to pick up, use the `message-api` HTTP service (`docs/message_api.md`), exposed on port `8090`:

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "say hello"}}'
```

The `coder` worker's agent loop and its tmux "agent chat" pane will pick up the message; `manager`/`tester` won't, since it wasn't addressed to them or broadcast.

To run a single worker outside Docker for quick iteration on `app/agent.py` or `app/avatar.py`:

```bash
pip install -r requirements.txt
python3 app/avatar.py --config config/workers/coder.yaml
```

## Configuration

All runtime behavior is config-driven — no code changes needed to retune an agent.

- `config/worker.yaml` — the annotated template/default worker config (role, name, system prompt, LLM/voice/avatar/stream/world-state/message-bus settings)
- `config/workers/coder.yaml`, `manager.yaml`, `tester.yaml` — per-role configs mounted into each container at `/config/worker.yaml`
- Environment variables (set via `docker-compose.yml` or `.env`) override config file values at runtime, notably: `STREAM_RTMP_URL`, `CODER_STREAM_KEY` / `MANAGER_STREAM_KEY` / `TESTER_STREAM_KEY`, `LLM_BASE_URL`, `DISPLAY_NUM`, `WORKER_ID`, `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`, `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`

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
| `message_bus` | Kafka backend, bootstrap servers, topic, and this worker's ID |

## Project Structure

```
virtualTubers/
├── app/
│   ├── agent.py          # Agent loop (perceive/think/act loop) — currently a stub
│   ├── avatar.py         # Terminal ASCII avatar renderer — currently a stub
│   ├── message_bus.py    # Shared Kafka producer/consumer/schema helper
│   └── tail_bus.py       # Live message-bus display, used by the tmux "agent chat" pane
├── services/
│   ├── message-logger/    # Consumes every bus message, logs it to Postgres
│   └── message-api/       # FastAPI service for injecting test messages onto the bus
├── config/
│   ├── worker.yaml        # Annotated default/template worker config
│   └── workers/           # Per-role configs (coder.yaml, manager.yaml, tester.yaml)
├── docs/
│   ├── VTuber_AI_Dev_Team_Concept.md   # Full architecture & roadmap doc
│   ├── message_bus.md, message_logger.md, message_api.md   # Per-module docs
├── tests/                  # pytest suite (message_bus, message-api)
├── Dockerfile              # Worker container image (Xvfb, tmux, ffmpeg, Python, etc.)
├── docker-compose.yml      # Local dev stack: 3 workers + message-logger + message-api + Redis + RTMP preview
├── startup.sh              # Container entrypoint: sets up display, tmux layout, avatar, agent loop, and ffmpeg broadcaster
├── requirements.txt        # Python dependencies (worker image)
└── .env.example            # Template for stream keys, Kafka, and Postgres config
```

## License

This project is licensed under the GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
