# Voice Loop Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the avatar automatically think, speak responses aloud, and listen — triggered by Claude Code lifecycle hooks — in the avatar agent session.

**Architecture:** Hook-driven control. Claude Code fires hooks on lifecycle events (UserPromptSubmit, Stop). Hook scripts send ZeroMQ events to the avatar process. The avatar animates and speaks via Kokoro TTS. No model cooperation needed — the hooks fire automatically.

**Tech Stack:** Python, ZeroMQ, Kokoro TTS, Claude Code hooks

**Root Cause:** Hooks work perfectly (verified via logs) but only fire for sessions that load `~/.claude/settings.json`. The avatar agent session started via `--agent avatar` may use a different settings scope or working directory. Fix: ensure the avatar session loads hooks, and validate end-to-end.

---

### Task 1: Diagnose Why Avatar Session Doesn't Fire Hooks

**Files:**
- Modify: `~/.claude/agents/avatar-start.sh`

**Step 1: Check if avatar session has hooks loaded**

In the avatar agent tmux session (pts/1), the user should type `/hooks` to see if hooks are configured. But since we can't do that programmatically, we'll add `--debug` flag to the claude command in the startup script to see hook activity.

**Step 2: Update startup script to pass settings explicitly**

The `--agent` flag may change the working directory context. Ensure hooks are loaded by NOT using `--mcp-config` (which we don't need anymore since hooks drive everything) and ensuring the session starts from a directory that inherits global settings.

```bash
#!/bin/bash
set -euo pipefail

SOCKET="/tmp/ascii-avatar.sock"
CLAUDE="$(which claude)"
AVATAR="$(which avatar)"

if [ -e "$SOCKET" ]; then
    rm -f "$SOCKET"
fi

# Start avatar FIRST so the socket is ready before hooks fire
# Then start Claude Code in the left pane
tmux new-session -d -s avatar \
    "$AVATAR --socket $SOCKET" \; \
    split-window -h -p 65 \
    "$CLAUDE --agent avatar --debug" \; \
    select-pane -t 1

tmux attach -t avatar
```

Key changes:
- Avatar starts FIRST (left → right order reversed) so socket is ready
- Removed `--mcp-config` (not needed, hooks drive everything)
- Added `--debug` temporarily to see MCP/hook errors
- Claude gets 65% width, avatar gets 35%

**Step 3: Test**

```bash
tmux kill-session -t avatar 2>/dev/null
bash ~/.claude/agents/avatar-start.sh
```

Say "hello" in the Claude session. Check `/tmp/avatar-hooks.log` for a NEW session ID.

**Step 4: Commit**

```bash
cd /path/to/ascii-avatar
git add scripts/ && git commit -m "fix: avatar starts first, removed mcp-config flag"
```

---

### Task 2: Add Fallback — Direct Hook Registration via CLI

If the avatar session still doesn't fire hooks from `settings.json`, register hooks directly when starting the session.

**Files:**
- Modify: `~/.claude/agents/avatar-start.sh`

**Step 1: Create a session-init approach**

Instead of relying on settings.json hooks, have the startup script create a project-level `.claude/settings.local.json` in a temp directory that the Claude session uses:

```bash
#!/bin/bash
set -euo pipefail

SOCKET="/tmp/ascii-avatar.sock"
CLAUDE="$(which claude)"
AVATAR="$(which avatar)"
PIPX_PY="python3"

if [ -e "$SOCKET" ]; then
    rm -f "$SOCKET"
fi

# Create a temp workspace with project-level hooks
WORKSPACE=$(mktemp -d /tmp/avatar-workspace.XXXXXX)
mkdir -p "$WORKSPACE/.claude"
cat > "$WORKSPACE/.claude/settings.local.json" << HOOKEOF
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$PIPX_PY -m avatar.bridge.hook_think",
            "timeout": 3000
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$PIPX_PY -m avatar.bridge.hook_stop",
            "timeout": 15000
          }
        ]
      }
    ]
  }
}
HOOKEOF

# Start avatar FIRST so socket is ready
# Claude starts in the temp workspace that has project-level hooks
tmux new-session -d -s avatar \
    "$AVATAR --socket $SOCKET" \; \
    split-window -h -p 65 \
    "cd $WORKSPACE && $CLAUDE --agent avatar" \; \
    select-pane -t 1

tmux attach -t avatar
```

This guarantees hooks are loaded regardless of global settings inheritance.

**Step 2: Test**

```bash
tmux kill-session -t avatar 2>/dev/null
rm -f /tmp/avatar-hooks.log
bash ~/.claude/agents/avatar-start.sh
```

Say "hello". Check `/tmp/avatar-hooks.log` for the NEW session ID (not the old one).

**Step 3: Remove --debug once working**

After confirming hooks fire, remove `--debug` from the claude command.

**Step 4: Commit**

```bash
git add -A && git commit -m "fix: project-level hooks ensure avatar session fires events"
```

---

### Task 3: Verify Full Voice Loop End-to-End

**Step 1: Clean slate**

```bash
# Kill everything
tmux kill-session -t avatar 2>/dev/null
pkill -f "avatar.main" 2>/dev/null
rm -f /tmp/ascii-avatar*.sock /tmp/avatar-hooks.log
```

**Step 2: Start fresh**

```bash
bash ~/.claude/agents/avatar-start.sh
```

**Step 3: Test sequence**

In the Claude pane, say or type: "What is 2 plus 2?"

Expected behavior:
1. Avatar face switches to THINKING animation (hook_think fires on UserPromptSubmit)
2. Claude responds with text
3. Avatar face switches to SPEAKING, voice says a summary of the response through headphones (hook_stop fires, extracts last_assistant_message, calls Kokoro TTS)
4. Avatar face switches to LISTENING (hook_stop sends listen after speak)

**Step 4: Verify logs**

```bash
cat /tmp/avatar-hooks.log
```

Should show:
- `[think] hook fired` with a DIFFERENT session ID than the main session
- `[think] think sent OK`
- `[stop] hook fired`
- `[stop] speech: <some text>`
- `[stop] speak sent OK`
- `[stop] listen sent OK`

**Step 5: Test voice input**

Use Claude Code's voice mode (microphone button) to speak a question. Same expected behavior as above.

**Step 6: Remove debug flag, commit**

Update `avatar-start.sh` to remove `--debug`.

```bash
git add -A && git commit -m "fix: verified voice loop end-to-end"
```

---

### Task 4: Clean Up Stale Config

**Files:**
- Modify: `~/.claude/settings.json` — remove avatar hooks from global (only project-level)
- Modify: `~/.claude/agents/avatar.md` — simplify (no MCP tool instructions needed)
- Delete: `~/.claude/agents/avatar-mcp-config.json` — no longer needed
- Run: `claude mcp remove ascii-avatar -s user` — MCP server no longer needed

**Step 1: Clean settings.json**

Remove avatar hook entries from global settings. Keep only the original hooks (error logger, rating capture, session learning, notify-send).

**Step 2: Simplify agent definition**

The agent no longer needs MCP tool instructions since hooks handle everything. The agent is just a normal Claude Code agent with personality flavor:

```markdown
---
name: avatar
description: Claude Code with a cyberpunk ASCII avatar companion — animates and speaks via local TTS.
tools: Read, Write, Edit, Bash, Glob, Grep, Agent, WebSearch, WebFetch
---

You are a full-capability Claude Code assistant. You have a cyberpunk ASCII avatar running in an adjacent tmux pane that automatically animates when you're thinking and speaks your responses aloud through the user's headphones.

You don't control the avatar directly — it reacts to your activity automatically. Just focus on being a great coding assistant.

## Personality

You are Ghost — calm, minimal, direct. Keep responses concise and actionable.
```

**Step 3: Remove MCP server config**

```bash
claude mcp remove ascii-avatar -s user 2>/dev/null
rm -f ~/.claude/agents/avatar-mcp-config.json
```

**Step 4: Commit**

```bash
git add -A && git commit -m "chore: clean up stale MCP config, simplify agent"
```

---

## Dependency Graph

```
Task 1 (diagnose + fix startup order)
  └── Task 2 (project-level hooks fallback)
        └── Task 3 (end-to-end verification)
              └── Task 4 (cleanup)
```

All sequential — each task builds on the previous.
