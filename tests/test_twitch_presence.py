"""
Tests for services/twitch-presence/presence.py — the pure logic (channel-map
parsing, IRC JOIN parsing, cooldown, message-api POST payload, line
dispatch). No real sockets or HTTP: sockets are faked, urllib is mocked.
"""
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "twitch-presence"))

import presence
from presence import (
    GreetingCooldown,
    PresenceWatcher,
    parse_channel_map,
    parse_ignored_users,
    parse_join,
    post_viewer_joined,
)


# ── parse_channel_map ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("mychan:coder", {"mychan": "coder"}),
    ("a:coder,b:manager", {"a": "coder", "b": "manager"}),
    ("#MyChan:coder", {"mychan": "coder"}),           # '#' stripped, lowercased
    (" a : coder , b : tester ", {"a": "coder", "b": "tester"}),
    ("", {}),
    (None, {}),
])
def test_parse_channel_map_valid_inputs(raw, expected):
    assert parse_channel_map(raw) == expected


def test_parse_channel_map_skips_malformed_entries_keeps_valid_ones():
    assert parse_channel_map("nocolon,good:coder,:noworker,nochannel:") == {"good": "coder"}


# ── parse_ignored_users ──────────────────────────────────────────────────────

def test_parse_ignored_users_extends_defaults():
    ignored = parse_ignored_users("MyBot, other_bot")
    assert "mybot" in ignored
    assert "other_bot" in ignored
    assert "nightbot" in ignored  # defaults kept, never replaced


# ── parse_join ───────────────────────────────────────────────────────────────

def test_parse_join_extracts_user_and_channel():
    line = ":phil!phil@phil.tmi.twitch.tv JOIN #mychan"
    assert parse_join(line) == ("phil", "mychan")


@pytest.mark.parametrize("line", [
    ":phil!phil@phil.tmi.twitch.tv PRIVMSG #mychan :hello",
    ":phil!phil@phil.tmi.twitch.tv PART #mychan",
    ":tmi.twitch.tv 353 justinfan123 = #mychan :user1 user2",  # NAMES list
    "PING :tmi.twitch.tv",
    ":tmi.twitch.tv JOIN #mychan",  # no user prefix
    "",
])
def test_parse_join_ignores_non_join_lines(line):
    assert parse_join(line) is None


# ── GreetingCooldown ─────────────────────────────────────────────────────────

def test_cooldown_greets_first_time_then_suppresses_within_window():
    clock = MagicMock(side_effect=[0, 10, 3601])
    cooldown = GreetingCooldown(3600, clock=clock)
    assert cooldown.should_greet("chan", "phil") is True
    assert cooldown.should_greet("chan", "phil") is False   # 10s later
    assert cooldown.should_greet("chan", "phil") is True    # window elapsed


def test_cooldown_tracks_channel_and_user_independently():
    clock = MagicMock(return_value=0)
    cooldown = GreetingCooldown(3600, clock=clock)
    assert cooldown.should_greet("chan", "phil") is True
    assert cooldown.should_greet("chan", "dana") is True
    assert cooldown.should_greet("other", "phil") is True


# ── post_viewer_joined ───────────────────────────────────────────────────────

def test_post_viewer_joined_sends_expected_message():
    with patch("presence.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b"{}"
        assert post_viewer_joined("http://api/messages", "coder", "phil", "mychan") is True
        request = urlopen.call_args[0][0]
        assert request.full_url == "http://api/messages"
        import json
        body = json.loads(request.data)
        assert body == {
            "to": "coder",
            "type": "viewer_joined",
            "payload": {"username": "phil", "channel": "mychan"},
        }


def test_post_viewer_joined_api_down_returns_false_never_raises():
    import urllib.error
    with patch("presence.urllib.request.urlopen",
               side_effect=urllib.error.URLError("refused")):
        assert post_viewer_joined("http://api/messages", "coder", "phil", "mychan") is False


# ── PresenceWatcher.handle_line ──────────────────────────────────────────────

@pytest.fixture
def watcher():
    w = PresenceWatcher(
        {"mychan": "coder"}, "http://api/messages", GreetingCooldown(3600),
    )
    w.nick = "justinfan12345"
    return w


def test_handle_line_join_posts_to_message_api(watcher):
    with patch("presence.post_viewer_joined") as post:
        keep = watcher.handle_line(MagicMock(), ":phil!phil@phil.tmi.twitch.tv JOIN #mychan")
    assert keep is True
    post.assert_called_once_with("http://api/messages", "coder", "phil", "mychan")


def test_handle_line_unmapped_channel_does_not_post(watcher):
    with patch("presence.post_viewer_joined") as post:
        watcher.handle_line(MagicMock(), ":phil!phil@phil.tmi.twitch.tv JOIN #otherchan")
    post.assert_not_called()


def test_handle_line_ignores_own_nick_and_known_bots(watcher):
    with patch("presence.post_viewer_joined") as post:
        watcher.handle_line(MagicMock(), f":{watcher.nick}!x@x.tmi.twitch.tv JOIN #mychan")
        watcher.handle_line(MagicMock(), ":nightbot!x@x.tmi.twitch.tv JOIN #mychan")
    post.assert_not_called()


def test_handle_line_respects_cooldown(watcher):
    with patch("presence.post_viewer_joined") as post:
        watcher.handle_line(MagicMock(), ":phil!phil@phil.tmi.twitch.tv JOIN #mychan")
        watcher.handle_line(MagicMock(), ":phil!phil@phil.tmi.twitch.tv JOIN #mychan")
    assert post.call_count == 1


def test_handle_line_answers_ping_with_pong(watcher):
    sock = MagicMock()
    keep = watcher.handle_line(sock, "PING :tmi.twitch.tv")
    assert keep is True
    sock.sendall.assert_called_once_with(b"PONG :tmi.twitch.tv\r\n")


def test_handle_line_reconnect_notice_returns_false(watcher):
    assert watcher.handle_line(MagicMock(), ":tmi.twitch.tv RECONNECT") is False
