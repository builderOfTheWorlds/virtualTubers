import json
import os
import tempfile
import threading
import time

import pytest
import zmq

from avatar.event_bus import AvatarEvent, EventBus


@pytest.fixture
def socket_path(tmp_path):
    return str(tmp_path / "test-avatar.sock")


@pytest.fixture
def event_bus(socket_path):
    bus = EventBus(socket_path=socket_path)
    yield bus
    bus.stop()


def send_event(socket_path: str, event: dict) -> None:
    """Helper: send a single event via PUSH socket."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.connect(f"ipc://{socket_path}")
    time.sleep(0.05)  # Allow connection to establish
    sock.send_json(event)
    sock.close()
    ctx.term()


class TestAvatarEvent:
    def test_from_dict_state_change(self):
        e = AvatarEvent.from_dict({"event": "state_change", "state": "thinking"})
        assert e.event == "state_change"
        assert e.state == "thinking"

    def test_from_dict_speak_start(self):
        e = AvatarEvent.from_dict({
            "event": "speak_start",
            "text": "hello",
            "data": {"timestamps": []},
        })
        assert e.event == "speak_start"
        assert e.text == "hello"

    def test_from_dict_missing_event_raises(self):
        with pytest.raises(ValueError):
            AvatarEvent.from_dict({"state": "idle"})


class TestEventBus:
    def test_creates_pull_socket(self, event_bus, socket_path):
        event_bus.start()
        # Verify the socket file was created
        assert os.path.exists(socket_path)

    def test_receives_state_change(self, event_bus, socket_path):
        received = []
        event_bus.on_event = lambda e: received.append(e)
        event_bus.start()
        time.sleep(0.1)

        send_event(socket_path, {"event": "state_change", "state": "thinking"})
        time.sleep(0.2)

        assert len(received) == 1
        assert received[0].event == "state_change"
        assert received[0].state == "thinking"

    def test_receives_multiple_events(self, event_bus, socket_path):
        received = []
        event_bus.on_event = lambda e: received.append(e)
        event_bus.start()
        time.sleep(0.1)

        send_event(socket_path, {"event": "state_change", "state": "thinking"})
        send_event(socket_path, {"event": "state_change", "state": "idle"})
        time.sleep(0.3)

        assert len(received) == 2

    def test_stop_and_cleanup(self, event_bus, socket_path):
        event_bus.start()
        time.sleep(0.1)
        event_bus.stop()
        # Socket file should be cleaned up
        time.sleep(0.1)
        assert not os.path.exists(socket_path)

    def test_ignores_malformed_json(self, event_bus, socket_path):
        received = []
        event_bus.on_event = lambda e: received.append(e)
        event_bus.start()
        time.sleep(0.1)

        # Send malformed data
        ctx = zmq.Context()
        sock = ctx.socket(zmq.PUSH)
        sock.connect(f"ipc://{socket_path}")
        time.sleep(0.05)
        sock.send(b"not json")
        sock.close()
        ctx.term()

        # Then send a valid event
        send_event(socket_path, {"event": "state_change", "state": "idle"})
        time.sleep(0.2)

        # Should only have the valid event
        assert len(received) == 1

    def test_connected_false_before_any_event(self, event_bus):
        event_bus.start()
        assert event_bus.connected is False

    def test_connected_true_after_event(self, event_bus, socket_path):
        event_bus.start()
        time.sleep(0.1)

        send_event(socket_path, {"event": "state_change", "state": "idle"})
        time.sleep(0.2)

        assert event_bus.connected is True

    def test_last_event_time_none_before_any_event(self, event_bus):
        event_bus.start()
        assert event_bus.last_event_time is None

    def test_last_event_time_set_after_event(self, event_bus, socket_path):
        event_bus.start()
        time.sleep(0.1)

        before = time.monotonic()
        send_event(socket_path, {"event": "state_change", "state": "idle"})
        time.sleep(0.2)
        after = time.monotonic()

        assert event_bus.last_event_time is not None
        assert before <= event_bus.last_event_time <= after

    def test_time_since_last_event_none_before_any_event(self, event_bus):
        event_bus.start()
        assert event_bus.time_since_last_event is None

    def test_time_since_last_event_small_after_recent_event(self, event_bus, socket_path):
        event_bus.start()
        time.sleep(0.1)

        send_event(socket_path, {"event": "state_change", "state": "idle"})
        time.sleep(0.2)

        elapsed = event_bus.time_since_last_event
        assert elapsed is not None
        assert 0.0 <= elapsed < 5.0  # Should be very recent

    def test_heartbeat_event_sets_connected(self, event_bus, socket_path):
        received = []
        event_bus.on_event = lambda e: received.append(e)
        event_bus.start()
        time.sleep(0.1)

        send_event(socket_path, {"event": "heartbeat"})
        time.sleep(0.2)

        # connected is tracked at bus level via last_event_time
        assert event_bus.connected is True
        # heartbeat is dispatched to on_event like any other event
        assert len(received) == 1
        assert received[0].event == "heartbeat"
