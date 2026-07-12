"""
audio_player.py
Best-effort, non-blocking WAV playback for the replay narration layer.

Inside a worker container, `paplay` plays into the PulseAudio sink
(PULSE_SINK=vout) that ffmpeg captures — so anything played here is what the
Twitch stream hears. On a dev box without paplay it falls back to ffplay,
then aplay, then to silence.

Every failure mode is soft: no player found, a player that errors, a missing
file — all yield a playback object whose waits return immediately. Audio
must never take the show down (same contract as agent_state writes in
replay.py).
"""
import shutil
import subprocess
import sys
import time


def _find_player(path):
    """First available player command for `path`, or None."""
    if shutil.which("paplay"):
        return ["paplay", str(path)]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)]
    if shutil.which("aplay"):
        return ["aplay", "-q", str(path)]
    return None


class Playback:
    """Handle for one in-flight audio playback. `proc` may be None (silent)."""

    def __init__(self, proc=None):
        self.proc = proc

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def wait(self, timeout=None):
        """Block until playback finishes (or `timeout` seconds pass)."""
        if self.proc is None:
            return
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.stop()

    def stop(self):
        if self.running:
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass


_SILENT = Playback(None)


def play_wav(path, out=sys.stderr):
    """Start playing a WAV without blocking; always returns a Playback.

    A missing player or a spawn failure returns a silent (already-finished)
    Playback and notes it on `out` — the caller's timing loop needs no
    special cases.
    """
    command = _find_player(path)
    if command is None:
        print("[audio] no player available (paplay/ffplay/aplay) — silent show",
              file=out)
        return _SILENT
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        print(f"[audio] playback failed to start: {exc}", file=out)
        return _SILENT
    return Playback(proc)


def wait_extra(playback, started_at, min_seconds):
    """Hold the scene until `min_seconds` have elapsed since `started_at`
    AND playback has finished — whichever is later. Used when the visuals
    finish before the spoken line does."""
    remaining = min_seconds - (time.monotonic() - started_at)
    if remaining > 0:
        playback.wait(timeout=remaining)
        remaining = min_seconds - (time.monotonic() - started_at)
        if remaining > 0:
            time.sleep(remaining)
    playback.wait(timeout=10)  # grace: let a slightly-long line finish
