# Avatar Agent Setup

## Prerequisites

- ascii-avatar installed (`pip install -e .`)
- `ANTHROPIC_API_KEY` set in environment (for Haiku agent decisions)

## Running the Agent

```bash
# With terminal rendering (default persona: ghost)
avatar --agent --persona ghost

# Headless mode (no terminal, agent only)
avatar --agent --headless

# With custom socket path
avatar --agent --socket /tmp/my-avatar.sock
```

## Claude Code Hook Configuration

Add to your Claude Code `settings.json` (global or project-level) to route all hook events through the unified forwarder:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "type": "command",
        "command": "python3 /path/to/ascii-avatar/scripts/claude-hook-event.py --socket /tmp/ascii-avatar.sock"
      }
    ],
    "PostToolUse": [
      {
        "type": "command",
        "command": "python3 /path/to/ascii-avatar/scripts/claude-hook-event.py --socket /tmp/ascii-avatar.sock"
      }
    ],
    "PostToolUseFailure": [
      {
        "type": "command",
        "command": "python3 /path/to/ascii-avatar/scripts/claude-hook-event.py --socket /tmp/ascii-avatar.sock"
      }
    ],
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "python3 /path/to/ascii-avatar/scripts/claude-hook-event.py --socket /tmp/ascii-avatar.sock"
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "python3 /path/to/ascii-avatar/scripts/claude-hook-event.py --socket /tmp/ascii-avatar.sock"
      }
    ]
  }
}
```

## How It Works

1. Claude Code fires hook events (PreToolUse, PostToolUse, etc.)
2. Each hook runs `claude-hook-event.py` which forwards raw JSON to the avatar's ZeroMQ socket
3. The avatar agent (Haiku) batches events every 3 seconds, evaluates context, and decides:
   - **Visual state**: Which face animation to show (thinking, speaking, error, idle)
   - **Speech**: Whether to speak and what to say (max 10 words, Ghost personality)
4. The renderer updates the terminal display accordingly

## Migrating from Legacy Hooks

If you were using the old hook scripts (`claude-hook-speak.py`), replace them with the unified forwarder above. The old scripts sent pre-processed events; the agent mode expects raw hook data.
