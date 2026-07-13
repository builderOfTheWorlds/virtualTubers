"""Claude Code bridge — sends events to the avatar process via PUSH socket."""

from __future__ import annotations

import zmq

from avatar.event_bus import DEFAULT_SOCKET_PATH


class ClaudeCodeBridge:
    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH) -> None:
        self._socket_path = socket_path
        self._context: zmq.Context | None = None
        self._socket: zmq.Socket | None = None

    def connect(self) -> None:
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.PUSH)
        self._socket.connect(f"ipc://{self._socket_path}")

    def disconnect(self) -> None:
        if self._socket:
            self._socket.close()
        if self._context:
            self._context.term()
        self._socket = None
        self._context = None

    def __enter__(self) -> ClaudeCodeBridge:
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    def _send(self, event: dict) -> None:
        assert self._socket is not None, "Not connected"
        self._socket.send_json(event)

    def send_thinking(self) -> None:
        self._send({"event": "state_change", "state": "thinking"})

    def send_speaking(self, text: str) -> None:
        self._send({"event": "speak_start", "state": "speaking", "text": text})

    def send_listening(self) -> None:
        self._send({"event": "state_change", "state": "listening"})

    def send_idle(self) -> None:
        self._send({"event": "state_change", "state": "idle"})

    def send_error(self, message: str) -> None:
        self._send({
            "event": "state_change",
            "state": "error",
            "data": {"message": message},
        })
