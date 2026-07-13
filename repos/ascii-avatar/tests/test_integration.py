"""Integration test — spawns avatar process, sends events, verifies lifecycle."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest
import zmq

# Mark all tests in this module as integration
pytestmark = pytest.mark.integration


@pytest.fixture
def avatar_process(tmp_path):
    """Start the avatar process in headless mode (no terminal rendering)."""
    socket_path = str(tmp_path / "test-avatar.sock")
    env = {
        **os.environ,
        "PYTHONPATH": str(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
        ),
        "TERM": "dumb",
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "avatar.main",
            "--socket",
            socket_path,
            "--no-voice",
            "--headless",
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Give the process time to bind the socket
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if os.path.exists(socket_path):
            break
        if proc.poll() is not None:
            stdout = proc.stdout.read().decode(errors="replace")
            stderr = proc.stderr.read().decode(errors="replace")
            pytest.fail(
                f"Avatar process exited early (rc={proc.returncode}).\n"
                f"stdout: {stdout}\nstderr: {stderr}"
            )
        time.sleep(0.1)
    else:
        # Socket never appeared — may still work if process bound quickly
        pass

    yield proc, socket_path

    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def send_event(socket_path: str, event: dict) -> None:
    """Send a single ZeroMQ PUSH event to the avatar process."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.setsockopt(zmq.SNDTIMEO, 2000)
    sock.connect(f"ipc://{socket_path}")
    time.sleep(0.05)  # allow connection to establish
    sock.send_json(event)
    sock.close()
    ctx.term()


class TestIntegration:
    @pytest.mark.timeout(30)
    def test_process_starts(self, avatar_process):
        """Avatar process should start and stay alive."""
        proc, _socket_path = avatar_process
        assert proc.poll() is None, "Process died on startup"

    @pytest.mark.timeout(30)
    def test_full_lifecycle(self, avatar_process):
        """Walk through idle -> thinking -> speaking -> listening -> idle transitions."""
        proc, socket_path = avatar_process
        assert proc.poll() is None, "Process died on startup"

        # idle (already the initial state)
        time.sleep(0.5)
        assert proc.poll() is None, "Died during idle"

        # thinking
        send_event(socket_path, {"event": "state_change", "state": "thinking"})
        time.sleep(0.5)
        assert proc.poll() is None, "Died during thinking transition"

        # speaking (animation only — TTS disabled)
        send_event(
            socket_path,
            {
                "event": "speak_start",
                "state": "speaking",
                "text": "Hello, I am your AI assistant.",
            },
        )
        time.sleep(0.5)
        assert proc.poll() is None, "Died during speaking transition"

        # listening
        send_event(socket_path, {"event": "state_change", "state": "listening"})
        time.sleep(0.5)
        assert proc.poll() is None, "Died during listening transition"

        # back to idle
        send_event(socket_path, {"event": "state_change", "state": "idle"})
        time.sleep(0.3)
        assert proc.poll() is None, "Died returning to idle"

    @pytest.mark.timeout(30)
    def test_unknown_state_does_not_crash(self, avatar_process):
        """Sending an unknown state should log a warning but not crash."""
        proc, socket_path = avatar_process
        assert proc.poll() is None

        send_event(socket_path, {"event": "state_change", "state": "nonexistent_state"})
        time.sleep(0.3)
        assert proc.poll() is None, "Process crashed on unknown state"

    @pytest.mark.timeout(30)
    def test_malformed_event_does_not_crash(self, avatar_process):
        """Sending a malformed JSON payload should not crash the process."""
        proc, socket_path = avatar_process
        assert proc.poll() is None

        # Send raw bytes that are not valid JSON
        ctx = zmq.Context()
        sock = ctx.socket(zmq.PUSH)
        sock.setsockopt(zmq.SNDTIMEO, 2000)
        sock.connect(f"ipc://{socket_path}")
        time.sleep(0.05)
        sock.send(b"not-valid-json{{{")
        sock.close()
        ctx.term()

        time.sleep(0.3)
        assert proc.poll() is None, "Process crashed on malformed event"

    @pytest.mark.timeout(15)
    def test_clean_shutdown_on_sigterm(self, avatar_process):
        """SIGTERM should cause a clean exit (return code not None)."""
        proc, _socket_path = avatar_process
        assert proc.poll() is None

        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("Process did not exit within 8 seconds of SIGTERM")

        assert proc.returncode is not None, "Process has no return code after SIGTERM"
