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

Emergency stop that works with Redis down: WorkerControl's local kill file
(WORKER_KILL_FILE) wins over Redis, is checked every KILL_CHECK_INTERVAL_S
between polls, and SIGUSR1 to this process writes it and stops ffmpeg
immediately (scripts/emergency_stop.sh).
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
#: Between full polls the supervisor wakes this often to stat the local kill
#: file (cheap, no Redis) so an emergency stop takes <1s rather than a full
#: POLL_INTERVAL_S. Only the kill file / SIGUSR1 flag are checked here.
KILL_CHECK_INTERVAL_S = 0.5

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

#: Per-input capture queue sizing. `-thread_queue_size` is counted in
#: PACKETS, and for a raw x11grab input one packet is one uncompressed
#: frame — at 1920x1080 BGR0 that is 1920*1080*4 == ~8.3 MB EACH (it was
#: ~14.7 MB at the old 2560x1440 capture size). A flat
#: `-thread_queue_size 1024` on the video input therefore authorized
#: 8-15 GB of shared-memory frame buffer per ffmpeg, and because the
#: software encoder on this host can't drain 30fps at these sizes the
#: producer always runs ahead and the queue actually fills to the cap.
#: With 7 stream workers that reached ~50 GB and drove the host into
#: repeated global OOM kills (ffmpeg dying with shmem-rss ~7 GB, taking
#: pipewire, dbus, the user systemd and every worker container with it).
#:
#: So the video queue is sized by BYTES, not by a frame count: hold at
#: most VIDEO_QUEUE_BUDGET_BYTES of in-flight frames whatever the capture
#: resolution is. Audio is unaffected — pulse packets are ~KB, so 1024 of
#: them is a rounding error and keeps the original dropped-frame fix.
VIDEO_QUEUE_BUDGET_BYTES = 512 * 1024 * 1024
VIDEO_QUEUE_MIN_FRAMES = 8     # ffmpeg's own default; never go below it
VIDEO_QUEUE_MAX_FRAMES = 256
AUDIO_QUEUE_PACKETS = 1024


def video_thread_queue_size(capture_resolution):
    """Frames to allow in the x11grab input queue at this capture size.

    Bounded so the queue can never cost more than VIDEO_QUEUE_BUDGET_BYTES
    of resident frame buffer per ffmpeg process, which is what stops N
    concurrent stream workers from OOM-killing the host.
    """
    try:
        width, height = (int(v) for v in capture_resolution.split("x", 1))
        frame_bytes = width * height * 4  # x11grab captures BGR0, 4 bytes/px
        frames = VIDEO_QUEUE_BUDGET_BYTES // frame_bytes
    except (ValueError, ZeroDivisionError):
        # Unparseable resolution: fall back to the floor rather than
        # guessing large — a small queue drops frames, a large one
        # takes the host down.
        frames = VIDEO_QUEUE_MIN_FRAMES
    return max(VIDEO_QUEUE_MIN_FRAMES, min(VIDEO_QUEUE_MAX_FRAMES, frames))



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


def music_filter_graph(duck=None, music_gain=1.0):
    """filter_complex that mixes the music bed (input 2) under the voices
    (input 1), ducked by a sidechain compressor keyed on the voices.

    Voices are split: one copy goes straight to the mix untouched, the other
    is only the compressor's KEY. So speech is never altered; the music
    drops by `ratio` whenever the voice level crosses `threshold`, and
    recovers over `release_ms` after the line ends. amix normalize=0 keeps
    the voices at exactly their current level (the default normalize would
    halve them just because a second input exists).
    """
    d = {"threshold": 0.02, "ratio": 8, "attack_ms": 20, "release_ms": 600}
    d.update({k: v for k, v in (duck or {}).items() if k in d})
    # Both legs MUST be forced to one rate/format/layout: vout runs at 48 kHz
    # and the music sink at 44.1 kHz, and sidechaincompress refuses mismatched
    # inputs ("could not choose their formats") — which crash-looped the live
    # broadcaster on first deploy. 44.1 kHz matches the AAC output (-ar 44100).
    fmt = ("aresample=44100:async=1,"
           "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo")
    return (
        f"[1:a]{fmt},asplit=2[voice][key];"
        f"[2:a]{fmt},volume={float(music_gain):.3f}[bed];"
        f"[bed][key]sidechaincompress=threshold={float(d['threshold'])}"
        f":ratio={float(d['ratio'])}:attack={float(d['attack_ms'])}"
        f":release={float(d['release_ms'])}[ducked];"
        "[voice][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )


def resolve_music_source(config, sink="music"):
    """(pulse_source, duck_config) when this worker has background music
    enabled (`music.enabled`, overridable by MUSIC_ENABLED env — same rule
    as music_director.music_config) AND the music sink actually exists;
    (None, None) otherwise, which leaves the ffmpeg command unchanged."""
    section = dict((config or {}).get("music") or {})
    env = os.environ.get("MUSIC_ENABLED")
    enabled = (env.strip().lower() in ("1", "true", "yes", "on")
               if env is not None and env.strip() else bool(section.get("enabled")))
    if not enabled:
        return None, None
    sink = os.environ.get("MUSIC_SINK") or section.get("sink") or sink
    if not pulse_monitor_available(sink):
        log(f"WARNING: music enabled but Pulse source {sink}.monitor missing — streaming voices only")
        return None, None
    return f"{sink}.monitor", section.get("duck") or {}


def build_ffmpeg_cmd(rtmp_url, stream_key, resolution, display,
                     capture_resolution=None, use_gpu=None,
                     local_preview_url=None, music_source=None, music_duck=None,
                     record_tap_url=None):
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

    `local_preview_url`: None (default) keeps single-output behavior
    byte-identical to before this parameter existed — one `-f flv
    <rtmp_url>/<stream_key>` output, nothing else. When set (opt-in via
    LOCAL_PREVIEW_ENABLED, see startup.sh), the already-encoded output is
    duplicated to a second RTMP destination via ffmpeg's `tee` muxer
    instead of re-encoding — near-zero extra CPU cost, since it's just
    copying encoded packets to a second socket. Only the LOCAL leg is
    tagged `onfail=ignore` (see the inline comment in the tee branch for
    why the Twitch leg deliberately is NOT) — a stall/disconnect on the
    local preview (e.g. a VLC viewer closing, or rtmp-preview restarting)
    can't affect the real Twitch broadcast, but a genuine Twitch failure
    still fails the whole process and gets picked up by
    stream_supervisor's normal restart loop, same as single-output mode
    always has. Exists so a local rtmp-preview viewer (VLC) can watch
    with near-zero latency without interrupting the live Twitch broadcast
    (the whole point being to skip Twitch's ~30s CDN delay for debugging).

    x264 and NVENC take almost entirely different flag sets (there is no
    single -preset/-tune value that means the same thing to both
    encoders), so this branches on the whole encode+scale tail rather
    than trying to parameterize one shared list — same -b:v/-maxrate/
    -bufsize/-g target either way, so stream bitrate and GOP behavior
    are unchanged, only which silicon does the work.
    `music_source`: None (default) keeps the command byte-identical to
    before background music existed. When set (e.g. "music.monitor", only
    on the roundtable — see main()), that Pulse source becomes input 2 and
    a filter_complex (music_filter_graph) ducks it under the voices; the
    mixed `[aout]` label replaces input 1's audio in every -map. Ignored
    when Pulse itself is down (the anullsrc fallback has nothing to duck).
    `music_duck` is the worker config's `music.duck` dict.

    `record_tap_url`: None (default) keeps the command unchanged. When set
    (RECORDING_TAP_ENABLED, on by default in startup.sh; e.g.
    udp://127.0.0.1:23000), the already-encoded packets are ALSO tee'd as
    MPEG-TS to that local UDP port, where app/stream_recorder.py picks them
    up with `-c copy` only while a recorded replay is airing
    (docs/stream_recorder.md). Same zero-re-encode tee as the local
    preview, and the same `onfail=ignore` rule for the non-Twitch leg; UDP
    to a port nobody is listening on just drops, so the tap costs one
    loopback datagram copy per packet and never blocks the broadcast.
    """
    if capture_resolution is None:
        capture_resolution = resolution
    if use_gpu is None:
        use_gpu = nvenc_available()

    # Real audio (the PulseAudio null sink narration/audio_player.py plays
    # into) when Pulse is actually up; otherwise a synthesized silent track
    # so the flv/aac muxer still gets an audio stream and the broadcast
    # itself never fails over what should only ever mute the narration.
    music_args, audio_map = [], "1:a:0"
    if pulse_monitor_available():
        audio_input = ["-thread_queue_size", str(AUDIO_QUEUE_PACKETS),
                       "-f", "pulse", "-i", "vout.monitor"]
        if music_source:
            music_args = ["-thread_queue_size", str(AUDIO_QUEUE_PACKETS),
                          "-f", "pulse", "-i", music_source,
                          "-filter_complex", music_filter_graph(music_duck)]
            audio_map = "[aout]"
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

    primary_output = f"{rtmp_url}/{stream_key}"

    tee_legs = []
    if local_preview_url:
        tee_legs.append(f"[f=flv:onfail=ignore]{local_preview_url}/{stream_key}")
    if record_tap_url:
        # pkt_size=1316 == 7 TS packets: the standard MPEG-TS-over-UDP
        # datagram size, well under the loopback MTU.
        sep = "&" if "?" in record_tap_url else "?"
        tee_legs.append(f"[f=mpegts:onfail=ignore]{record_tap_url}{sep}pkt_size=1316")

    if tee_legs:
        # tee muxer duplicates the already-encoded packets to a second
        # destination — no second encode, so no meaningful extra CPU cost
        # (see local_preview_url's docstring above). onfail=ignore ONLY on
        # the local leg — a local VLC viewer disconnecting or the
        # rtmp-preview container hiccuping must never take down the real
        # broadcast. The Twitch leg deliberately has NO onfail=ignore:
        # ffmpeg's tee muxer treats onfail=ignore as PERMANENT for that
        # slave once it fails once (a single write stall/error, e.g. from
        # a CPU-throttled moment, drops it for the rest of the process's
        # life — tee does not retry, so -reconnect/-reconnect_streamed
        # never get a chance to kick in). Confirmed live: with
        # onfail=ignore on both legs, Twitch silently went offline for the
        # rest of the run while the local leg kept working. Leaving Twitch
        # WITHOUT onfail means a Twitch failure now fails the whole ffmpeg
        # process instead, which stream_supervisor.py's own poll loop
        # already restarts (decide_action) — the exact same recovery path
        # single-output mode has always relied on, so this doesn't
        # introduce a new failure mode, only avoids a NEW one from tee.
        tee_spec = "|".join([f"[f=flv]{primary_output}", *tee_legs])
        # Map by explicit INPUT INDEX + stream type: video is always input 0
        # (the x11grab -i above); audio is always input 1, whichever branch
        # of audio_input supplied it (pulse or anullsrc) — both are single
        # -f/-i pairs immediately after the video input, so "1:a:0" is
        # correct either way. A bare "0:a" (or the unprefixed "a:0" form)
        # fails ("Stream map '0:a'/'a:0' matches no streams") because input
        # 0 (the video capture) has no audio stream at all — confirmed live
        # on gx10, ffmpeg only resolves stream-type-only map specs within a
        # SINGLE named input, not across every -i.
        output_args = [
            "-map", "0:v:0", "-map", audio_map,
            "-f", "tee",
            tee_spec,
        ]
    elif music_args:
        # A labelled filter_complex output must be mapped explicitly (an
        # unmapped [aout] is an "unconnected output" error), and once any
        # -map is given ffmpeg stops auto-selecting video too.
        output_args = ["-map", "0:v:0", "-map", audio_map, "-f", "flv", primary_output]
    else:
        output_args = ["-f", "flv", primary_output]

    return [
        "ffmpeg",
        "-thread_queue_size", str(video_thread_queue_size(capture_resolution)),
        "-f", "x11grab",
        "-video_size", capture_resolution,
        "-framerate", "30",
        "-i", display,
        *audio_input,
        *music_args,
        *scale_filter,
        *encode_args,
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        *output_args,
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


def supervise_step(control, worker_id, proc, ffmpeg_cmd, force_off=False):
    """One poll: reap a dead ffmpeg, ask WorkerControl (kill file first, then
    Redis) whether we should be live, and start/stop ffmpeg accordingly.
    Returns the (possibly new/None) process. `force_off` is the in-memory
    fallback for a SIGUSR1 whose kill-file write failed."""
    if proc is not None and proc.poll() is not None:
        log(f"ffmpeg exited unexpectedly (code {proc.returncode})")
        proc = None

    # is_enabled() already checks the kill file before Redis; the separate
    # local_override_active() call only decides which reason gets logged.
    enabled = False if force_off else control.is_enabled(worker_id)
    override = force_off or (not enabled and control.local_override_active())
    action = decide_action(enabled, proc is not None)

    if action == "start":
        log("starting ffmpeg broadcaster")
        proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif action == "stop":
        if override:
            log(f"WARN event=stop_ffmpeg reason=local_override worker_id={worker_id} "
                f"kill_file={control.kill_file} force_off={force_off}")
        else:
            log("worker disabled: stopping ffmpeg broadcaster")
        stop_process(proc)
        proc = None
    return proc


def wait_for_next_poll(control, proc, should_wake, sleep=time.sleep,
                       interval=POLL_INTERVAL_S, slice_s=KILL_CHECK_INTERVAL_S):
    """Sleep up to `interval`, returning early when a live ffmpeg should be
    stopped NOW (kill file appeared, or should_wake() — SIGUSR1/shutdown).
    Python retries time.sleep after a signal handler (PEP 475), so the
    handler alone can't cut a 3s sleep short; slicing it does."""
    waited = 0.0
    while waited < interval:
        if should_wake():
            return True
        if proc is not None and control.local_override_active():
            return True
        sleep(slice_s)
        waited += slice_s
    return False


def make_emergency_stop_handler(control, state):
    """SIGUSR1: "stop streaming now and write the kill file". The file makes
    it stick (and stops agent.py too); if it can't be written, state
    ["force_off"] keeps ffmpeg off in this process regardless."""
    def handle_emergency_stop(signum, frame):
        state["wake"] = True
        try:
            control.engage_local_override(reason="SIGUSR1")
        except OSError as exc:
            state["force_off"] = True
            log(f"ERROR event=kill_file_write_failed kill_file={control.kill_file} "
                f"error={exc!r} fallback=in_memory_force_off")
    return handle_emergency_stop


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
    parser.add_argument(
        "--local-preview-url",
        default=None,
        help=(
            "Optional second RTMP destination (e.g. rtmp://rtmp-preview:1935/live) "
            "the encoded stream is also tee'd to, for near-zero-latency local "
            "viewing (VLC) alongside the normal Twitch broadcast. Omitted by "
            "default (opt-in via LOCAL_PREVIEW_ENABLED, see startup.sh) so "
            "existing single-output behavior is unchanged unless set."
        ),
    )
    parser.add_argument(
        "--record-tap-url",
        default=None,
        help=(
            "Optional local UDP address (e.g. udp://127.0.0.1:23000) the encoded "
            "stream is also tee'd to as MPEG-TS, for app/stream_recorder.py to save "
            "recorded replay airings from (docs/stream_recorder.md). Set by "
            "startup.sh unless RECORDING_TAP_ENABLED=0; omitted = command unchanged."
        ),
    )
    args = parser.parse_args()

    capture_resolution = args.capture_resolution or args.resolution

    config = load_worker_config(args.config)
    bus_config = config.get("message_bus", {})
    worker_id = resolve("WORKER_ID", bus_config.get("worker_id"), "worker")
    control = WorkerControl.from_config(config)
    music_source, music_duck = resolve_music_source(config)
    ffmpeg_cmd = build_ffmpeg_cmd(
        args.rtmp_url, args.stream_key, args.resolution, args.display,
        capture_resolution=capture_resolution,
        local_preview_url=args.local_preview_url,
        music_source=music_source, music_duck=music_duck,
        record_tap_url=args.record_tap_url,
    )
    if music_source:
        log(f"{worker_id} mixing background music from {music_source} (ducked under voices)")

    log(redact_stream_key(f"{worker_id} supervising ffmpeg -> {args.rtmp_url}/{args.stream_key}"))
    if args.local_preview_url:
        log(f"{worker_id} also tee'ing to local preview -> {args.local_preview_url}/{args.stream_key}")
    if args.record_tap_url:
        log(f"{worker_id} recording tap enabled -> {args.record_tap_url} (mpegts, used only while a recorded replay airs)")

    proc = None
    state = {"running": True, "wake": False, "force_off": False}

    def handle_signal(signum, frame):
        state["running"] = False
        state["wake"] = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGUSR1, make_emergency_stop_handler(control, state))

    if control.local_override_active():
        log(f"WARN event=local_override_active_at_startup worker_id={worker_id} "
            f"kill_file={control.kill_file} ffmpeg=not_started")

    while state["running"]:
        state["wake"] = False
        proc = supervise_step(control, worker_id, proc, ffmpeg_cmd,
                              force_off=state["force_off"])
        wait_for_next_poll(control, proc, lambda: state["wake"])

    log("shutting down")
    if proc is not None:
        stop_process(proc)


if __name__ == "__main__":
    sys.exit(main())
