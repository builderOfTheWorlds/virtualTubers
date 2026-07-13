"""Tests for the avatar agent decision loop."""
import os
import threading
import time
from unittest.mock import MagicMock

import pytest
import zmq

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

    def test_decide_calls_haiku(self):
        mock_client = MagicMock()
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

    def test_debounce_allows_errors_through(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='{"state": "error", "speak": "Build failed."}')]
        )

        agent = AgentLoop(socket_path="/dev/null", dry_run=True)
        agent._client = mock_client
        agent._last_speech_time = time.monotonic()  # just spoke

        agent._process_raw_event({
            "hook": "PostToolUseFailure",
            "session_id": "s1",
            "cwd": "/home/user/projects/vyzibl",
        })
        state, speak = agent._decide()

        # Error events bypass debounce
        assert state == "error"
        assert speak == "Build failed."


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
