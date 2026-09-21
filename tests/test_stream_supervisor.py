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


# ── capture-vs-output resolution (Contract E, docs/tuber_base_layout_plan.md) ─
def test_build_ffmpeg_cmd_defaults_capture_resolution_to_resolution():
    """No capture_resolution passed -> byte-identical to pre-scaling behavior:
    -video_size uses `resolution` and no -vf scale filter is present."""
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd("rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99")
    i = cmd.index("-video_size")
    assert cmd[i + 1] == "1920x1080"
    assert "-vf" not in cmd


def test_build_ffmpeg_cmd_equal_capture_and_output_omits_scale_filter():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99",
            capture_resolution="1920x1080",
        )
    i = cmd.index("-video_size")
    assert cmd[i + 1] == "1920x1080"
    assert "-vf" not in cmd


def test_build_ffmpeg_cmd_scales_down_larger_capture_to_output():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99",
            capture_resolution="3840x2160",
        )
    i = cmd.index("-video_size")
    assert cmd[i + 1] == "3840x2160"
    assert "-vf" in cmd
    vf_i = cmd.index("-vf")
    assert cmd[vf_i + 1] == "scale=1920:1080"
    # scale filter must precede the encode args
    assert vf_i < cmd.index("-c:v")


def test_build_ffmpeg_cmd_capture_resolution_precedes_framerate_and_input():
    """-video_size <capture_resolution> must be what x11grab is told to grab,
    independent of the output/scale settings."""
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1280x720", ":99",
            capture_resolution="2560x1440",
        )
    assert cmd[:5] == ["ffmpeg", "-f", "x11grab", "-video_size", "2560x1440"]
    assert "scale=1280:720" in cmd
