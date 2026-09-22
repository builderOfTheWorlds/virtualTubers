#!/usr/bin/env python3
"""
stream_supervisor.py
Starts/stops the ffmpeg broadcaster based on this worker's on/off flag
(worker_control.WorkerControl), instead of running ffmpeg as startup.sh's
raw foreground command. This is what makes "disable" actually stop the
Twitch stream: it runs as the container's new long-lived foreground
process, and ffmpeg becomes a child it can kill and restart in place
without the container exiting (see startup.sh step 8 and
docs/stream_supervisor.md).
"""
import argparse
import os
import signal
import subprocess
import sys
import time

from message_bus import load_worker_config, resolve
from worker_control import WorkerControl

POLL_INTERVAL_S = 3
STOP_TIMEOUT_S = 10

#: docs/twitch_broadcasting_guidelines.md's own 1080p30 recommendation
#: (4500kbps CBR, 2s keyframe interval == 60 frames at 30fps). Was
#: 3000k/no-CBR before this session — Twitch's guide explicitly names
#: VBR as a cause of "broadcast starvation" (bursty delivery the player
#: buffers heavily around), matching this session's own stream stats
#: (Download Bitrate 566Kbps vs. an 84Mbps Bandwidth Estimate, Buffer
#: Size 6.72s, Latency 11.36s — a player fighting a bursty encoder, not
#: a bandwidth-limited one).
TWITCH_BITRATE_KBPS = 4500
TWITCH_KEYFRAME_INTERVAL_FRAMES = 60



def log(msg):
    print(f"[stream_supervisor] {msg}", flush=True)


def redact_stream_key(text):
    """Mask Twitch stream keys (live_XXX) in log messages to prevent
    credentials from being stored in Postgres via log-shipper."""
    import re
    return re.sub(r"\blive_[A-Za-z0-9_]{16,}\b", "[stream-key]", text)


def pulse_monitor_available(sink="vout"):
    """Whether PulseAudio is up and has the null sink's monitor source —
    i.e. whether audio_player.py's paplay (docs/audio_player.md) actually
    has somewhere to go that ffmpeg can hear. False on any error (Pulse
    down, pactl missing, timeout): the caller falls back to a silent audio
    track rather than failing the whole broadcaster over an audio-only
    problem."""
    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and f"{sink}.monitor" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


_nvenc_available_cache = None


def nvenc_available():
    """Whether this host can hardware-encode H.264 via NVIDIA NVENC —
    both the codec must be compiled into this ffmpeg AND a real GPU must
    actually be reachable (a container can have the codec compiled in
    with no GPU passed through, e.g. before the docker-compose.yml GPU
    passthrough fix — see gl_raster.py's history for that exact
    distinction mattering).

    WHY THIS MATTERS: every one of gx10's 6+ worker containers was
    running SOFTWARE x264 (`libx264 -preset veryfast`) simultaneously,
    all competing for the same CPU cores (load average ~10 on a 20-core
    host, observed 2026-09-22) — the most likely cause of stream
    choppiness reported on Twitch, separate from (and probably bigger
    than) the avatar rendering fixes earlier this session. NVENC moves
    the encode (and, via scale_cuda below, the 4K->1080p resize) off the
    CPU entirely and onto the GPU's dedicated encode hardware, which is
    NOT the same silicon doing 3D rendering (gpu_render_worker.py) or
    CUDA compute — so multiple workers encoding at once don't compete
    with each other or with avatar rendering the way software x264
    encodes compete for CPU cores. Confirmed on gx10: 7 concurrent NVENC
    sessions ran with no session-limit/out-of-memory errors, and a
    4K->1080p capture+encode held ~1x realtime speed standalone.

    Caches its result (this doesn't change mid-process) so this doesn't
    re-shell out to ffmpeg/nvidia-smi on every start_process() call —
    stream_supervisor restarts ffmpeg on every worker enable/disable
    toggle, and detection results don't change between those.
    """
    global _nvenc_available_cache
    if _nvenc_available_cache is not None:
        return _nvenc_available_cache
    try:
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5)
        has_codec = encoders.returncode == 0 and "h264_nvenc" in encoders.stdout
        if not has_codec:
            _nvenc_available_cache = False
            return _nvenc_available_cache
        gpu = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=5)
        _nvenc_available_cache = gpu.returncode == 0 and "GPU" in gpu.stdout
    except (OSError, subprocess.TimeoutExpired):
        _nvenc_available_cache = False
    return _nvenc_available_cache


def build_ffmpeg_cmd(rtmp_url, stream_key, resolution, display,
                     capture_resolution=None, use_gpu=None):
    """Build the ffmpeg broadcaster command.

    Contract E (docs/tuber_base_layout_plan.md): `capture_resolution` is what
    Xvfb/xterm actually render at (x11grab's `-video_size`), while
    `resolution` remains the STREAM OUTPUT size, unchanged in meaning. When
    `capture_resolution` is omitted or equal to `resolution`, no scale
    step is added and the command is byte-for-byte identical to the
    single-resolution behavior that existed before capture/output were
    split — so nobody who hasn't opted into a larger capture size sees any
    change. When they differ, a scale filter is inserted before the
    encode to scale the larger capture down (or up) to the stream's
    output resolution.

    `use_gpu`: None (default) auto-detects via nvenc_available() — real
    GPU hardware encoding whenever it's actually usable on this host,
    software libx264 otherwise, so a host without a GPU/NVENC keeps
    working exactly as before this parameter existed. Pass True/False to
    force one path — mainly for tests, but also available as
    avatar.stream.gpu_encode in worker config for an explicit override
    (e.g. temporarily forcing software if a specific host's NVENC turns
    out to be flaky).

    x264 and NVENC take almost entirely different flag sets (there is no
    single -preset/-tune value that means the same thing to both
    encoders), so this branches on the whole encode+scale tail rather
    than trying to parameterize one shared list — same -b:v/-maxrate/
    -bufsize/-g target either way, so stream bitrate and GOP behavior
    are unchanged, only which silicon does the work.
    """
    if capture_resolution is None:
        capture_resolution = resolution
    if use_gpu is None:
        use_gpu = nvenc_available()

    # Real audio (the PulseAudio null sink narration/audio_player.py plays
    # into) when Pulse is actually up; otherwise a synthesized silent track
    # so the flv/aac muxer still gets an audio stream and the broadcast
    # itself never fails over what should only ever mute the narration.
    if pulse_monitor_available():
        audio_input = ["-thread_queue_size", "1024", "-f", "pulse", "-i", "vout.monitor"]
    else:
        log("WARNING: PulseAudio vout.monitor not found — streaming silent audio")
        audio_input = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    needs_scale = capture_resolution != resolution
    output_w, output_h = resolution.split("x", 1)

    if use_gpu:
        # format=nv12 before hwupload_cuda: scale_cuda/nvenc need a pixel
        # format they can actually upload to the GPU — the raw x11grab
        # capture comes out as bgr0, which neither accepts directly
        # (confirmed on gx10: omitting this raises "Impossible to
        # convert between the formats supported by the filter"). This
        # conversion IS still a CPU step either way — small relative to
        # the scale+encode work it unblocks moving to the GPU.
        scale_filter = (
            ["-vf", f"format=nv12,hwupload_cuda,scale_cuda={output_w}:{output_h}"]
            if needs_scale else
            ["-vf", "format=nv12,hwupload_cuda"]
        )
        encode_args = [
            "-c:v", "h264_nvenc",
            "-preset", "p1",       # NVENC's fastest preset — matches
                                   # libx264 "veryfast"'s speed-over-
                                   # quality tradeoff for a live stream
            "-tune", "ll",         # low-latency, NVENC's rough
                                   # equivalent of x264's "zerolatency"
            "-rc", "cbr",          # true constant bitrate — see the CBR
                                   # note above the "if use_gpu:" branch;
                                   # NVENC's default rc mode is VBR, "-b:v"
                                   # alone does NOT switch it to CBR
            "-b:v", f"{TWITCH_BITRATE_KBPS}k",
            "-maxrate", f"{TWITCH_BITRATE_KBPS}k",
            "-bufsize", f"{TWITCH_BITRATE_KBPS}k",
            "-g", str(TWITCH_KEYFRAME_INTERVAL_FRAMES),
        ]
    else:
        scale_filter = (["-vf", f"scale={output_w}:{output_h}"]
                        if needs_scale else [])
        encode_args = [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            # True CBR, not just a capped VBR: -b:v/-maxrate/-bufsize
            # alone let x264 vary output bitrate frame-to-frame (lower
            # during static scenes, spiking on motion) — exactly the
            # "broadcast starvation" pattern Twitch's own broadcasting
            # guidelines (docs/twitch_broadcasting_guidelines.md) warn
            # against, and consistent with what this session's stream
            # stats showed: Download Bitrate 566Kbps against an 84Mbps
            # Bandwidth Estimate, Buffer Size 6.72s, Latency 11.36s — a
            # bursty encoder output the player buffers heavily around,
            # which reads as choppy/low-fps playback even when frames
            # are actually being produced upstream. nal-hrd=cbr forces
            # x264 to actually pad output to a constant rate; force-cfr
            # 1 (below) is required alongside it or libx264 refuses/
            # ignores nal-hrd=cbr on a variable-frame-rate input (x11grab
            # is nominally constant-fps, but not guaranteed frame-exact).
            "-x264opts", "nal-hrd=cbr:force-cfr=1",
            "-b:v", f"{TWITCH_BITRATE_KBPS}k",
            "-maxrate", f"{TWITCH_BITRATE_KBPS}k",
            "-bufsize", f"{TWITCH_BITRATE_KBPS}k",
            "-pix_fmt", "yuv420p",
            "-g", str(TWITCH_KEYFRAME_INTERVAL_FRAMES),
        ]

    return [
        "ffmpeg",
        "-thread_queue_size", "1024",
        "-f", "x11grab",
        "-video_size", capture_resolution,
        "-framerate", "30",
        "-i", display,
        *audio_input,
        *scale_filter,
        *encode_args,
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-f", "flv",
        f"{rtmp_url}/{stream_key}",
    ]


def decide_action(enabled, proc_running):
    """Pure decision table — kept separate from Popen/signal plumbing so it's
    unit-testable without spawning real processes."""
    if enabled and not proc_running:
        return "start"
    if not enabled and proc_running:
        return "stop"
    return "noop"


def stop_process(proc):
    proc.terminate()
    try:
        proc.wait(timeout=STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/worker.yaml")
    parser.add_argument("--rtmp-url", required=True)
    parser.add_argument("--stream-key", required=True)
    parser.add_argument("--resolution", required=True)
    parser.add_argument(
        "--capture-resolution",
        default=None,
        help=(
            "Resolution Xvfb/xterm actually render at (x11grab -video_size). "
            "Defaults to --resolution when omitted, so single-resolution "
            "behavior is unchanged unless this is set (docs/tuber_base_layout_plan.md Contract E)."
        ),
    )
    parser.add_argument("--display", required=True)
    args = parser.parse_args()

    capture_resolution = args.capture_resolution or args.resolution

    config = load_worker_config(args.config)
    bus_config = config.get("message_bus", {})
    worker_id = resolve("WORKER_ID", bus_config.get("worker_id"), "worker")
    control = WorkerControl.from_config(config)
    ffmpeg_cmd = build_ffmpeg_cmd(
        args.rtmp_url, args.stream_key, args.resolution, args.display,
        capture_resolution=capture_resolution,
    )

    log(redact_stream_key(f"{worker_id} supervising ffmpeg -> {args.rtmp_url}/{args.stream_key}"))

    proc = None
    running = True

    def handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while running:
        if proc is not None and proc.poll() is not None:
            log(f"ffmpeg exited unexpectedly (code {proc.returncode})")
            proc = None

        enabled = control.is_enabled(worker_id)
        action = decide_action(enabled, proc is not None)

        if action == "start":
            log("starting ffmpeg broadcaster")
            proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif action == "stop":
            log("worker disabled: stopping ffmpeg broadcaster")
            stop_process(proc)
            proc = None

        time.sleep(POLL_INTERVAL_S)

    log("shutting down")
    if proc is not None:
        stop_process(proc)


if __name__ == "__main__":
    sys.exit(main())
