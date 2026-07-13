# Usage

## Starting the Avatar

```bash
python -m avatar.main [OPTIONS]
```

Or, after installing the package (`pip install -e .`):

```bash
avatar [OPTIONS]
```

### CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--persona NAME` | `ghost` | Persona preset: `ghost`, `oracle`, `spectre` |
| `--socket PATH` | `/tmp/ascii-avatar.sock` | Unix socket path for the event bus |
| `--no-voice` | off | Disable TTS; animation only |
| `--no-color` | off | Disable ANSI colors |
| `--voice VOICE_ID` | persona default | Override the persona's voice ID |
| `--audio-device INDEX` | system default | Override audio output device (integer index) |
| `--compact` | off | Compact display mode |
| `--headless` | off | Run event bus and state machine without terminal rendering (useful for testing) |
| `-v`, `--verbose` | off | Enable DEBUG logging |

Examples:

```bash
# Oracle persona, no TTS
python -m avatar.main --persona oracle --no-voice

# Headless mode for CI / integration tests
python -m avatar.main --headless -v

# Custom socket path
python -m avatar.main --socket /tmp/my-avatar.sock
```

## Rendering Modes

### Layered 2.5D (default for ghost persona)

The layered renderer composites 8 depth-ordered layers with parallax offsets for a
3D holographic effect. Requires a sixel-capable terminal.

Frames are pre-rendered at first launch (~5-15 seconds) and cached to
`~/.cache/ascii-avatar/frames/`. Subsequent launches are instant.

To regenerate assets from a custom reference image:

```bash
python scripts/generate_layers.py --reference /path/to/face.png --output assets/layers
```

To clear the frame cache:

```bash
rm -rf ~/.cache/ascii-avatar/frames/
```

## Sending Events via CLI

Use the bridge CLI to push events to a running avatar process:

```bash
python -m avatar.bridge.cli [--socket PATH] COMMAND
```

| Command | Description |
|---------|-------------|
| `think` | Signal thinking state |
| `speak TEXT...` | Speak text aloud and animate mouth |
| `listen` | Signal listening state |
| `idle` | Return to idle state |
| `error [MESSAGE]` | Signal error state |

Examples:

```bash
python -m avatar.bridge.cli think
python -m avatar.bridge.cli speak "Analysis complete."
python -m avatar.bridge.cli idle
python -m avatar.bridge.cli error "Build failed"
```

The `--socket` flag must match the socket path used when starting the avatar.

## Claude Code Hooks Setup

Hooks fire automatically on Claude Code tool events, animating the avatar during normal use.

Add the following to `~/.claude/settings.json` (global) or `.claude/settings.json` (project):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "command": "cd /path/to/ascii-avatar && python -m avatar.bridge.cli think"
      }
    ],
    "PostToolUse": [
      {
        "command": "cd /path/to/ascii-avatar && python -m avatar.bridge.cli idle"
      }
    ],
    "Notification": [
      {
        "command": "cd /path/to/ascii-avatar && python -m avatar.bridge.cli speak \"$CLAUDE_NOTIFICATION\""
      }
    ]
  }
}
```

Replace `/path/to/ascii-avatar` with the absolute path to this repo, or run `bash scripts/setup-hooks.sh` to print the config with the path filled in.

## MCP Server Setup

The MCP server exposes avatar control as tools that Claude can call directly.

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "ascii-avatar": {
      "command": "python",
      "args": ["-m", "avatar.bridge.mcp_server"],
      "cwd": "/path/to/ascii-avatar"
    }
  }
}
```

Available MCP tools:

| Tool | Description |
|------|-------------|
| `avatar_think` | Signal thinking state |
| `avatar_speak` | Speak text aloud with TTS and mouth animation |
| `avatar_listen` | Signal listening state |
| `avatar_idle` | Return to idle state |

## tmux Integration

To open Claude Code and the avatar side-by-side automatically, run:

```bash
bash scripts/setup-tmux.sh
```

This adds a `clauded-avatar` alias to `~/.bashrc` that creates a new tmux session with the avatar in a 45-column split pane. After sourcing, run:

```bash
source ~/.bashrc && clauded-avatar
```

## Troubleshooting

**No audio / `sounddevice` error**
Install the `portaudio` system library before installing Python dependencies:
- Debian/Ubuntu: `sudo apt install portaudio19-dev`
- Fedora/RHEL: `sudo dnf install portaudio-devel`

**"Kokoro model not found"**
Run `bash scripts/install.sh` to download the ONNX model and voice pack to `~/.cache/ascii-avatar/models/`. The avatar falls back to animation-only mode until models are present.

**Socket errors / "Avatar not connected"**
- Ensure the avatar process is running before sending events.
- Check that the `--socket` path matches on both sides (default: `/tmp/ascii-avatar.sock`).
- On permission errors, verify the socket directory is writable by your user.

**ElevenLabs not working (`spectre` persona)**
Set `ELEVENLABS_API_KEY` in your environment. Without it, the `spectre` persona falls back to Kokoro if models are available, otherwise runs in animation-only mode.
