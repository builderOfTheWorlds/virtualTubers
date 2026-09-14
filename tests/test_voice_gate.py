"""Tests for app/voice_gate.py — the cross-process "one voice at a time"
semaphore (docs/voice_gate.md).

The gate is a counting lock built on fcntl.flock over N seat files, so the
tests use threads (which share the process but exercise the exact flock
code path) plus one real second-process case for the cross-process
guarantee the roundtable relies on: a seat held by process A must block
process B.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import voice_gate  # noqa: E402


# ── basic serialization (1 seat, the default) ────────────────────────────────
def test_single_seat_serializes_two_holders(tmp_path):
    gate = voice_gate.VoiceGate(tmp_path, seats=1, tag="t1")
    first = gate.acquire()
    assert first is not None and first.seat == 0

    # While the first holder holds seat 0, a second acquire must time out
    # instead of overlapping — the core no-overlap guarantee.
    blocked = voice_gate.VoiceGate(tmp_path, seats=1,
                                   acquire_timeout_s=0.2, tag="t2")
    assert blocked.acquire() is None

    first.release()

    # After release the seat is free again.
    third = voice_gate.VoiceGate(tmp_path, seats=1, tag="t3")
    assert third.acquire() is not None


def test_two_seats_allow_two_holders_and_block_the_third(tmp_path):
    gate = voice_gate.VoiceGate(tmp_path, seats=2, tag="t1")
    a, b = gate.acquire(), gate.acquire()
    assert a is not None and b is not None
    assert {a.seat, b.seat} == {0, 1}  # distinct seats

    third = voice_gate.VoiceGate(tmp_path, seats=2,
                                 acquire_timeout_s=0.2, tag="t3")
    assert third.acquire() is None

    a.release()
    freed = voice_gate.VoiceGate(tmp_path, seats=2, tag="t4").acquire()
    assert freed is not None
    assert freed.seat not in {b.seat}


def test_release_is_idempotent(tmp_path):
    gate = voice_gate.VoiceGate(tmp_path, seats=1, tag="t1")
    seat = gate.acquire()
    seat.release()
    seat.release()  # second release is a no-op, must not raise


def test_acquired_seat_releases_when_holder_thread_unblocks(tmp_path):
    """A holder that waits out a long line (simulated by holding the seat
    in another thread) frees it for the next line without any cleanup."""
    gate = voice_gate.VoiceGate(tmp_path, seats=1, tag="holder")
    holder = gate.acquire()

    events = []

    def waiter():
        time.sleep(0.3)
        # The holder releases before this runs (main thread), so the seat
        # must be acquirable now — ordering proves the release landed.
        events.append(("waiter_acquire",
                       voice_gate.VoiceGate(tmp_path, seats=1,
                                            tag="w").acquire() is not None))

    t = threading.Thread(target=waiter)
    t.start()
    holder.release()
    t.join()
    assert events == [("waiter_acquire", True)]


def test_gate_survives_a_crashed_holder(tmp_path):
    """The liveness guarantee: a process that holds a seat and DIES must
    not wedge the show — the OS releases the flock, so the next acquire
    succeeds with no manual cleanup. This is the whole reason for using
    flock over a lock-server/lease design."""
    code = f"""
import sys, time
sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / "app")!r})
import voice_gate
g = voice_gate.VoiceGate({str(tmp_path)!r}, seats=1, tag="dying")
s = g.acquire()
assert s is not None, "dying holder could not acquire"
print("HELD", flush=True)
time.sleep(30)  # killed by SIGKILL before release()
"""
    holder = subprocess.Popen([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, text=True)
    assert "HELD" in holder.stdout.readline()

    # Kill -9: no cleanup, no release() — the classic deadlock trap.
    holder.kill()
    holder.wait()

    survivor = voice_gate.VoiceGate(tmp_path, seats=1, tag="survivor",
                                    acquire_timeout_s=2.0)
    assert survivor.acquire() is not None, "crashed holder wedged the gate"


# ── degrade paths (audio must never be withheld) ─────────────────────────────
def test_unusable_gate_dir_degrades_to_none(tmp_path):
    # A file where the parent of the gate dir should be: makedirs fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    gate = voice_gate.VoiceGate(str(blocker / "voice_gate"), seats=1, tag="x")
    assert gate.available() is False
    assert gate.acquire() is None


def test_events_are_recorded_for_reconstruction(tmp_path):
    gate = voice_gate.VoiceGate(tmp_path, seats=1, tag="recorder")
    seat = gate.acquire()
    seat.release()

    events = [json.loads(line) for line in
              (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds == ["acquire", "release"]
    assert events[0]["tag"] == "recorder" and events[0]["seat"] == 0


# ── resolve_voice_gate: the layered config (escape hatch) ───────────────────
def test_resolve_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_GATE_DIR", str(tmp_path))
    for var in ("VOICE_GATE_CONCURRENT", "VOICE_GATE_LINE_GAP_S",
                "VOICE_GATE_ACQUIRE_TIMEOUT_S"):
        monkeypatch.delenv(var, raising=False)
    gate_dir, seats, gap, timeout = voice_gate.resolve_voice_gate(None, None)
    assert (gate_dir, seats, gap, timeout) == (str(tmp_path), 1, 0.0,
                                               voice_gate.DEFAULT_ACQUIRE_TIMEOUT_S)


def test_resolve_env_wins_over_show_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_GATE_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_GATE_CONCURRENT", "3")
    monkeypatch.setenv("VOICE_GATE_LINE_GAP_S", "0.5")
    script = {"show": {"audio": {"max_concurrent": 2, "line_gap_s": 1.0}}}
    config = {"voice": {"audio": {"max_concurrent": 4, "line_gap_s": 2.0}}}
    gate_dir, seats, gap, _ = voice_gate.resolve_voice_gate(script, config)
    assert (seats, gap) == (3, 0.5)  # env > show > config


def test_resolve_show_header_wins_over_config(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_GATE_DIR", str(tmp_path))
    for var in ("VOICE_GATE_CONCURRENT", "VOICE_GATE_LINE_GAP_S"):
        monkeypatch.delenv(var, raising=False)
    script = {"show": {"audio": {"max_concurrent": 2, "line_gap_s": 0.4}}}
    config = {"voice": {"audio": {"max_concurrent": 5}}}
    _, seats, gap, _ = voice_gate.resolve_voice_gate(script, config)
    assert (seats, gap) == (2, 0.4)


def test_resolve_clamps_to_sane_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_GATE_DIR", str(tmp_path))
    script = {"show": {"audio": {"max_concurrent": 99, "line_gap_s": -3,
                                 "acquire_timeout_s": 1.0}}}
    _, seats, gap, timeout = voice_gate.resolve_voice_gate(script, None, roster_size=7)
    assert seats == 7      # clamped to the roster, never absurd
    assert gap == 0.0      # negative clamped to 0
    assert timeout == 5.0  # below the floor, clamped up


def test_resolve_rejects_booleans_and_garbage(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_GATE_DIR", str(tmp_path))
    for var in ("VOICE_GATE_CONCURRENT", "VOICE_GATE_LINE_GAP_S"):
        monkeypatch.delenv(var, raising=False)
    # bool is an int subclass — must be explicitly rejected, not treated as 1.
    script = {"show": {"audio": {"max_concurrent": True, "line_gap_s": "lots"}}}
    _, seats, gap, _ = voice_gate.resolve_voice_gate(script, None)
    assert (seats, gap) == (1, 0.0)  # degraded to defaults
