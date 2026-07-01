from datetime import datetime

from message_bus import build_message


def test_build_message_valid_input():
    msg = build_message("coder", "manager", "task_complete", {"ticket": 42})
    assert msg["from"] == "coder"
    assert msg["to"] == "manager"
    assert msg["type"] == "task_complete"
    assert msg["payload"] == {"ticket": 42}
    assert "id" in msg
    assert "timestamp" in msg


def test_build_message_missing_payload_defaults_to_empty_dict():
    msg = build_message("coder", "broadcast", "status_update")
    assert msg["payload"] == {}


def test_build_message_generates_unique_ids():
    msg1 = build_message("coder", "manager", "status_update")
    msg2 = build_message("coder", "manager", "status_update")
    assert msg1["id"] != msg2["id"]


def test_build_message_timestamp_is_iso8601_utc():
    msg = build_message("coder", "manager", "status_update")
    parsed = datetime.fromisoformat(msg["timestamp"])
    assert parsed.tzinfo is not None
