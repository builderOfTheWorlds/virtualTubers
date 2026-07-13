"""Tests for the unified hook event forwarder."""
import json
import os
import subprocess
import sys
import time

import pytest
import zmq

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "claude-hook-event.py"
)


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
