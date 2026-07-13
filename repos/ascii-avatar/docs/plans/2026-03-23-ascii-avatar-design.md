# ASCII Avatar for Claude Code — Design Document

**Date**: 2026-03-23
**Status**: Approved

## Overview

A separate Python process that runs in a tmux pane beside Claude Code. Renders a cyberpunk ASCII face that animates in response to Claude Code activity (thinking, speaking, listening, idle, error) and speaks responses aloud via local TTS. A persona system bundles visual theme, voice engine, voice ID, color accent, and personality traits into switchable profiles.

## Architecture

```
Claude Code (hooks/MCP) ──PUSH──> PULL── Avatar Process
                         ipc://              |-- State Machine
                                             |-- Renderer (blessed)
                                             |-- Persona Manager
                                             '-- Voice Engine
                                                  |-- KokoroEngine (default)
                                                  |-- ElevenLabsEngine (opt-in)
                                                  '-- PiperEngine (fallback)
```

Two processes, one Unix domain socket. Claude Code sends events via hooks or MCP tool calls. The avatar process receives events, updates its state machine, renders the appropriate animation frame, and optionally synthesizes + plays speech.

### IPC: ZeroMQ PUSH/PULL

- Socket: `ipc:///tmp/ascii-avatar.sock`
- Pattern: PUSH/PULL (not PUB/SUB — PUB/SUB drops messages before subscriber handshake)
- Events are JSON: `{"event": "state_change", "state": "thinking", "data": {...}}`
- Avatar runs PULL (receiver). Bridge runs PUSH (sender) — can be short-lived or persistent.
- Reconnection with exponential backoff on the PULL side.

### Event Types

| Event | Payload | Trigger |
|-------|---------|---------|
| `state_change` | `{state: "thinking\|speaking\|listening\|idle\|error"}` | Hook or MCP call |
| `speak_start` | `{text: "...", persona: "ghost"}` | Notification hook or MCP |
| `speak_end` | `{}` | Internal (after playback completes) |
| `heartbeat` | `{}` | Periodic health check |

## Components

### State Machine (`state_machine.py`)

- States: IDLE, THINKING, SPEAKING, LISTENING, ERROR
- Thread-safe transitions via `threading.Lock`
- Each state has: entry action, exit action, frame set reference, frame rate (modified by persona)
- SPEAKING state receives phoneme timing data to sync mouth frames
- Auto-return to IDLE after configurable timeout if no new events

### Renderer (`renderer.py`)

- `blessed` library for terminal rendering
- Designed for a tmux pane (NOT the same terminal as Claude Code)
- Frame cycling with configurable rates per state, modified by persona `frame_rate_modifier`
- Terminal resize handling
- Status bar: current state, last event timestamp, TTS status, connection status
- Color support detection (fallback to monochrome)
- Startup boot-up glitch animation before settling into idle
- `--compact` flag for smaller terminals
- Non-blocking — `q` or Ctrl+C to quit

### Frames (`frames/cyberpunk.py`)

- ~500+ lines, section-commented by state
- 5 states x 3-6 frames each
- Unicode box-drawing, block elements, braille dots
- ANSI escape codes for color accents (configurable per persona)
- ~20 lines tall, ~40 chars wide per frame

### Voice Layer

#### Abstract Interface (`voice/base.py`)

```python
class TTSEngine(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> tuple[np.ndarray, list[WordTiming]]: ...

    @abstractmethod
    def stream_synthesize(self, text: str) -> Generator[tuple[np.ndarray, WordTiming], None, None]: ...

    @property
    @abstractmethod
    def sample_rate(self) -> int: ...

    @abstractmethod
    def is_available(self) -> bool: ...
```

#### Kokoro Engine (`voice/kokoro_engine.py`) — Primary

- Wraps `kokoro-onnx` (82M params, ~300MB disk, ~80MB quantized)
- CPU inference at 5x realtime (~0.2s per sentence)
- **Native phoneme output**: generator yields `(graphemes, phonemes, audio)` — real viseme mapping, no estimation
- Word-level timestamps via `--timestamps` flag or captioned_speech API
- Lazy model loading
- 54 voices across 8 languages
- Apache 2.0

#### ElevenLabs Engine (`voice/elevenlabs_engine.py`) — Cloud Opt-in

- Wraps `elevenlabs` Python SDK
- Requires `ELEVENLABS_API_KEY` env var — never default
- Word-level timestamps via websocket streaming API
- Higher quality voices + voice cloning
- ~$0.30/1K chars (free tier available)

#### Piper Engine (`voice/piper_engine.py`) — Fallback

- Wraps `piper-tts`
- ~60MB models, <100ms latency
- Lower voice quality (slightly robotic)
- No word-level timestamps — falls back to proportional estimation
- GPL-3.0 license (noted for distribution implications)

#### Audio Player (`voice/audio_player.py`)

- `sounddevice` — uses system default output device (no config needed)
- Override via `SD_DEVICE` env var or `--audio-device` flag
- Non-blocking playback in separate thread
- Word/phoneme callbacks drive mouth animation
- `stop()` for interruption

### Persona System (`personas.py`)

```python
@dataclass
class Persona:
    name: str
    frames: str              # frame set name
    voice_engine: str        # "kokoro" | "elevenlabs" | "piper"
    voice_id: str            # engine-specific voice identifier
    accent_color: str        # ANSI color for frame accents
    personality: str         # "minimal" | "sage" | "glitch"
    frame_rate_modifier: float  # multiplier on base frame rates

PERSONAS = {
    "ghost": Persona("ghost", "cyberpunk", "kokoro", "af_bella", "cyan", "minimal", 1.0),
    "oracle": Persona("oracle", "cyberpunk", "kokoro", "bf_emma", "amber", "sage", 0.8),
    "spectre": Persona("spectre", "cyberpunk", "elevenlabs", "<voice-id>", "green", "glitch", 1.3),
}
```

Personality traits (light-touch, Option A):
- **minimal**: calm, long idle pauses, smooth transitions
- **sage**: slow, deliberate, longer thinking animations
- **glitch**: twitchy, random micro-glitches during idle, faster frame rates

### Bridge (`bridge/`)

#### CLI (`bridge/cli.py`)
- Argparse entry point: `python -m avatar.bridge.cli {think|speak|listen|idle|error} [text]`
- Safe — no shell injection (replaces the unsafe bash interpolation from original design)

#### Claude Code Bridge (`bridge/claude_code.py`)
- PUSH socket sender
- `send_thinking()`, `send_speaking(text)`, `send_listening()`, `send_idle()`, `send_error(msg)`

#### Hooks (`bridge/hooks.py`)
- Thin wrappers for Claude Code hook scripts
- `think()`, `respond(text)`, `listen()`, `error(msg)`

#### MCP Server (`bridge/mcp_server.py`)
- `mcp` Python SDK (PyPI: `mcp`)
- Tools: `avatar_think`, `avatar_speak`, `avatar_listen`, `avatar_idle`
- Runs as part of main process or sidecar

## Integration

### Primary: Claude Code Hooks (`settings.json`)

```json
{
  "hooks": {
    "PreToolUse": [{"command": "python -m avatar.bridge.cli think"}],
    "PostToolUse": [{"command": "python -m avatar.bridge.cli idle"}],
    "Notification": [{"command": "python -m avatar.bridge.cli speak \"$CLAUDE_NOTIFICATION\""}]
  }
}
```

Zero-config, event-driven, documented Claude Code feature.

### Secondary: MCP Server

```json
{
  "mcpServers": {
    "ascii-avatar": {
      "command": "python",
      "args": ["-m", "avatar.bridge.mcp_server"],
      "cwd": "~/projects/ascii-avatar"
    }
  }
}
```

For explicit tool-call control from within Claude Code conversations.

### tmux Launcher

`clauded-avatar` shell alias:
- Splits tmux: left = Claude Code, right = avatar
- Auto-sizes avatar pane to fit frame dimensions

## Package Layout

```
ascii-avatar/
├── pyproject.toml
├── README.md
├── USAGE.md
├── src/
│   └── avatar/
│       ├── __init__.py
│       ├── main.py
│       ├── renderer.py
│       ├── state_machine.py
│       ├── event_bus.py
│       ├── personas.py
│       ├── frames/
│       │   ├── __init__.py
│       │   └── cyberpunk.py
│       ├── voice/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── kokoro_engine.py
│       │   ├── elevenlabs_engine.py
│       │   ├── piper_engine.py
│       │   └── audio_player.py
│       └── bridge/
│           ├── __init__.py
│           ├── claude_code.py
│           ├── cli.py
│           ├── hooks.py
│           └── mcp_server.py
├── assets/
│   └── frames/
├── tests/
│   ├── __init__.py
│   ├── test_state_machine.py
│   ├── test_renderer.py
│   └── test_integration.py
├── scripts/
│   ├── install.sh
│   ├── demo.sh
│   ├── setup-hooks.sh
│   └── setup-tmux.sh
└── docs/
    └── plans/
```

## Dependencies

### Python (pyproject.toml)

```
kokoro-onnx          # Primary TTS (82M, Apache 2.0)
blessed              # Terminal rendering
pyzmq                # ZeroMQ IPC
sounddevice          # Audio playback (system default device)
numpy                # Audio arrays
mcp                  # MCP server SDK

[optional]
elevenlabs           # Cloud TTS (opt-in)
piper-tts            # Fallback TTS (GPL-3.0)
```

### System

- `portaudio-devel` (dnf) — required by sounddevice
- Kokoro models: `kokoro-v1.0.onnx` + `voices-v1.0.bin` in `~/.cache/ascii-avatar/models/`

## Graceful Degradation

| Missing | Behavior |
|---------|----------|
| Kokoro model | Animation-only mode, no audio, clear message pointing to install.sh |
| Audio device | Animation-only mode, no audio |
| tmux | Runs in current terminal (no split) |
| ElevenLabs key | Persona falls back to Kokoro voice |
| ZeroMQ socket | Renderer shows "waiting for connection" in status bar |

## Out of Scope

- Daemon mode
- GUI / non-terminal rendering
- Voice input / STT
- Custom frame editor
- Multi-avatar instances
- AI-driven personality (dynamic mood, inner monologue)
