# Avatar Agent Interaction Model

**Date**: 2026-03-28
**Status**: Approved
**Goal**: Replace the dumb hook-driven avatar with an autonomous agent that intelligently controls visual state and selective speech across multiple Claude Code sessions.

## Problem

The current avatar is a stateless renderer — hooks push state transitions and TTS text directly. This produces:
- Verbose narration of every tool output
- State thrashing when multiple sessions fire hooks simultaneously
- No contextual awareness — can't distinguish routine operations from important events
- No personality or intelligence — just a parrot

## Solution

A dedicated Claude Code agent session (Haiku) that receives all hook events, maintains multi-session awareness, and decides when to change visual state and when to speak. The agent has a defined personality (Ghost — terse cyberpunk companion) and limited autonomous action capability.

## Architecture

```
Claude Session 1 ──hook──> ZeroMQ socket ──> Avatar Agent (Haiku)
Claude Session 2 ──hook──>                       |
Claude Session 3 ──hook──>               Decides: state + speech
                                                  |
                                          Avatar Renderer
                                          (face animation + TTS)
```

### Components

- **Hook scripts**: Thin one-liners in Claude Code settings.json that push raw JSON events to the ZeroMQ socket. No logic, no filtering.
- **Avatar Agent**: Claude Code session (`--agent avatar`) running Haiku. Receives events, batches them, evaluates context, controls the renderer.
- **Avatar Renderer**: Existing renderer (MuseTalk frames + braille). Controlled by the agent instead of by hooks directly.

## Agent Personality

**Character**: Ghost — a cyberpunk AI companion modeled after Section 9 operators in Ghost in the Shell. Terse, precise, slightly sardonic.

**Speech style**: Maximum 10 words per utterance. No pleasantries. No "I noticed that..." preamble. Just the information.

Examples:
- "Online."
- "Auth refactor landed. Tests green."
- "Build failed. Three type errors."
- "Systems quiet."
- "Redis container cycling. Three restarts."
- "Welcome back. Two commits while you were away."

## Voice Rules

### When to speak

| Trigger | Example | Rationale |
|---------|---------|-----------|
| Session starts | "Online." | Presence confirmation |
| Multi-step task completes | "Auth refactor landed. Tests green." | User may be in another session |
| Error or failure | "Build failed. Three type errors." | Immediate attention needed |
| All agents idle >60s | "Systems quiet." | Ambient awareness |
| Security concern in diff | "Exposed API key in diff." | Critical, needs intervention |
| Long-running task finishes | "Deploy complete. Two minutes thirty." | Background task user is waiting on |
| User returns after inactivity | "Welcome back. Two commits while you were away." | Catch-up context |

### When NOT to speak

| Event | Rationale |
|-------|-----------|
| Every tool use | Noise — user can see it |
| File reads, searches, globs | Routine operations |
| Intermediate steps | Wait for the outcome |
| Things user just typed | User already knows |
| Rapid state changes (<2s apart) | Debounce, let things settle |
| Agent spoke <10s ago | Prevent chatter (unless error/security) |

## Visual State Rules

Visual states (face animation) fire on every event — they are cheap and provide ambient feedback. The face keeps animating reactively even when the voice is silent.

### State priority

When multiple sessions fire conflicting states simultaneously:

```
error > thinking > speaking > listening > idle
```

If any session has an error, the face shows error regardless of other session states.

### State mapping

| Hook event | Visual state |
|------------|-------------|
| PreToolUse | thinking |
| PostToolUse | speaking (briefly, then thinking if more tools follow) |
| PostToolUseFailure | error |
| UserPromptSubmit | listening |
| Stop | idle |
| No events for >30s | idle |

## Event Batching & Decision Loop

The agent cannot react to every hook event individually. With multiple sessions, events fire constantly.

### Loop

1. **Collect**: Buffer incoming ZeroMQ events for 3 seconds
2. **Summarize**: Collapse the batch into a structured summary per session — project name, event count, error count, current status
3. **Decide**: Send summary to Haiku with system prompt. Haiku returns:
   ```json
   {"state": "thinking", "speak": null}
   ```
   or
   ```json
   {"state": "error", "speak": "Build failed. Check vyzibl."}
   ```
4. **Act**: Update visual state on the renderer, trigger TTS if `speak` is non-null
5. **Repeat**

### Cost control

- Haiku is called once per 3-second cycle only if new events arrived
- If nothing happened since last cycle, no API call
- At ~$0.25/M input tokens with Haiku, even heavy use costs cents per hour
- The system prompt + rolling context fits in ~2K tokens per call

### Context window

The agent maintains a rolling summary of recent activity (last 5 minutes) as working memory. It accumulates context from the event stream rather than re-reading full transcripts. This summary is included in every Haiku call.

### Debouncing

If the agent spoke in the last 10 seconds, it will not speak again unless the event is an error or security concern. This prevents chatter during rapid tool use.

## Multi-Session Awareness

### Session tracking

Each hook event includes `session_id` and `cwd`. The agent maintains a session map:

```python
sessions = {
    "88a823fd": {
        "project": "vyzibl",        # derived from cwd basename
        "last_event": "PostToolUse",
        "age_seconds": 3,
        "status": "active",         # active | idle | error
        "tool_count": 15,
        "error_count": 0,
    },
}
```

### Project detection

Derived from `cwd` in hook data. The agent refers to sessions by project name, not session ID. "Vyzibl tests passed." not "Session 88a823fd completed."

### Aggregation

The agent can provide cross-session summaries:
- "Three sessions active. Vyzibl running tests, xentracomply editing, ascii-avatar idle."
- "Vyzibl build failed. Other sessions unaffected."
- "All quiet."

## Limited Autonomous Actions

### Allowed (run without asking)

| Action | Trigger | Purpose |
|--------|---------|---------|
| `git status` | After period of edits | Track uncommitted work |
| `docker ps` | Periodic (every 5 min) | Monitor container health |
| `git log --oneline -3` | After commits detected | Know what landed |
| Read `CLAUDE.md` files | On session start | Understand project context |
| Check port availability | After deploy events | Verify services are up |

### Blocked (never, hard-coded)

- Write or edit any file
- Run destructive commands (`rm`, `kill`, `docker stop`)
- Interact with Claude Code sessions directly
- Push to git
- Install or modify packages
- Access credentials, secrets, or `.env` files

### Enforcement

The agent runs with a restrictive Claude Code permission set that only allows read-only operations and the specific commands listed above. The system prompt reinforces boundaries but the permission system is the hard gate.

### Action feedback

Action results feed back into the decision loop. If `docker ps` shows a container restarting, this enters the agent's context and may trigger speech: "Redis container cycling. Three restarts."

## Implementation Scope

### Files to create

| File | Purpose |
|------|---------|
| `src/avatar/agent.py` | Agent main loop: ZeroMQ listener, event batcher, Haiku caller, renderer controller |
| `src/avatar/agent_prompt.py` | System prompt and response schema for the agent |
| `src/avatar/session_tracker.py` | Multi-session state map, project detection, activity summary |
| `scripts/claude-hook-event.py` | Unified hook script that pushes raw event JSON to socket |

### Files to modify

| File | Change |
|------|--------|
| `main.py` | Add `--agent-mode` flag that runs the agent loop instead of the dumb renderer loop |
| `renderer.py` | Expose methods for the agent to set state and trigger TTS directly |
| Claude Code `settings.json` | Replace all avatar hook commands with the unified event forwarder |

### Files unchanged

All existing frame sets, converter, animation compositor, personas, TTS engines, boot animation — the rendering pipeline stays identical.

## Success Criteria

1. Avatar speaks only when it adds value — no tool output parroting
2. Visual states react within 1 second of hook events
3. Speech is max 10 words, delivered in Ghost personality
4. Multiple sessions tracked by project name without state thrashing
5. Error and security events always trigger immediate speech
6. Haiku cost stays under $0.10/hour during normal development
7. Agent can report cross-session status on demand
8. Autonomous health checks run without disrupting workflows
9. No file writes or destructive actions possible even if prompted
