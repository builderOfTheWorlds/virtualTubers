import json
import os
import threading
import time

import pytest
import zmq

from avatar.bridge.claude_code import ClaudeCodeBridge
from avatar.bridge.hooks import think, respond, listen, idle, error


@pytest.fixture
def socket_path(tmp_path):
    return str(tmp_path / "test-bridge.sock")


@pytest.fixture
def pull_receiver(socket_path):
    """Set up a PULL socket to receive events from the bridge."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PULL)
    sock.bind(f"ipc://{socket_path}")
    yield sock
    sock.close()
    ctx.term()


class TestClaudeCodeBridge:
    def test_send_thinking(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_thinking()
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["event"] == "state_change"
        assert msg["state"] == "thinking"
        bridge.disconnect()

    def test_send_speaking(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_speaking("hello world")
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["event"] == "speak_start"
        assert msg["text"] == "hello world"
        bridge.disconnect()

    def test_send_listening(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_listening()
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["state"] == "listening"
        bridge.disconnect()

    def test_send_idle(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_idle()
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["state"] == "idle"
        bridge.disconnect()

    def test_send_error(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_error("something broke")
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["state"] == "error"
        assert msg["data"]["message"] == "something broke"
        bridge.disconnect()

    def test_context_manager(self, socket_path, pull_receiver):
        with ClaudeCodeBridge(socket_path=socket_path) as bridge:
            bridge.send_thinking()
        time.sleep(0.1)
        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["state"] == "thinking"
