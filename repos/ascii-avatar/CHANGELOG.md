# Changelog

## [0.2.0] - 2026-03-26

### Added
- **Layered 2.5D avatar system**: depth-ordered compositing with parallax for 3D-feeling animation
- Sixel-first rendering: pixel-perfect frames on supported terminals
- 5 expressive states with distinct visual language (idle/thinking/speaking/listening/error)
- GITS-inspired color grading (cyan highlights, violet midtones, purple shadows)
- Pre-rendered frame atlas with disk caching (~5s first launch, instant after)
- `scripts/generate_layers.py` for asset generation from reference images
- 47 layer PNG assets (procedural backgrounds/overlays, geometric expressions)

### Changed
- `ghost` persona now uses `layered2d` frame set (was `portrait`)

## 0.1.0 (2026-03-24)

Initial public release.

### Features
- Five-state avatar animation (idle, thinking, speaking, listening, error)
- Kokoro ONNX TTS engine (local, 82M params, 5x realtime)
- ElevenLabs cloud TTS (opt-in)
- Piper fallback TTS
- Phoneme-driven mouth animation synced to word timings
- Boot glitch animation on startup
- Connection heartbeat with staleness tracking
- Three personas: Ghost (cyan), Oracle (amber), Spectre (green)
- ZeroMQ IPC event bus (PUSH/PULL over Unix socket)
- Claude Code hook integration (UserPromptSubmit, Stop, Notification)
- MCP server for tool-based avatar control
- Portrait mode (image-to-ASCII conversion)
- Bridge CLI for manual event injection
- Tmux launcher script (split-pane setup)
- Headless mode for CI/testing
- 109 tests
