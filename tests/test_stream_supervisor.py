"""
Tests for app/stream_supervisor.py's decide_action() — the pure
enabled/running -> start/stop/noop decision table. No subprocess or Redis
mocking needed since the function has no I/O.
"""
from stream_supervisor import decide_action


def test_decide_action_starts_when_enabled_and_not_running():
    assert decide_action(enabled=True, proc_running=False) == "start"


def test_decide_action_stops_when_disabled_and_running():
    assert decide_action(enabled=False, proc_running=True) == "stop"


def test_decide_action_noop_when_enabled_and_running():
    assert decide_action(enabled=True, proc_running=True) == "noop"


def test_decide_action_noop_when_disabled_and_not_running():
    assert decide_action(enabled=False, proc_running=False) == "noop"
