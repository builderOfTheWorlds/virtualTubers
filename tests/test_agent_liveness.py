"""
test_agent_liveness.py
agent.py's liveness + bus-heartbeat cadence: the Redis liveness key is
written every tick (even while disabled), and the `status_update` bus
heartbeat follows agent.bus_heartbeat_every (0 = off). main() is driven for
a few ticks with every external dependency faked; time.sleep raises to end
the loop.
"""
from unittest.mock import MagicMock

import pytest

import agent


class _StopLoop(Exception):
    pass


class FakeProducer:
    def __init__(self, *args, **kwargs):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


class FakeConsumer:
    def __init__(self, *args, **kwargs):
        self.batches = []

    def poll_new(self):
        return self.batches.pop(0) if self.batches else []


class FakeControl:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.heartbeats = []

    def heartbeat(self, worker_id, ttl_s):
        self.heartbeats.append((worker_id, ttl_s))
        return True

    def is_enabled(self, worker_id):
        return self.enabled


@pytest.fixture
def run_agent(monkeypatch):
    """run_agent(agent_config, ticks, enabled=True) -> (producer, control)."""

    def _run(agent_config, ticks, enabled=True):
        config = {"agent": agent_config, "message_bus": {"worker_id": "coder", "topic": "t"}}
        producer = FakeProducer()
        control = FakeControl(enabled=enabled)
        monkeypatch.setattr("sys.argv", ["agent.py", "--config", "unused.yaml"])
        monkeypatch.delenv("WORKER_ID", raising=False)
        monkeypatch.setattr(agent, "load_worker_config", lambda path: config)
        monkeypatch.setattr(agent, "build_llm_client", lambda cfg: MagicMock())
        monkeypatch.setattr(agent, "build_coding_backend", lambda cfg, llm: None)
        monkeypatch.setattr(agent, "resolve_state_path", lambda cfg: None)
        monkeypatch.setattr(agent, "write_state", lambda *a, **k: None)
        monkeypatch.setattr(agent, "MessageProducer", lambda *a, **k: producer)
        monkeypatch.setattr(agent, "MessageConsumer", FakeConsumer)
        monkeypatch.setattr(agent.WorkerControl, "from_config", classmethod(lambda cls, cfg: control))
        monkeypatch.setattr(agent, "resolve_runtime_dir", lambda: None)
        monkeypatch.setattr(agent, "AgentMetrics", MagicMock())
        monkeypatch.setattr(agent, "MetricsProducerWrapper", lambda p, m: p)
        monkeypatch.setattr(agent, "InstrumentedLLMClient", lambda *a, **k: MagicMock())

        sleeps = {"n": 0}

        def fake_sleep(_s):
            sleeps["n"] += 1
            if sleeps["n"] >= ticks:
                raise _StopLoop

        monkeypatch.setattr(agent.time, "sleep", fake_sleep)
        with pytest.raises(_StopLoop):
            agent.main()
        return producer, control

    return _run


def _heartbeats(producer):
    return [m for m in producer.sent if m["type"] == "status_update"]


# ── pure helpers ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("config, expected", [
    ({}, agent.DEFAULT_BUS_HEARTBEAT_EVERY),
    ({"bus_heartbeat_every": None}, agent.DEFAULT_BUS_HEARTBEAT_EVERY),
    ({"bus_heartbeat_every": 0}, 0),
    ({"bus_heartbeat_every": 1}, 1),
    ({"bus_heartbeat_every": "5"}, 5),
    ({"bus_heartbeat_every": -3}, 0),
    ({"bus_heartbeat_every": "often"}, agent.DEFAULT_BUS_HEARTBEAT_EVERY),
])
def test_resolve_bus_heartbeat_every(config, expected):
    assert agent.resolve_bus_heartbeat_every(config) == expected


@pytest.mark.parametrize("config, tick_rate_s, expected", [
    ({}, 5.0, 15),                         # 3 ticks == floor
    ({}, 8.0, 24),                         # 3 ticks above floor
    ({}, 1.0, agent.MIN_LIVENESS_TTL_S),   # floor wins
    ({}, 6.5, 20),                         # rounded up
    ({"liveness_ttl_s": 60}, 5.0, 60),
    ({"liveness_ttl_s": 0}, 5.0, 15),      # 0 = auto
    ({"liveness_ttl_s": "bad"}, 5.0, 15),
])
def test_resolve_liveness_ttl(config, tick_rate_s, expected):
    assert agent.resolve_liveness_ttl(config, tick_rate_s) == expected


@pytest.mark.parametrize("tick, every, expected", [
    (0, 12, True), (11, 12, False), (12, 12, True), (5, 1, True), (0, 0, False), (7, 0, False),
])
def test_bus_heartbeat_due(tick, every, expected):
    assert agent.bus_heartbeat_due(tick, every) is expected


# ── loop behaviour ────────────────────────────────────────────────────────────

def test_loop_writes_liveness_key_every_tick(run_agent):
    _, control = run_agent({"tick_rate_ms": 5000}, ticks=4)
    assert control.heartbeats == [("coder", 15)] * 4


def test_loop_writes_liveness_even_while_disabled(run_agent):
    producer, control = run_agent({"tick_rate_ms": 5000, "bus_heartbeat_every": 1}, ticks=3, enabled=False)
    assert len(control.heartbeats) == 3
    assert _heartbeats(producer) == []


def test_loop_bus_heartbeat_every_tick_when_set_to_one(run_agent):
    producer, _ = run_agent({"bus_heartbeat_every": 1}, ticks=4)
    assert [m["payload"]["text"] for m in _heartbeats(producer)] == [
        "heartbeat #0", "heartbeat #1", "heartbeat #2", "heartbeat #3"]


def test_loop_bus_heartbeat_follows_cadence(run_agent):
    producer, _ = run_agent({"bus_heartbeat_every": 3}, ticks=7)
    assert [m["payload"]["text"] for m in _heartbeats(producer)] == [
        "heartbeat #0", "heartbeat #3", "heartbeat #6"]


def test_loop_bus_heartbeat_off_when_zero(run_agent):
    producer, control = run_agent({"bus_heartbeat_every": 0}, ticks=5)
    assert _heartbeats(producer) == []
    assert len(control.heartbeats) == 5


def test_loop_default_cadence_is_low_rate(run_agent):
    producer, _ = run_agent({}, ticks=agent.DEFAULT_BUS_HEARTBEAT_EVERY + 1)
    assert len(_heartbeats(producer)) == 2  # tick 0 and tick N


def test_loop_uses_configured_liveness_ttl(run_agent):
    _, control = run_agent({"liveness_ttl_s": 42}, ticks=1)
    assert control.heartbeats == [("coder", 42)]
