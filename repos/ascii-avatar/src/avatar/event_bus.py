"""ZeroMQ PUSH/PULL event bus for avatar IPC."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import zmq

from avatar.bridge.paths import get_socket_path

log = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH = get_socket_path()


@dataclass
class AvatarEvent:
    event: str
    state: str = ""
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AvatarEvent:
        if "event" not in d:
            raise ValueError("Event dict must contain 'event' key")
        return cls(
            event=d["event"],
            state=d.get("state", ""),
            text=d.get("text", ""),
            data=d.get("data", {}),
        )


class EventBus:
    """Receives avatar events over a ZeroMQ PULL socket.

    Args:
        socket_path: Path for the Unix domain socket.
    """

    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH) -> None:
        self._socket_path = socket_path
        self._context: zmq.Context | None = None
        self._socket: zmq.Socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.on_event: Callable[[AvatarEvent], None] | None = None
        self._last_event_time: float | None = None
        self._lock = threading.Lock()

    @property
    def socket_path(self) -> str:
        return self._socket_path

    @property
    def connected(self) -> bool:
        """True if at least one event has been received."""
        with self._lock:
            return self._last_event_time is not None

    @property
    def last_event_time(self) -> float | None:
        """Monotonic timestamp of the most recently received event, or None."""
        with self._lock:
            return self._last_event_time

    @property
    def time_since_last_event(self) -> float | None:
        """Seconds since the last event was received, or None if never received."""
        with self._lock:
            if self._last_event_time is None:
                return None
            return time.monotonic() - self._last_event_time

    def start(self) -> None:
        self._stop_event.clear()
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.PULL)
        self._socket.bind(f"ipc://{self._socket_path}")

        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    def _recv_loop(self) -> None:
        assert self._socket is not None
        poller = zmq.Poller()
        poller.register(self._socket, zmq.POLLIN)

        while not self._stop_event.is_set():
            socks = dict(poller.poll(timeout=100))
            if self._socket in socks:
                try:
                    raw = self._socket.recv(zmq.NOBLOCK)
                    data = json.loads(raw)
                    event = AvatarEvent.from_dict(data)
                    with self._lock:
                        self._last_event_time = time.monotonic()
                    if self.on_event:
                        self.on_event(event)
                except (json.JSONDecodeError, ValueError) as e:
                    log.warning("Malformed event: %s", e)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._socket is not None:
            self._socket.close()
        if self._context is not None:
            self._context.term()
        # Clean up socket file
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
