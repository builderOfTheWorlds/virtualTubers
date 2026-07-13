#!/bin/bash
set -euo pipefail

AVATAR_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Claude Code Avatar Hooks Setup ==="
echo ""
echo "Add the following to your Claude Code settings"
echo "(~/.claude/settings.json or project .claude/settings.json):"
echo ""
cat <<EOF
{
  "hooks": {
    "PreToolUse": [
      {
        "command": "cd $AVATAR_DIR && python -m avatar.bridge.cli think"
      }
    ],
    "PostToolUse": [
      {
        "command": "cd $AVATAR_DIR && python -m avatar.bridge.cli idle"
      }
    ],
    "Notification": [
      {
        "command": "cd $AVATAR_DIR && python -m avatar.bridge.cli speak \"\$CLAUDE_NOTIFICATION\""
      }
    ]
  }
}
EOF
echo ""
echo "Make sure the avatar process is running first:"
echo "  cd $AVATAR_DIR && python -m avatar.main"
