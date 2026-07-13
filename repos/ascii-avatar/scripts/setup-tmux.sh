#!/bin/bash
set -euo pipefail

AVATAR_DIR="$(cd "$(dirname "$0")/.." && pwd)"

ALIAS_LINE="alias clauded-avatar='tmux new-session \"clauded\" \; split-window -h -l 45 \"cd $AVATAR_DIR && python -m avatar.main\"'"

echo "=== tmux Integration Setup ==="
echo ""
echo "Adding alias to ~/.bashrc:"
echo "  $ALIAS_LINE"
echo ""

read -p "Add to ~/.bashrc? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "" >> ~/.bashrc
    echo "# ASCII Avatar for Claude Code" >> ~/.bashrc
    echo "$ALIAS_LINE" >> ~/.bashrc
    echo "Added! Run: source ~/.bashrc && clauded-avatar"
else
    echo "Skipped. Add manually if desired."
fi
