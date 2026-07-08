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

from message_bus import load_worker_config
from worker_control import WorkerControl

POLL_INTERVAL_S = 3
STOP_TIMEOUT_S = 10


def resolve(env_name, config_value, default=None):
    return os.environ.get(env_name) or config_value or default


def log(msg):
    print(f"[stream_supervisor] {msg}", flush=True)


def build_ffmpeg_cmd(rtmp_url, stream_key, resolution, display):
    return [
        "ffmpeg",
        "-f", "x11grab",
        "-video_size", resolution,
        "-framerate", "30",
        "-i", display,
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-b:v", "3000k",
        "-maxrate", "3000k",
        "-bufsize", "6000k",
        "-pix_fmt", "yuv420p",
        "-g", "60",
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
    parser.add_argument("--display", required=True)
    args = parser.parse_args()

    config = load_worker_config(args.config)
    bus_config = config.get("message_bus", {})
    worker_id = resolve("WORKER_ID", bus_config.get("worker_id"), "worker")
    control = WorkerControl.from_config(config)
    ffmpeg_cmd = build_ffmpeg_cmd(args.rtmp_url, args.stream_key, args.resolution, args.display)

    log(f"{worker_id} supervising ffmpeg -> {args.rtmp_url}/{args.stream_key}")

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
            proc = subprocess.Popen(ffmpeg_cmd)
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
