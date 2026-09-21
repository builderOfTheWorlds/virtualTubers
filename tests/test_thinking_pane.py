"""
test_thinking_pane.py
Unit tests for the pure filtering/formatting helpers in app/thinking_pane.py.
Kafka is never touched — only importable pure functions are exercised.
conftest.py inserts app/ onto sys.path. message_bus imports `from kafka
import ...` at module load, so stub it if a real broker client isn't
installed (mirrors tests/test_tail_bus_format.py's guard).
"""
import sys
from unittest import mock

try:  # pragma: no cover - environment dependent
    import kafka  # noqa: F401
except Exception:  # pragma: no cover
    sys.modules["kafka"] = mock.MagicMock()

import thinking_pane


def make_thinking_msg(**overrides):
    msg = {
        "from": "coder",
        "to": "broadcast",
        "type": "agent_thinking",
        "payload": {"text": "Considering the best approach to this ticket."},
        "timestamp": "2026-07-01T12:34:56+00:00",
    }
    msg.update(overrides)
    return msg


# ── is_own_thinking: pin the worker's own stream ────────────────────────────
def test_is_own_thinking_true_for_matching_worker():
    msg = make_thinking_msg(**{"from": "coder"})
    assert thinking_pane.is_own_thinking(msg, "coder") is True


def test_is_own_thinking_false_for_other_worker():
    msg = make_thinking_msg(**{"from": "manager"})
    assert thinking_pane.is_own_thinking(msg, "coder") is False


def test_is_own_thinking_false_for_wrong_type():
    msg = make_thinking_msg(type="status_update")
    assert thinking_pane.is_own_thinking(msg, "coder") is False


def test_is_own_thinking_false_when_from_missing():
    msg = make_thinking_msg()
    del msg["from"]
    assert thinking_pane.is_own_thinking(msg, "coder") is False


# ── format_thinking_line ─────────────────────────────────────────────────────
def test_format_thinking_line_contains_text():
    msg = make_thinking_msg(**{"payload": {"text": "Weighing two implementation options."}})
    line = thinking_pane.format_thinking_line(msg)
    assert "Weighing two implementation options." in line


def test_format_thinking_line_bad_timestamp_does_not_crash():
    msg = make_thinking_msg(timestamp="not-a-date")
    line = thinking_pane.format_thinking_line(msg)
    assert "not-a-date" in line


def test_format_thinking_line_missing_timestamp_placeholder():
    msg = make_thinking_msg()
    del msg["timestamp"]
    line = thinking_pane.format_thinking_line(msg)
    assert "??:??:??" in line


def test_format_thinking_line_missing_text_renders_empty_skippable():
    msg = make_thinking_msg(payload={})
    line = thinking_pane.format_thinking_line(msg)
    # Empty text -> line is just the timestamp bracket, safe to filter by
    # the caller (run loop skips blank-text lines).
    assert line.strip().endswith("]")
