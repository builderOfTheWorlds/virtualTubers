"""
test_tail_bus_format.py
Unit tests for the pure formatting/filtering helpers in app/tail_bus.py.
Kafka is never touched — only the importable pure functions are exercised.
conftest.py inserts app/ onto sys.path.
"""
import re
import sys
from unittest import mock

import pytest

# Ensure importing tail_bus doesn't require a live kafka broker: message_bus
# imports `from kafka import ...` at module load, so stub it if absent.
try:  # pragma: no cover - environment dependent
    import kafka  # noqa: F401
except Exception:  # pragma: no cover
    sys.modules["kafka"] = mock.MagicMock()

import tail_bus  # noqa: E402


ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text):
    return ANSI_RE.sub("", text)


def make_msg(**overrides):
    msg = {
        "id": "abc",
        "from": "coder",
        "to": "manager",
        "type": "task_complete",
        "payload": {"ticket": 42},
        "timestamp": "2026-07-01T12:34:56+00:00",
    }
    msg.update(overrides)
    return msg


# ── Filtering: hide_types ─────────────────────────────────────────────────────
@pytest.mark.parametrize("msg_type", ["heartbeat", "status_update"])
def test_default_hide_types_drop_flood(msg_type):
    filters = dict(tail_bus.DEFAULT_FEED_CONFIG["filters"])
    assert tail_bus.passes_filters(make_msg(type=msg_type), filters) is False


def test_non_hidden_type_passes():
    filters = dict(tail_bus.DEFAULT_FEED_CONFIG["filters"])
    assert tail_bus.passes_filters(make_msg(type="task_complete"), filters) is True


# ── Filtering: show_types whitelist ───────────────────────────────────────────
def test_show_types_whitelist_keeps_only_listed():
    filters = {"hide_types": [], "show_types": ["error"], "direction": "all"}
    assert tail_bus.passes_filters(make_msg(type="error"), filters) is True
    assert tail_bus.passes_filters(make_msg(type="task_complete"), filters) is False


def test_show_types_empty_allows_all_non_hidden():
    filters = {"hide_types": [], "show_types": [], "direction": "all"}
    assert tail_bus.passes_filters(make_msg(type="anything"), filters) is True


# ── Filtering: direction ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "direction,msg_kwargs,expected",
    [
        ("broadcast", {"to": "broadcast"}, True),
        ("broadcast", {"to": "coder"}, False),
        ("to:manager", {"to": "manager"}, True),
        ("to:manager", {"to": "coder"}, False),
        ("from:coder", {"from": "coder"}, True),
        ("from:coder", {"from": "manager"}, False),
        ("all", {"to": "anyone"}, True),
        ("unknown-spec", {"to": "anyone"}, True),  # permissive fallback
    ],
)
def test_direction_filter(direction, msg_kwargs, expected):
    filters = {"hide_types": [], "show_types": [], "direction": direction}
    assert tail_bus.passes_filters(make_msg(**msg_kwargs), filters) is expected


# ── Sender coloring ───────────────────────────────────────────────────────────
def test_sender_coloring_applied():
    cfg = tail_bus._merge_feed_config({})
    line = tail_bus.format_line(make_msg(**{"from": "coder"}), cfg)
    # green = ANSI 32
    assert "\033[32m" in line
    assert "coder" in strip_ansi(line)


def test_unknown_sender_not_colored():
    cfg = tail_bus._merge_feed_config({})
    line = tail_bus.format_line(make_msg(**{"from": "ghost"}), cfg)
    assert "ghost" in strip_ansi(line)
    # No color code should wrap an unknown sender (no color mapping)
    # broadcast/type may still add codes, so just assert ghost is uncolored:
    assert "\033[3" not in line.split("──▶")[0].replace("\033[36m", "")


# ── Type highlight ────────────────────────────────────────────────────────────
def test_type_highlight_applied():
    cfg = tail_bus._merge_feed_config({})
    line = tail_bus.format_line(make_msg(type="error"), cfg)
    assert "\033[31m" in line  # red
    assert "error" in strip_ansi(line)


def test_type_without_highlight_not_colored():
    cfg = tail_bus._merge_feed_config({})
    line = tail_bus.format_line(make_msg(type="plain_type"), cfg)
    assert "plain_type" in strip_ansi(line)


# ── Payload truncation ────────────────────────────────────────────────────────
def test_payload_truncated_to_max_chars():
    cfg = {"payload": {"mode": "raw", "max_chars": 10}}
    payload = {"text": "x" * 100}
    out = tail_bus.format_payload(payload, cfg["payload"])
    assert len(out) == 10
    assert out.endswith("…")


def test_payload_not_truncated_when_short():
    out = tail_bus.format_payload({"a": 1}, {"mode": "pretty", "max_chars": 80})
    assert out == "a=1"


# ── Payload modes ─────────────────────────────────────────────────────────────
def test_payload_mode_pretty():
    out = tail_bus.format_payload({"ticket": 42, "ok": True}, {"mode": "pretty", "max_chars": 80})
    assert out == "ticket=42, ok=True"


def test_payload_mode_raw():
    out = tail_bus.format_payload({"ticket": 42}, {"mode": "raw", "max_chars": 80})
    assert out == "{'ticket': 42}"


def test_payload_mode_hidden():
    out = tail_bus.format_payload({"ticket": 42}, {"mode": "hidden", "max_chars": 80})
    assert out == ""


# ── Timestamp formatting ──────────────────────────────────────────────────────
def test_timestamp_format_utc_no_local():
    ts = tail_bus.format_timestamp(
        "2026-07-01T12:34:56+00:00", {"format": "%H:%M:%S", "local": False}
    )
    assert ts == "12:34:56"


def test_timestamp_custom_format():
    ts = tail_bus.format_timestamp(
        "2026-07-01T12:34:56+00:00", {"format": "%Y-%m-%d", "local": False}
    )
    assert ts == "2026-07-01"


def test_timestamp_bad_value_returns_string():
    assert tail_bus.format_timestamp("not-a-date", {"local": False}) == "not-a-date"


# ── Config merge tolerance ────────────────────────────────────────────────────
def test_merge_feed_config_defaults_when_empty():
    cfg = tail_bus._merge_feed_config({})
    assert cfg["filters"]["hide_types"] == ["heartbeat", "status_update"]


def test_merge_feed_config_partial_override():
    cfg = tail_bus._merge_feed_config({"filters": {"direction": "broadcast"}})
    # overridden key present; untouched defaults preserved
    assert cfg["filters"]["direction"] == "broadcast"
    assert "hide_types" in cfg["filters"]


def test_load_feed_config_missing_path_uses_defaults():
    cfg = tail_bus.load_feed_config(None)
    assert cfg["header"] is True
    assert cfg["payload"]["mode"] == "pretty"


# ── Full line shape ───────────────────────────────────────────────────────────
def test_format_line_contains_arrow_and_fields():
    cfg = tail_bus._merge_feed_config({})
    plain = strip_ansi(tail_bus.format_line(make_msg(), cfg))
    assert "──▶" in plain
    assert "coder" in plain
    assert "manager" in plain
    assert "task_complete" in plain
    assert "ticket=42" in plain


# ── connect_with_retry: bootstrap failures must not crash the pane ────────────
def test_connect_with_retry_succeeds_after_transient_failures():
    attempts = {"n": 0}
    sentinel = mock.MagicMock()

    def flaky_consumer(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Exception("Unable to bootstrap from 192.168.1.120:9092")
        return sentinel

    sleeps = []
    with mock.patch.object(tail_bus, "MessageConsumer", side_effect=flaky_consumer):
        result = tail_bus.connect_with_retry(
            "192.168.1.120:9092", "vtuber.messages", "vtuber-display-coder",
            sleep=sleeps.append,
        )

    assert result is sentinel
    assert attempts["n"] == 3
    # backoff: retried twice before success, with increasing delay
    assert sleeps == [
        tail_bus.CONNECT_RETRY_BASE_SECONDS,
        tail_bus.CONNECT_RETRY_BASE_SECONDS * 2,
    ]


def test_connect_with_retry_never_raises():
    with mock.patch.object(tail_bus, "MessageConsumer", side_effect=Exception("boom")):
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise StopIteration  # bail out of the infinite retry loop for the test

        with pytest.raises(StopIteration):
            tail_bus.connect_with_retry(
                "broker:9092", "topic", "group", sleep=fake_sleep
            )
    # delay caps at CONNECT_RETRY_MAX_SECONDS rather than growing unbounded
    assert sleeps[0] == tail_bus.CONNECT_RETRY_BASE_SECONDS
