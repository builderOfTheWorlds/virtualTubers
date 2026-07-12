"""
Tests for app/stream_supervisor.py: decide_action() — the pure
enabled/running -> start/stop/noop decision table, no I/O — and
pulse_monitor_available()/build_ffmpeg_cmd()'s audio input selection
(subprocess.run mocked; never touches a real Pulse server or ffmpeg).
"""
from unittest.mock import patch

from stream_supervisor import build_ffmpeg_cmd, decide_action, pulse_monitor_available


def test_decide_action_starts_when_enabled_and_not_running():
    assert decide_action(enabled=True, proc_running=False) == "start"


def test_decide_action_stops_when_disabled_and_running():
    assert decide_action(enabled=False, proc_running=True) == "stop"


def test_decide_action_noop_when_enabled_and_running():
    assert decide_action(enabled=True, proc_running=True) == "noop"


def test_decide_action_noop_when_disabled_and_not_running():
    assert decide_action(enabled=False, proc_running=False) == "noop"


# ── pulse_monitor_available / build_ffmpeg_cmd audio input ───────────────────
def _fake_pactl(stdout, returncode=0):
    result = type("R", (), {"stdout": stdout, "returncode": returncode})()
    return patch("stream_supervisor.subprocess.run", return_value=result)


def test_pulse_monitor_available_true_when_sink_listed():
    with _fake_pactl("0\tvout.monitor\tmodule-null-sink.c\ts16le 2ch 44100Hz\tRUNNING\n"):
        assert pulse_monitor_available() is True


def test_pulse_monitor_available_false_when_sink_missing():
    with _fake_pactl("0\talsa_output.monitor\tmodule-alsa-card.c\ts16le 2ch 44100Hz\tIDLE\n"):
        assert pulse_monitor_available() is False


def test_pulse_monitor_available_false_on_nonzero_exit():
    with _fake_pactl("vout.monitor\n", returncode=1):
        assert pulse_monitor_available() is False


def test_pulse_monitor_available_false_when_pactl_missing():
    with patch("stream_supervisor.subprocess.run", side_effect=OSError("no pactl")):
        assert pulse_monitor_available() is False


def test_build_ffmpeg_cmd_uses_pulse_input_when_monitor_available():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd("rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99")
    assert "-f" in cmd and "pulse" in cmd
    i = cmd.index("pulse")
    assert cmd[i - 1] == "-f"
    assert cmd[i + 1] == "-i"
    assert cmd[i + 2] == "vout.monitor"
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" not in cmd


def test_build_ffmpeg_cmd_falls_back_to_silence_when_no_monitor():
    with patch("stream_supervisor.pulse_monitor_available", return_value=False):
        cmd = build_ffmpeg_cmd("rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99")
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in cmd
    assert "vout.monitor" not in cmd
