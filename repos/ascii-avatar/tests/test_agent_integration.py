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
