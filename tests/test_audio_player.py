"""Tests for app/audio_player.py — playback must be best-effort: every
failure path yields a silent playback the caller can wait on safely."""
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import audio_player  # noqa: E402
from audio_player import Playback, play_wav, wait_extra  # noqa: E402


def test_play_wav_without_any_player_is_silent(monkeypatch):
    monkeypatch.setattr(audio_player.shutil, "which", lambda name: None)
    err = io.StringIO()
    playback = play_wav("x.wav", out=err)
    assert not playback.running
    playback.wait()  # returns immediately, no exception
    playback.stop()
    assert "no player" in err.getvalue()


def test_play_wav_spawn_failure_is_silent(monkeypatch):
    monkeypatch.setattr(audio_player.shutil, "which",
                        lambda name: "paplay" if name == "paplay" else None)

    def broken_popen(*args, **kwargs):
        raise OSError("exec failed")

    monkeypatch.setattr(audio_player.subprocess, "Popen", broken_popen)
    err = io.StringIO()
    playback = play_wav("x.wav", out=err)
    assert not playback.running
    assert "failed to start" in err.getvalue()


def test_find_player_prefers_paplay(monkeypatch):
    monkeypatch.setattr(audio_player.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    assert audio_player._find_player("x.wav")[0] == "paplay"


def test_wait_extra_holds_until_min_seconds():
    silent = Playback(None)
    started = time.monotonic()
    wait_extra(silent, started, min_seconds=0.05)
    # Windows timer granularity can undersleep by a few ms — assert the
    # hold happened, not exact wall time.
    assert time.monotonic() - started >= 0.03


def test_wait_extra_returns_fast_when_time_already_elapsed():
    silent = Playback(None)
    started = time.monotonic() - 10  # scene visuals overran the audio long ago
    t0 = time.monotonic()
    wait_extra(silent, started, min_seconds=0.5)
    assert time.monotonic() - t0 < 0.2
