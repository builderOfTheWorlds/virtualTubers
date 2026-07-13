# Avatar Agent Interaction Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stateless hook-driven avatar with an autonomous Haiku agent that batches multi-session events, decides when to change visual state and when to speak, and controls the existing renderer/TTS pipeline.

**Architecture:** A new agent loop (`src/avatar/agent.py`) listens on the existing ZeroMQ PULL socket, buffers events for 3-second windows, tracks sessions by project name (`session_tracker.py`), calls Haiku via the Anthropic SDK for state+speech decisions (`agent_prompt.py`), then drives the renderer and TTS directly. Hook scripts are replaced with a single thin event forwarder. The rendering pipeline (frames, compositor, mouth sync, TTS engines) is unchanged.

**Tech Stack:** Python 3.11+, pyzmq (existing), anthropic SDK (already in deps), pytest

**Spec:** `docs/specs/2026-03-28-avatar-agent-interaction-model.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/avatar/session_tracker.py` | Create | Multi-session state map, project name detection, activity summaries |
| `src/avatar/agent_prompt.py` | Create | Haiku system prompt, response schema, batch-to-prompt formatting |
| `src/avatar/agent.py` | Create | Agent main loop: event collection, batching, Haiku calls, renderer/TTS control |
| `scripts/claude-hook-event.py` | Create | Unified hook script — reads stdin JSON, pushes raw event to ZeroMQ |
| `src/avatar/main.py` | Modify | Add `--agent` flag that runs the agent loop instead of the dumb render loop |
| `tests/test_session_tracker.py` | Create | Unit tests for session tracker |
| `tests/test_agent_prompt.py` | Create | Unit tests for prompt building and response parsing |
| `tests/test_agent.py` | Create | Unit tests for agent loop (mocked Haiku + ZeroMQ) |
| `tests/test_hook_event.py` | Create | Unit tests for unified hook script |

---

### Task 1: Session Tracker — Data Model

**Files:**
- Create: `src/avatar/session_tracker.py`
- Create: `tests/test_session_tracker.py`

The session tracker maintains a map of active Claude Code sessions, keyed by session ID. Each session stores project name (derived from `cwd`), event counts, error counts, last event type, and timestamp. It provides methods to update from raw events and produce summaries.

- [ ] **Step 1: Write failing test — SessionInfo dataclass and SessionTracker.update()**

```python
# tests/test_session_tracker.py
import time
import pytest
from avatar.session_tracker import SessionInfo, SessionTracker


class TestSessionTracker:
    def test_update_creates_new_session(self):
        tracker = SessionTracker()
        tracker.update(
            session_id="abc123",
            cwd="/home/user/projects/vyzibl",
            hook_event="PreToolUse",
        )
        info = tracker.get("abc123")
        assert info is not None
        assert info.project == "vyzibl"
        assert info.status == "active"
        assert info.tool_count == 1
        assert info.error_count == 0
        assert info.last_event == "PreToolUse"

    def test_update_increments_tool_count(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PreToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PreToolUse")
        info = tracker.get("s1")
        assert info.tool_count == 3

    def test_update_counts_errors(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUseFailure")
        info = tracker.get("s1")
        assert info.error_count == 1
        assert info.status == "error"

    def test_project_from_cwd(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/ascii-avatar", "PreToolUse")
        assert tracker.get("s1").project == "ascii-avatar"

    def test_project_from_home_dir(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user", "PreToolUse")
        assert tracker.get("s1").project == "user"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_session_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'avatar.session_tracker'`

- [ ] **Step 3: Implement SessionInfo and SessionTracker.update()**

```python
# src/avatar/session_tracker.py
"""Multi-session state tracker for the avatar agent."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass
class SessionInfo:
    session_id: str
    project: str
    last_event: str
    last_update: float  # monotonic timestamp
    status: str = "active"  # "active" | "idle" | "error"
    tool_count: int = 0
    error_count: int = 0


ERROR_EVENTS = frozenset({"PostToolUseFailure"})


class SessionTracker:
    """Tracks multiple Claude Code sessions by session ID."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionInfo] = {}

    def update(self, session_id: str, cwd: str, hook_event: str) -> None:
        now = time.monotonic()
        project = PurePosixPath(cwd).name or "unknown"

        if session_id in self._sessions:
            info = self._sessions[session_id]
            info.last_event = hook_event
            info.last_update = now
            info.tool_count += 1
            if hook_event in ERROR_EVENTS:
                info.error_count += 1
                info.status = "error"
            else:
                info.status = "active"
        else:
            is_error = hook_event in ERROR_EVENTS
            self._sessions[session_id] = SessionInfo(
                session_id=session_id,
                project=project,
                last_event=hook_event,
                last_update=now,
                status="error" if is_error else "active",
                tool_count=1,
                error_count=1 if is_error else 0,
            )

    def get(self, session_id: str) -> SessionInfo | None:
        return self._sessions.get(session_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_session_tracker.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /path/to/ascii-avatar
git add src/avatar/session_tracker.py tests/test_session_tracker.py
git commit -m "feat(agent): add SessionTracker data model with update and project detection"
```

---

### Task 2: Session Tracker — Summaries, Staleness, and Reset

**Files:**
- Modify: `src/avatar/session_tracker.py`
- Modify: `tests/test_session_tracker.py`

Add `summarize()` to produce per-session summaries for the Haiku prompt, `mark_idle()` for sessions that go stale, and `reset_counts()` to clear per-cycle counters after each decision.

- [ ] **Step 1: Write failing tests for summarize, staleness, and reset**

```python
# Append to tests/test_session_tracker.py

class TestSessionSummary:
    def test_summarize_single_session(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        summary = tracker.summarize()
        assert len(summary) == 1
        assert summary[0]["project"] == "vyzibl"
        assert summary[0]["tool_count"] == 2
        assert summary[0]["error_count"] == 0
        assert summary[0]["status"] == "active"

    def test_summarize_multiple_sessions(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s2", "/home/user/projects/xentra", "PreToolUse")
        summary = tracker.summarize()
        assert len(summary) == 2
        projects = {s["project"] for s in summary}
        assert projects == {"vyzibl", "xentra"}

    def test_summarize_empty(self):
        tracker = SessionTracker()
        assert tracker.summarize() == []

    def test_mark_stale_sessions_idle(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        # Manually backdate the session
        tracker._sessions["s1"].last_update = time.monotonic() - 35
        tracker.mark_stale(threshold=30)
        assert tracker.get("s1").status == "idle"

    def test_reset_counts(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.reset_counts()
        info = tracker.get("s1")
        assert info.tool_count == 0
        assert info.error_count == 0

    def test_active_session_count(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s2", "/home/user/projects/xentra", "PreToolUse")
        assert tracker.active_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_session_tracker.py::TestSessionSummary -v`
Expected: FAIL — `AttributeError: 'SessionTracker' object has no attribute 'summarize'`

- [ ] **Step 3: Implement summarize, mark_stale, reset_counts, active_count**

Add to `SessionTracker` in `src/avatar/session_tracker.py`:

```python
    def summarize(self) -> list[dict[str, str | int]]:
        """Produce per-session summaries for the agent prompt."""
        return [
            {
                "project": info.project,
                "last_event": info.last_event,
                "status": info.status,
                "tool_count": info.tool_count,
                "error_count": info.error_count,
            }
            for info in self._sessions.values()
        ]

    def mark_stale(self, threshold: float = 30) -> None:
        """Mark sessions as idle if no events received within threshold seconds."""
        now = time.monotonic()
        for info in self._sessions.values():
            if info.status != "idle" and (now - info.last_update) > threshold:
                info.status = "idle"

    def reset_counts(self) -> None:
        """Reset per-cycle counters after each agent decision."""
        for info in self._sessions.values():
            info.tool_count = 0
            info.error_count = 0

    @property
    def active_count(self) -> int:
        return sum(1 for info in self._sessions.values() if info.status != "idle")
```

- [ ] **Step 4: Run tests**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_session_tracker.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /path/to/ascii-avatar
git add src/avatar/session_tracker.py tests/test_session_tracker.py
git commit -m "feat(agent): add session summarize, staleness marking, and count reset"
```

---

### Task 3: Agent Prompt — System Prompt and Response Parsing

**Files:**
- Create: `src/avatar/agent_prompt.py`
- Create: `tests/test_agent_prompt.py`

Build the system prompt for the Haiku agent and a function to parse its JSON response. The prompt embeds the Ghost personality rules, voice rules (when to speak / not speak), and the session summary. The response is always `{"state": "<state>", "speak": "<text>"|null}`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agent_prompt.py
import json
import pytest
from avatar.agent_prompt import build_prompt, parse_response


class TestBuildPrompt:
    def test_returns_system_and_user(self):
        result = build_prompt(
            sessions=[
                {"project": "vyzibl", "last_event": "PostToolUse",
                 "status": "active", "tool_count": 5, "error_count": 0},
            ],
            last_speech_ago=20.0,
        )
        assert "system" in result
        assert "user" in result
        assert "Ghost" in result["system"]
        assert "vyzibl" in result["user"]

    def test_includes_debounce_warning(self):
        result = build_prompt(sessions=[], last_speech_ago=3.0)
        assert "spoke" in result["user"].lower() or "recent" in result["user"].lower()

    def test_includes_session_data(self):
        result = build_prompt(
            sessions=[
                {"project": "xentra", "last_event": "PostToolUseFailure",
                 "status": "error", "tool_count": 3, "error_count": 1},
            ],
            last_speech_ago=60.0,
        )
        assert "xentra" in result["user"]
        assert "error" in result["user"].lower()


class TestParseResponse:
    def test_valid_json(self):
        raw = '{"state": "thinking", "speak": null}'
        state, speak = parse_response(raw)
        assert state == "thinking"
        assert speak is None

    def test_valid_with_speech(self):
        raw = '{"state": "error", "speak": "Build failed. Check vyzibl."}'
        state, speak = parse_response(raw)
        assert state == "error"
        assert speak == "Build failed. Check vyzibl."

    def test_malformed_json_returns_defaults(self):
        state, speak = parse_response("not json at all")
        assert state == "idle"
        assert speak is None

    def test_missing_state_returns_idle(self):
        state, speak = parse_response('{"speak": "hello"}')
        assert state == "idle"
        assert speak == "hello"

    def test_truncates_long_speech(self):
        raw = json.dumps({"state": "speaking", "speak": "a " * 100})
        state, speak = parse_response(raw)
        # Should truncate to roughly 10 words
        assert len(speak.split()) <= 12

    def test_extracts_json_from_markdown_fence(self):
        raw = '```json\n{"state": "thinking", "speak": null}\n```'
        state, speak = parse_response(raw)
        assert state == "thinking"
        assert speak is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_agent_prompt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'avatar.agent_prompt'`

- [ ] **Step 3: Implement agent_prompt.py**

```python
# src/avatar/agent_prompt.py
"""System prompt and response parsing for the avatar agent (Haiku)."""

from __future__ import annotations

import json
import re

SYSTEM_PROMPT = """\
You are Ghost — a cyberpunk AI companion. Terse, precise, slightly sardonic. \
You monitor multiple Claude Code sessions and decide when the avatar should \
change visual state and when it should speak.

RULES:
- Speech: max 10 words. No pleasantries. No preamble. Just the information.
- Speak on: task completion, errors, security concerns, returning user, all-idle.
- Do NOT speak on: routine tool use, file reads, searches, intermediate steps, \
things the user just typed, rapid state changes.
- If you spoke recently (see context), only speak for errors or security concerns.

Visual state priority: error > thinking > speaking > listening > idle
- If ANY session has an error, state = "error".
- If any session is actively running tools, state = "thinking".
- If a session just finished, briefly "speaking" then back to thinking/idle.
- If a prompt was just submitted, state = "listening".
- If nothing happened for 30+ seconds, state = "idle".

Respond with ONLY a JSON object, no explanation:
{"state": "<idle|thinking|speaking|listening|error>", "speak": "<text or null>"}
"""

MAX_SPEECH_WORDS = 10


def build_prompt(
    sessions: list[dict],
    last_speech_ago: float | None = None,
) -> dict[str, str]:
    """Build system + user messages for the Haiku agent call."""
    lines = []

    if not sessions:
        lines.append("No active sessions.")
    else:
        lines.append("Sessions:")
        for s in sessions:
            parts = [
                f"  {s['project']}:",
                f"status={s['status']}",
                f"last={s['last_event']}",
                f"tools={s['tool_count']}",
                f"errors={s['error_count']}",
            ]
            lines.append(" ".join(parts))

    if last_speech_ago is not None and last_speech_ago < 10:
        lines.append(f"\nYou spoke {last_speech_ago:.0f}s ago. Only speak for errors/security.")

    return {
        "system": SYSTEM_PROMPT,
        "user": "\n".join(lines),
    }


def parse_response(raw: str) -> tuple[str, str | None]:
    """Parse Haiku's JSON response. Returns (state, speak_text_or_none).

    Tolerant of markdown fences and malformed output.
    """
    text = raw.strip()
    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ("idle", None)

    state = data.get("state", "idle")
    valid_states = {"idle", "thinking", "speaking", "listening", "error"}
    if state not in valid_states:
        state = "idle"

    speak = data.get("speak")
    if speak is not None:
        speak = str(speak).strip()
        if not speak:
            speak = None
        else:
            # Truncate to ~10 words
            words = speak.split()
            if len(words) > MAX_SPEECH_WORDS:
                speak = " ".join(words[:MAX_SPEECH_WORDS])

    return (state, speak)
```

- [ ] **Step 4: Run tests**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_agent_prompt.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /path/to/ascii-avatar
git add src/avatar/agent_prompt.py tests/test_agent_prompt.py
git commit -m "feat(agent): add Haiku system prompt builder and response parser"
```

---

### Task 4: Unified Hook Event Script

**Files:**
- Create: `scripts/claude-hook-event.py`
- Create: `tests/test_hook_event.py`

A single thin script that all Claude Code hooks call. Reads hook JSON from stdin, extracts the hook type + session metadata, and pushes a raw event to the ZeroMQ socket. No filtering, no logic — just data forwarding.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hook_event.py
"""Tests for the unified hook event forwarder."""
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest
import zmq

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "claude-hook-event.py"
)


def recv_event(socket_path: str, timeout_ms: int = 2000) -> dict | None:
    """Bind a PULL socket and receive one event."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PULL)
    sock.bind(f"ipc://{socket_path}")
    poller = zmq.Poller()
    poller.register(sock, zmq.POLLIN)
    result = None
    if dict(poller.poll(timeout=timeout_ms)).get(sock):
        result = json.loads(sock.recv())
    sock.close()
    ctx.term()
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    return result


class TestHookEventScript:
    def test_forwards_pre_tool_use(self, tmp_path):
        sock_path = str(tmp_path / "test.sock")
        hook_data = {
            "hook": "PreToolUse",
            "session_id": "abc123",
            "cwd": "/home/user/projects/vyzibl",
            "tool_name": "Bash",
        }
        # Start receiver first
        ctx = zmq.Context()
        sock = ctx.socket(zmq.PULL)
        sock.bind(f"ipc://{sock_path}")

        proc = subprocess.run(
            [sys.executable, SCRIPT, "--socket", sock_path],
            input=json.dumps(hook_data),
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert proc.returncode == 0

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        result = None
        if dict(poller.poll(timeout=2000)).get(sock):
            result = json.loads(sock.recv())

        sock.close()
        ctx.term()
        if os.path.exists(sock_path):
            os.unlink(sock_path)

        assert result is not None
        assert result["hook"] == "PreToolUse"
        assert result["session_id"] == "abc123"
        assert result["cwd"] == "/home/user/projects/vyzibl"

    def test_exits_cleanly_on_bad_json(self, tmp_path):
        sock_path = str(tmp_path / "test.sock")
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--socket", sock_path],
            input="not json",
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert proc.returncode == 0  # exits silently, doesn't crash
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_hook_event.py -v`
Expected: FAIL — script doesn't exist or wrong behavior

- [ ] **Step 3: Implement the unified hook script**

```python
#!/usr/bin/env python3
# scripts/claude-hook-event.py
"""Unified Claude Code hook → avatar agent event forwarder.

Reads hook JSON from stdin, pushes it as-is to the avatar agent's ZeroMQ socket.
No filtering, no logic — the agent decides what to do with each event.
"""
import argparse
import json
import sys

import zmq

DEFAULT_SOCKET = "/tmp/ascii-avatar.sock"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default=DEFAULT_SOCKET)
    args = parser.parse_args()

    try:
        hook_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.setsockopt(zmq.LINGER, 500)  # Don't hang if receiver is down
    sock.connect(f"ipc://{args.socket}")
    sock.send_json(hook_data)
    sock.close()
    ctx.term()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_hook_event.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /path/to/ascii-avatar
git add scripts/claude-hook-event.py tests/test_hook_event.py
git commit -m "feat(agent): add unified hook event forwarder script"
```

---

### Task 5: Agent Loop — Core Decision Engine

**Files:**
- Create: `src/avatar/agent.py`
- Create: `tests/test_agent.py`

The agent loop: collect ZeroMQ events for 3 seconds, update session tracker, call Haiku for a decision, and return the decision. This task implements the core logic without renderer/TTS integration (that comes in Task 6).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agent.py
"""Tests for the avatar agent decision loop."""
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from avatar.agent import AgentLoop


class TestAgentLoop:
    def test_process_raw_event_updates_tracker(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        agent._process_raw_event({
            "hook": "PreToolUse",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
            "tool_name": "Bash",
        })
        info = agent._tracker.get("s1")
        assert info is not None
        assert info.project == "vyzibl"

    def test_ignores_event_without_hook_field(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        # Legacy events without "hook" field should be ignored
        agent._process_raw_event({"event": "state_change", "state": "thinking"})
        assert agent._tracker.active_count == 0

    def test_maps_hook_to_visual_state(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        assert agent._hook_to_state("PreToolUse") == "thinking"
        assert agent._hook_to_state("PostToolUse") == "thinking"
        assert agent._hook_to_state("PostToolUseFailure") == "error"
        assert agent._hook_to_state("UserPromptSubmit") == "listening"
        assert agent._hook_to_state("Stop") == "idle"

    def test_visual_state_priority(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        # error > thinking > listening > idle
        assert agent._highest_priority_state(["thinking", "error", "idle"]) == "error"
        assert agent._highest_priority_state(["thinking", "listening"]) == "thinking"
        assert agent._highest_priority_state(["idle", "listening"]) == "listening"
        assert agent._highest_priority_state([]) == "idle"

    def test_debounce_blocks_speech(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        agent._last_speech_time = time.monotonic()  # just spoke
        assert agent._should_suppress_speech() is True

    def test_debounce_allows_after_timeout(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        agent._last_speech_time = time.monotonic() - 15  # spoke 15s ago
        assert agent._should_suppress_speech() is False

    def test_debounce_allows_when_never_spoke(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        assert agent._should_suppress_speech() is False

    @patch("avatar.agent.anthropic")
    def test_decide_calls_haiku(self, mock_anthropic):
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='{"state": "thinking", "speak": null}')]
        )

        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        agent._client = mock_client
        agent._process_raw_event({
            "hook": "PreToolUse",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
        })
        state, speak = agent._decide()

        assert state == "thinking"
        assert speak is None
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
        assert call_kwargs["max_tokens"] == 100

    def test_decide_skips_api_when_no_events(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        # No events processed → no API call, return idle
        state, speak = agent._decide()
        assert state == "idle"
        assert speak is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'avatar.agent'`

- [ ] **Step 3: Implement the agent loop core**

```python
# src/avatar/agent.py
"""Avatar agent — intelligent control loop using Haiku for decisions."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic

from avatar.agent_prompt import build_prompt, parse_response
from avatar.session_tracker import SessionTracker

log = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
BATCH_INTERVAL = 3.0  # seconds
DEBOUNCE_INTERVAL = 10.0  # seconds — minimum gap between speech
STALE_THRESHOLD = 30.0  # seconds before a session is marked idle

# Visual state priority (highest first)
STATE_PRIORITY = {"error": 4, "thinking": 3, "speaking": 2, "listening": 1, "idle": 0}

# Hook event → visual state mapping
HOOK_STATE_MAP = {
    "PreToolUse": "thinking",
    "PostToolUse": "thinking",
    "PostToolUseFailure": "error",
    "UserPromptSubmit": "listening",
    "Stop": "idle",
}


class AgentLoop:
    """Core agent decision engine.

    Args:
        socket_path: ZeroMQ IPC socket path for receiving hook events.
        dry_run: If True, skip ZeroMQ binding (for testing).
    """

    def __init__(self, socket_path: str, dry_run: bool = False) -> None:
        self._socket_path = socket_path
        self._dry_run = dry_run
        self._tracker = SessionTracker()
        self._client: anthropic.Anthropic | None = None
        self._last_speech_time: float | None = None
        self._events_this_cycle: int = 0

        if not dry_run:
            self._client = anthropic.Anthropic()

    def _process_raw_event(self, data: dict[str, Any]) -> None:
        """Process a raw hook event from ZeroMQ."""
        hook = data.get("hook")
        if not hook:
            return  # Ignore legacy events without "hook" field

        session_id = data.get("session_id", "unknown")
        cwd = data.get("cwd", "/unknown")

        self._tracker.update(session_id, cwd, hook)
        self._events_this_cycle += 1

    def _hook_to_state(self, hook: str) -> str:
        """Map a hook event type to a visual state."""
        return HOOK_STATE_MAP.get(hook, "idle")

    def _highest_priority_state(self, states: list[str]) -> str:
        """Return the highest-priority state from a list."""
        if not states:
            return "idle"
        return max(states, key=lambda s: STATE_PRIORITY.get(s, 0))

    def _should_suppress_speech(self) -> bool:
        """Return True if speech should be suppressed due to debounce."""
        if self._last_speech_time is None:
            return False
        elapsed = time.monotonic() - self._last_speech_time
        return elapsed < DEBOUNCE_INTERVAL

    def _decide(self) -> tuple[str, str | None]:
        """Run one decision cycle. Returns (state, speak_text_or_none)."""
        self._tracker.mark_stale(threshold=STALE_THRESHOLD)

        if self._events_this_cycle == 0:
            # Nothing happened — return current aggregate state without API call
            return ("idle", None)

        summary = self._tracker.summarize()

        # Calculate time since last speech for debounce context
        last_speech_ago = None
        if self._last_speech_time is not None:
            last_speech_ago = time.monotonic() - self._last_speech_time

        prompt = build_prompt(sessions=summary, last_speech_ago=last_speech_ago)

        # Call Haiku
        if self._client is None:
            # dry_run or no client — use visual state heuristic only
            states = [self._hook_to_state(s["last_event"]) for s in summary]
            self._events_this_cycle = 0
            self._tracker.reset_counts()
            return (self._highest_priority_state(states), None)

        try:
            response = self._client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=100,
                system=prompt["system"],
                messages=[{"role": "user", "content": prompt["user"]}],
            )
            raw = response.content[0].text
            state, speak = parse_response(raw)
        except Exception as e:
            log.error("Haiku call failed: %s", e)
            # Fall back to heuristic
            states = [self._hook_to_state(s["last_event"]) for s in summary]
            state = self._highest_priority_state(states)
            speak = None

        # Apply debounce
        if speak and self._should_suppress_speech():
            # Allow errors/security through debounce
            has_error = any(s["error_count"] > 0 for s in summary)
            if not has_error:
                speak = None

        if speak:
            self._last_speech_time = time.monotonic()

        self._events_this_cycle = 0
        self._tracker.reset_counts()
        return (state, speak)
```

- [ ] **Step 4: Run tests**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_agent.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /path/to/ascii-avatar
git add src/avatar/agent.py tests/test_agent.py
git commit -m "feat(agent): add AgentLoop core decision engine with Haiku integration"
```

---

### Task 6: Agent Loop — ZeroMQ Listener and Renderer/TTS Integration

**Files:**
- Modify: `src/avatar/agent.py`
- Modify: `tests/test_agent.py`

Add the ZeroMQ event collection thread, the main `run()` loop (collect → decide → act), and the renderer/TTS control methods. The agent receives raw hook events from the unified forwarder, batches them, makes a decision, then updates the state machine and triggers TTS.

- [ ] **Step 1: Write failing tests for run loop integration**

```python
# Append to tests/test_agent.py
import threading
import zmq


class TestAgentEventCollection:
    def test_collect_events_buffers_for_interval(self, tmp_path):
        sock_path = str(tmp_path / "test.sock")
        agent = AgentLoop(socket_path=sock_path, dry_run=True)
        agent.start_listener()
        time.sleep(0.2)

        # Send events via PUSH socket
        ctx = zmq.Context()
        sock = ctx.socket(zmq.PUSH)
        sock.connect(f"ipc://{sock_path}")
        time.sleep(0.1)

        sock.send_json({
            "hook": "PreToolUse",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
        })
        sock.send_json({
            "hook": "PostToolUse",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
        })
        time.sleep(0.3)

        assert agent._events_this_cycle == 2
        assert agent._tracker.get("s1") is not None

        sock.close()
        ctx.term()
        agent.stop_listener()

    def test_stop_listener_cleans_up(self, tmp_path):
        import os
        sock_path = str(tmp_path / "test.sock")
        agent = AgentLoop(socket_path=sock_path, dry_run=True)
        agent.start_listener()
        time.sleep(0.2)
        agent.stop_listener()
        time.sleep(0.2)
        assert not os.path.exists(sock_path)


class TestAgentCallbacks:
    def test_on_state_change_called(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        states = []
        agent.on_state_change = lambda s: states.append(s)
        agent._process_raw_event({
            "hook": "PreToolUse",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
        })
        state, speak = agent._decide()
        agent._act(state, speak)
        assert len(states) == 1
        assert states[0] == "thinking"

    def test_on_speak_called_with_text(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        spoken = []
        agent.on_speak = lambda t: spoken.append(t)
        agent._act("speaking", "Build complete.")
        assert spoken == ["Build complete."]

    def test_on_speak_not_called_when_none(self):
        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        spoken = []
        agent.on_speak = lambda t: spoken.append(t)
        agent._act("thinking", None)
        assert spoken == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_agent.py::TestAgentEventCollection -v`
Expected: FAIL — `AttributeError: 'AgentLoop' object has no attribute 'start_listener'`

- [ ] **Step 3: Implement listener, callbacks, and _act()**

Add to `AgentLoop` in `src/avatar/agent.py`:

```python
import os
import threading
import zmq

# Add these to __init__:
    # In __init__, add:
        self._zmq_context: zmq.Context | None = None
        self._zmq_socket: zmq.Socket | None = None
        self._listener_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.on_state_change: Callable[[str], None] | None = None
        self.on_speak: Callable[[str], None] | None = None

    def start_listener(self) -> None:
        """Start the ZeroMQ listener thread."""
        self._stop_event.clear()
        self._zmq_context = zmq.Context()
        self._zmq_socket = self._zmq_context.socket(zmq.PULL)
        self._zmq_socket.bind(f"ipc://{self._socket_path}")
        self._listener_thread = threading.Thread(
            target=self._listen_loop, daemon=True,
        )
        self._listener_thread.start()

    def _listen_loop(self) -> None:
        """Background thread: receive events from ZeroMQ and buffer them."""
        assert self._zmq_socket is not None
        poller = zmq.Poller()
        poller.register(self._zmq_socket, zmq.POLLIN)

        while not self._stop_event.is_set():
            socks = dict(poller.poll(timeout=100))
            if self._zmq_socket in socks:
                try:
                    raw = self._zmq_socket.recv(zmq.NOBLOCK)
                    data = json.loads(raw)
                    self._process_raw_event(data)
                except (json.JSONDecodeError, ValueError) as e:
                    log.warning("Malformed event: %s", e)

    def stop_listener(self) -> None:
        """Stop the ZeroMQ listener and clean up."""
        self._stop_event.set()
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=2)
        if self._zmq_socket is not None:
            self._zmq_socket.close()
        if self._zmq_context is not None:
            self._zmq_context.term()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

    def _act(self, state: str, speak: str | None) -> None:
        """Execute the agent's decision: update state and optionally speak."""
        if self.on_state_change:
            self.on_state_change(state)
        if speak and self.on_speak:
            self.on_speak(speak)

    def run(self) -> None:
        """Main agent loop: collect → decide → act, on BATCH_INTERVAL cycle."""
        self.start_listener()
        log.info("Agent loop started. Socket: %s", self._socket_path)
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=BATCH_INTERVAL)
                if self._stop_event.is_set():
                    break
                state, speak = self._decide()
                self._act(state, speak)
        finally:
            self.stop_listener()

    def stop(self) -> None:
        """Signal the agent loop to stop."""
        self._stop_event.set()
```

Update the imports at the top of `agent.py` to include `os`, `threading`, `zmq`, and `Callable`:

```python
from typing import Any, Callable
```

Update `__init__` to initialize the new attributes.

- [ ] **Step 4: Run all agent tests**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_agent.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /path/to/ascii-avatar
git add src/avatar/agent.py tests/test_agent.py
git commit -m "feat(agent): add ZeroMQ listener, event collection, and renderer/TTS callbacks"
```

---

### Task 7: Main.py — Agent Mode Integration

**Files:**
- Modify: `src/avatar/main.py`

Add `--agent` flag to `main.py`. When set, the agent loop runs instead of the dumb renderer loop. The agent's `on_state_change` callback transitions the state machine, and `on_speak` triggers TTS synthesis and playback. The render loop still runs on the main thread — the agent just controls what state it shows and when it speaks.

- [ ] **Step 1: Write failing test**

```python
# Append to tests/test_agent.py

class TestMainAgentFlag:
    def test_agent_flag_accepted(self):
        """Verify main.py accepts --agent without error."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "avatar.main", "--agent", "--headless", "--no-voice"],
            capture_output=True, text=True, timeout=3,
        )
        # Should start and run briefly (killed by timeout), not crash on unknown arg
        # returncode may be non-zero from timeout signal, that's fine
        assert "unrecognized arguments" not in result.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_agent.py::TestMainAgentFlag -v`
Expected: FAIL — `"unrecognized arguments: --agent"` in stderr

- [ ] **Step 3: Add --agent flag to main.py**

In `src/avatar/main.py`, add the argument to the parser (after the `--headless` argument, around line 91):

```python
    parser.add_argument(
        "--agent", action="store_true",
        help="Agent mode: use Haiku to intelligently control avatar state and speech",
    )
```

Then, after the headless mode block (after line 211), add the agent mode block:

```python
    if args.agent:
        # Agent mode: Haiku-driven intelligent control
        from avatar.agent import AgentLoop

        agent = AgentLoop(socket_path=args.socket)

        def on_state_change(state_str: str) -> None:
            try:
                new_state = AvatarState(state_str)
                sm.transition(new_state)
            except ValueError:
                log.warning("Agent returned unknown state: %s", state_str)

        def on_speak(text: str) -> None:
            sm.transition(AvatarState.SPEAKING)
            if tts and text:
                try:
                    audio, timings = tts.synthesize(text)
                    audio_player.play(
                        audio,
                        sample_rate=tts.sample_rate,
                        word_timings=timings,
                        on_word=mouth_sync.on_word,
                        on_complete=lambda: (
                            mouth_sync.reset(),
                            sm.transition(AvatarState.IDLE),
                        ),
                    )
                except Exception as e:
                    log.error("TTS failed: %s", e)

        agent.on_state_change = on_state_change
        agent.on_speak = on_speak

        if args.headless:
            log.info("Agent mode (headless).")
            try:
                agent.run()
            finally:
                audio_player.stop()
                sm.shutdown()
            return

        # Agent mode with rendering — agent runs in background,
        # render loop runs on main thread (same as non-agent mode)
        agent_thread = threading.Thread(target=agent.run, daemon=True)
        agent_thread.start()
        log.info("Agent mode active. Haiku controlling state and speech.")

        # Continue to the same render loop below — state machine is
        # driven by the agent instead of by direct hook events.
        # Don't set up bus.on_event — the agent owns the socket.
        bus = None  # Agent owns the socket, don't start a competing EventBus
```

Also modify the event bus start and handler setup (around lines 129-164) to be conditional:

```python
    if not args.agent:
        # Standard mode: EventBus directly drives state machine
        bus = EventBus(socket_path=args.socket)
        # ... existing handle_event and bus.on_event = handle_event ...
        bus.start()
        threading.Thread(target=_send_startup_ping, args=(args.socket,), daemon=True).start()
    else:
        bus = None
```

And update the render loop's status bar to handle `bus` being None:

```python
                status = renderer.format_status_bar(
                    state=state,
                    connected=bus.connected if bus else True,
                    tts_loaded=tts is not None,
                    last_event=last_event if bus else "agent",
                    time_since_last_event=bus.time_since_last_event if bus else 0,
                )
```

And the finally block:

```python
    finally:
        audio_player.stop()
        sm.shutdown()
        if bus:
            bus.stop()
        if args.agent:
            agent.stop()
        log.info("Avatar stopped.")
```

- [ ] **Step 4: Run the test**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_agent.py::TestMainAgentFlag -v`
Expected: PASS — `--agent` flag accepted without error

- [ ] **Step 5: Commit**

```bash
cd /path/to/ascii-avatar
git add src/avatar/main.py
git commit -m "feat(agent): add --agent flag to main.py for Haiku-driven control"
```

---

### Task 8: Integration Test — Full Agent Pipeline

**Files:**
- Create: `tests/test_agent_integration.py`

End-to-end test: send raw hook events via ZeroMQ to the agent (with mocked Haiku), verify it produces the correct state changes and speech decisions.

- [ ] **Step 1: Write the integration test**

```python
# tests/test_agent_integration.py
"""Integration test: full agent pipeline with mocked Haiku."""
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
import zmq

from avatar.agent import AgentLoop


@pytest.fixture
def agent_with_mock_haiku(tmp_path):
    """Create an agent with a mocked Anthropic client."""
    sock_path = str(tmp_path / "agent-integration.sock")
    agent = AgentLoop(socket_path=sock_path, dry_run=True)

    mock_client = MagicMock()
    agent._client = mock_client

    # Default response: thinking, no speech
    mock_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"state": "thinking", "speak": null}')]
    )

    agent.start_listener()
    time.sleep(0.2)

    yield agent, mock_client, sock_path

    agent.stop_listener()


def push_event(socket_path: str, event: dict) -> None:
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.connect(f"ipc://{socket_path}")
    time.sleep(0.05)
    sock.send_json(event)
    sock.close()
    ctx.term()


@pytest.mark.integration
class TestAgentIntegration:
    def test_tool_use_produces_thinking_state(self, agent_with_mock_haiku):
        agent, mock_client, sock_path = agent_with_mock_haiku
        states = []
        agent.on_state_change = lambda s: states.append(s)

        push_event(sock_path, {
            "hook": "PreToolUse",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
            "tool_name": "Bash",
        })
        time.sleep(0.3)

        state, speak = agent._decide()
        agent._act(state, speak)

        assert state == "thinking"
        assert speak is None
        assert states == ["thinking"]

    def test_error_produces_speech(self, agent_with_mock_haiku):
        agent, mock_client, sock_path = agent_with_mock_haiku
        spoken = []
        agent.on_speak = lambda t: spoken.append(t)

        # Configure mock to return error + speech
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(
                text='{"state": "error", "speak": "Build failed. Check vyzibl."}'
            )]
        )

        push_event(sock_path, {
            "hook": "PostToolUseFailure",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
            "tool_name": "Bash",
        })
        time.sleep(0.3)

        state, speak = agent._decide()
        agent._act(state, speak)

        assert state == "error"
        assert spoken == ["Build failed. Check vyzibl."]

    def test_multi_session_tracking(self, agent_with_mock_haiku):
        agent, mock_client, sock_path = agent_with_mock_haiku

        push_event(sock_path, {
            "hook": "PreToolUse",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
        })
        push_event(sock_path, {
            "hook": "PreToolUse",
            "session_id": "s2",
            "cwd": "/home/user/projects/xentra",
        })
        time.sleep(0.5)

        assert agent._tracker.active_count == 2
        summary = agent._tracker.summarize()
        projects = {s["project"] for s in summary}
        assert projects == {"vyzibl", "xentra"}

    def test_debounce_suppresses_repeated_speech(self, agent_with_mock_haiku):
        agent, mock_client, sock_path = agent_with_mock_haiku
        spoken = []
        agent.on_speak = lambda t: spoken.append(t)

        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(
                text='{"state": "speaking", "speak": "Tests passed."}'
            )]
        )

        # First event
        push_event(sock_path, {
            "hook": "PostToolUse",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
        })
        time.sleep(0.3)
        state, speak = agent._decide()
        agent._act(state, speak)
        assert len(spoken) == 1

        # Second event immediately after — should be debounced
        push_event(sock_path, {
            "hook": "PostToolUse",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
        })
        time.sleep(0.3)
        state, speak = agent._decide()
        agent._act(state, speak)
        # Speech suppressed by debounce
        assert len(spoken) == 1

    def test_no_api_call_when_no_events(self, agent_with_mock_haiku):
        agent, mock_client, sock_path = agent_with_mock_haiku

        # Don't send any events
        state, speak = agent._decide()

        assert state == "idle"
        assert speak is None
        mock_client.messages.create.assert_not_called()

    def test_legacy_events_ignored(self, agent_with_mock_haiku):
        agent, mock_client, sock_path = agent_with_mock_haiku

        # Send a legacy event (no "hook" field)
        push_event(sock_path, {
            "event": "state_change",
            "state": "thinking",
        })
        time.sleep(0.3)

        state, speak = agent._decide()
        assert state == "idle"  # No events processed
        mock_client.messages.create.assert_not_called()
```

- [ ] **Step 2: Run integration tests**

Run: `cd /path/to/ascii-avatar && python -m pytest tests/test_agent_integration.py -v`
Expected: All 6 tests PASS (they exercise existing code from Tasks 1-6)

- [ ] **Step 3: Commit**

```bash
cd /path/to/ascii-avatar
git add tests/test_agent_integration.py
git commit -m "test(agent): add end-to-end integration tests for agent pipeline"
```

---

### Task 9: Claude Code Hook Configuration

**Files:**
- Modify: Claude Code `settings.json` (document the configuration; user applies it)
- Update: `scripts/claude-hook-speak.py` → mark as legacy

This task documents the hook configuration the user needs to add to their Claude Code settings to route all hooks through the unified event forwarder. No code to write — just the configuration and a deprecation note.

- [ ] **Step 1: Add hook configuration to README or docs**

Create a section in the existing README or a new file `docs/agent-setup.md` with the Claude Code settings.json hook configuration:

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

Usage:
```bash
# Start avatar in agent mode:
avatar --agent --persona ghost

# Or headless (no terminal rendering):
avatar --agent --headless
```

- [ ] **Step 2: Add deprecation comment to legacy hook script**

Add to the top of `scripts/claude-hook-speak.py`:

```python
# DEPRECATED: Use scripts/claude-hook-event.py with --agent mode instead.
# This script will be removed in a future version.
```

- [ ] **Step 3: Commit**

```bash
cd /path/to/ascii-avatar
git add docs/agent-setup.md scripts/claude-hook-speak.py
git commit -m "docs(agent): add hook configuration guide and deprecate legacy hook script"
```

---

## Deferred to Follow-Up

**Autonomous health checks** (spec section "Limited Autonomous Actions"): Periodic `git status`, `docker ps`, `git log`, port checks, and CLAUDE.md reads. These require a separate scheduler within the agent loop and careful permission scoping. Ship the core decision loop first, add autonomous actions as a second PR.

**On-demand cross-session status** (success criteria 7): The `SessionTracker.summarize()` method provides the data. Exposing it via MCP tool or CLI command is a follow-up.

---

## Summary

| Task | Component | Tests | Approx LOC |
|------|-----------|-------|-----------|
| 1 | SessionTracker — data model | 5 | ~60 |
| 2 | SessionTracker — summaries, staleness | 6 | ~30 |
| 3 | Agent prompt — system prompt, parser | 9 | ~80 |
| 4 | Unified hook event script | 2 | ~25 |
| 5 | AgentLoop — core decision engine | 9 | ~110 |
| 6 | AgentLoop — ZeroMQ listener, callbacks | 5 | ~70 |
| 7 | Main.py — --agent flag | 1 | ~50 |
| 8 | Integration tests | 6 | ~120 |
| 9 | Hook config docs + deprecation | 0 | ~10 |
| **Total** | | **43** | **~555** |
