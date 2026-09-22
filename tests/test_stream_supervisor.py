"""
Tests for app/stream_supervisor.py: decide_action() — the pure
enabled/running -> start/stop/noop decision table, no I/O — and
pulse_monitor_available()/build_ffmpeg_cmd()'s audio input selection
(subprocess.run mocked; never touches a real Pulse server or ffmpeg).
"""
from unittest.mock import patch

import pytest

import stream_supervisor as ss
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
        cmd = build_ffmpeg_cmd("rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99", use_gpu=False)
    assert "-f" in cmd and "pulse" in cmd
    i = cmd.index("pulse")
    assert cmd[i - 1] == "-f"
    assert cmd[i + 1] == "-i"
    assert cmd[i + 2] == "vout.monitor"
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" not in cmd


def test_build_ffmpeg_cmd_falls_back_to_silence_when_no_monitor():
    with patch("stream_supervisor.pulse_monitor_available", return_value=False):
        cmd = build_ffmpeg_cmd("rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99", use_gpu=False)
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in cmd
    assert "vout.monitor" not in cmd


# ── capture-vs-output resolution (Contract E, docs/tuber_base_layout_plan.md) ─
# All pinned use_gpu=False: these assert the exact CPU-path scale=W:H
# filter string, which the GPU path (scale_cuda=W:H, see below) doesn't
# produce — auto-detection would make these tests non-deterministic
# across hosts with/without a real GPU (e.g. this dev machine's RTX 3080
# vs. a CI runner with none).
def test_build_ffmpeg_cmd_defaults_capture_resolution_to_resolution():
    """No capture_resolution passed -> byte-identical to pre-scaling behavior:
    -video_size uses `resolution` and no -vf scale filter is present."""
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd("rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99", use_gpu=False)
    i = cmd.index("-video_size")
    assert cmd[i + 1] == "1920x1080"
    assert "-vf" not in cmd


def test_build_ffmpeg_cmd_equal_capture_and_output_omits_scale_filter():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99",
            capture_resolution="1920x1080", use_gpu=False,
        )
    i = cmd.index("-video_size")
    assert cmd[i + 1] == "1920x1080"
    assert "-vf" not in cmd


def test_build_ffmpeg_cmd_scales_down_larger_capture_to_output():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99",
            capture_resolution="3840x2160", use_gpu=False,
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
            capture_resolution="2560x1440", use_gpu=False,
        )
    assert cmd[:7] == ["ffmpeg",
                       "-thread_queue_size", str(ss.video_thread_queue_size("2560x1440")),
                       "-f", "x11grab",
                       "-video_size", "2560x1440"]
    assert "scale=1280:720" in cmd


# ── GPU (NVENC) encoding — see nvenc_available()'s docstring for why this
#    exists: 6+ workers all running software libx264 simultaneously was
#    the suspected cause of Twitch stream choppiness (CPU contention),
#    confirmed against gx10's real load average. ─────────────────────────
def test_nvenc_available_true_when_codec_compiled_in_and_gpu_present():
    import stream_supervisor
    stream_supervisor._nvenc_available_cache = None
    encoders_result = type("R", (), {"returncode": 0, "stdout": "V....D h264_nvenc  NVIDIA NVENC H.264 encoder\n"})()
    gpu_result = type("R", (), {"returncode": 0, "stdout": "GPU 0: NVIDIA GB10/PCIe\n"})()
    with patch("stream_supervisor.subprocess.run", side_effect=[encoders_result, gpu_result]):
        assert stream_supervisor.nvenc_available() is True
    stream_supervisor._nvenc_available_cache = None


def test_nvenc_available_false_when_codec_not_compiled_in():
    import stream_supervisor
    stream_supervisor._nvenc_available_cache = None
    encoders_result = type("R", (), {"returncode": 0, "stdout": "V....D libx264  H.264 encoder\n"})()
    with patch("stream_supervisor.subprocess.run", return_value=encoders_result):
        assert stream_supervisor.nvenc_available() is False
    stream_supervisor._nvenc_available_cache = None


def test_nvenc_available_false_when_codec_present_but_no_gpu():
    """The gx10 bug this specifically guards against: NVENC can be
    compiled into ffmpeg with no GPU actually passed through to the
    container — must not be reported available in that case."""
    import stream_supervisor
    stream_supervisor._nvenc_available_cache = None
    encoders_result = type("R", (), {"returncode": 0, "stdout": "V....D h264_nvenc  NVIDIA NVENC H.264 encoder\n"})()
    gpu_result = type("R", (), {"returncode": 1, "stdout": ""})()
    with patch("stream_supervisor.subprocess.run", side_effect=[encoders_result, gpu_result]):
        assert stream_supervisor.nvenc_available() is False
    stream_supervisor._nvenc_available_cache = None


def test_nvenc_available_false_and_cached_when_ffmpeg_missing():
    import stream_supervisor
    stream_supervisor._nvenc_available_cache = None
    with patch("stream_supervisor.subprocess.run", side_effect=OSError("no ffmpeg")):
        assert stream_supervisor.nvenc_available() is False
    # second call must NOT shell out again — cached
    with patch("stream_supervisor.subprocess.run", side_effect=AssertionError("should be cached")):
        assert stream_supervisor.nvenc_available() is False
    stream_supervisor._nvenc_available_cache = None


def test_build_ffmpeg_cmd_use_gpu_false_is_byte_identical_to_pre_gpu_behavior():
    """use_gpu=False (or auto-detected False) must produce EXACTLY the
    same command as before nvenc_available()/use_gpu existed — no
    regression for hosts without a GPU."""
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99",
            capture_resolution="3840x2160", use_gpu=False,
        )
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "-vf" in cmd and cmd[cmd.index("-vf") + 1] == "scale=1920:1080"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"


def test_build_ffmpeg_cmd_use_gpu_true_uses_nvenc_and_scale_cuda():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99",
            capture_resolution="3840x2160", use_gpu=True,
        )
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "hwupload_cuda" in vf and "scale_cuda=1920:1080" in vf
    assert "-pix_fmt" not in cmd  # NVENC path doesn't set pix_fmt explicitly


def test_build_ffmpeg_cmd_use_gpu_true_no_scale_needed_still_uploads_cuda():
    """Even when capture_resolution == resolution (no resize needed), the
    GPU path must still hwupload_cuda before nvenc — nvenc can't encode
    directly from a plain CPU-side frame."""
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99",
            use_gpu=True,
        )
    vf = cmd[cmd.index("-vf") + 1]
    assert "hwupload_cuda" in vf
    assert "scale_cuda" not in vf  # no resize requested, no scale_cuda needed


def test_build_ffmpeg_cmd_use_gpu_none_calls_nvenc_available():
    """The default (use_gpu=None) must defer to auto-detection, not
    silently default to either encoder."""
    with patch("stream_supervisor.pulse_monitor_available", return_value=True), \
         patch("stream_supervisor.nvenc_available", return_value=True) as mock_detect:
        cmd = build_ffmpeg_cmd("rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99")
    mock_detect.assert_called_once()
    assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"


# ── True CBR, matching docs/twitch_broadcasting_guidelines.md's explicit
#    warning against VBR ("broadcast starvation" — bursty delivery the
#    player buffers heavily around, exactly what this session's live
#    stream stats showed: 566Kbps download vs. an 84Mbps bandwidth
#    estimate, 6.72s buffer, 11.36s latency). ─────────────────────────────
def test_build_ffmpeg_cmd_gpu_path_forces_rc_cbr():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99", use_gpu=True)
    assert "-rc" in cmd and cmd[cmd.index("-rc") + 1] == "cbr"


def test_build_ffmpeg_cmd_cpu_path_forces_nal_hrd_cbr():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99", use_gpu=False)
    assert "-x264opts" in cmd
    opts = cmd[cmd.index("-x264opts") + 1]
    assert "nal-hrd=cbr" in opts
    assert "force-cfr=1" in opts  # required alongside nal-hrd=cbr or x264 ignores it


def test_build_ffmpeg_cmd_bitrate_and_bufsize_match_twitch_1080p30_recommendation():
    """docs/twitch_broadcasting_guidelines.md's 1080p30 CBR spec: 4500kbps,
    bufsize == bitrate (not a multiple of it — a larger bufsize is what
    permits the bursty delivery the guide warns against), 2s keyframe
    interval (60 frames at this pipeline's fixed 30fps capture)."""
    import stream_supervisor as ss
    for use_gpu in (True, False):
        with patch("stream_supervisor.pulse_monitor_available", return_value=True):
            cmd = build_ffmpeg_cmd(
                "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99",
                use_gpu=use_gpu)
        expected = f"{ss.TWITCH_BITRATE_KBPS}k"
        assert cmd[cmd.index("-b:v") + 1] == expected
        assert cmd[cmd.index("-maxrate") + 1] == expected
        assert cmd[cmd.index("-bufsize") + 1] == expected  # == bitrate, not a multiple
        assert cmd[cmd.index("-g") + 1] == str(ss.TWITCH_KEYFRAME_INTERVAL_FRAMES)


# ── thread_queue_size: a real dropped-frame bug found live on gx10 —
#    "Thread message queue blocking; consider raising the thread_queue_size
#    option (current value: 8)" in ffmpeg's own stderr, independent of
#    capture resolution or encoder settings. ffmpeg's default input queue
#    (8 frames) between the x11grab/pulse capture thread and the rest of
#    the pipeline was overflowing and blocking under real load, capping
#    throughput well below the requested framerate regardless of how fast
#    the encoder itself could go. ─────────────────────────────────────────
def test_build_ffmpeg_cmd_sets_thread_queue_size_for_video_input():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99", use_gpu=False)
    # the FIRST -thread_queue_size must precede the x11grab -i (video input)
    i = cmd.index("-thread_queue_size")
    assert cmd[i + 1] == str(ss.video_thread_queue_size("1920x1080"))
    assert cmd.index("-i") > i  # comes before this input, not after


def test_build_ffmpeg_cmd_sets_thread_queue_size_for_pulse_audio_input():
    with patch("stream_supervisor.pulse_monitor_available", return_value=True):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99", use_gpu=False)
    # both the video AND pulse audio inputs need their own -thread_queue_size
    assert cmd.count("-thread_queue_size") == 2
    pulse_i = cmd.index("pulse")
    assert cmd[pulse_i - 3] == "-thread_queue_size"
    assert cmd[pulse_i - 2] == str(ss.AUDIO_QUEUE_PACKETS)


def test_build_ffmpeg_cmd_silent_audio_fallback_has_no_thread_queue_size():
    """anullsrc is a synthesized lavfi source, not a real capture thread —
    it doesn't need (and ffmpeg would likely warn/ignore) a queue size."""
    with patch("stream_supervisor.pulse_monitor_available", return_value=False):
        cmd = build_ffmpeg_cmd(
            "rtmp://live.twitch.tv/app", "key123", "1920x1080", ":99", use_gpu=False)
    assert cmd.count("-thread_queue_size") == 1  # only the video input's


# ── video queue must be sized in BYTES, not frames — the flat 1024 above
#    was an OOM bug: x11grab packets are whole uncompressed frames
#    (~14.7 MB at 1440p), so 1024 of them authorized ~15 GB of shmem per
#    ffmpeg, and 7 concurrent stream workers global-OOM-killed the host
#    (ffmpeg dying with shmem-rss ~7 GB, taking pipewire/dbus/the user
#    systemd and every worker container with it). ────────────────────────
@pytest.mark.parametrize("capture_resolution", [
    "1280x720", "1920x1080", "2560x1440", "3840x2160",
])
def test_video_thread_queue_stays_within_memory_budget(capture_resolution):
    width, height = (int(v) for v in capture_resolution.split("x"))
    frames = ss.video_thread_queue_size(capture_resolution)
    assert frames * width * height * 4 <= ss.VIDEO_QUEUE_BUDGET_BYTES


def test_video_thread_queue_shrinks_as_capture_resolution_grows():
    assert (ss.video_thread_queue_size("3840x2160")
            < ss.video_thread_queue_size("1280x720"))


def test_video_thread_queue_never_below_ffmpeg_default():
    # an absurd capture size must still leave a usable queue, not 0
    assert ss.video_thread_queue_size("30000x30000") == ss.VIDEO_QUEUE_MIN_FRAMES


def test_video_thread_queue_is_capped_for_tiny_captures():
    assert ss.video_thread_queue_size("64x64") == ss.VIDEO_QUEUE_MAX_FRAMES


@pytest.mark.parametrize("bad", ["", "notaresolution", "1920", "0x0", "axb"])
def test_video_thread_queue_falls_back_to_floor_on_bad_input(bad):
    """Unparseable input must fail SMALL — a short queue drops frames, an
    unbounded one takes the whole host down."""
    assert ss.video_thread_queue_size(bad) == ss.VIDEO_QUEUE_MIN_FRAMES


def test_total_video_queue_memory_across_all_workers_is_bounded():
    """The actual production shape that broke: 7 stream workers, each with
    its own ffmpeg, must not collectively authorize more frame buffer than
    the host has RAM."""
    workers = 7
    capture_resolution = "2560x1440"
    width, height = (int(v) for v in capture_resolution.split("x"))
    per_worker = ss.video_thread_queue_size(capture_resolution) * width * height * 4
    assert workers * per_worker <= 8 * 1024 ** 3  # well under the 121 GB host

