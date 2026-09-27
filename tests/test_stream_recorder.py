"""Tests for app/stream_recorder.py — the per-worker recorder that stream-
copies the broadcaster's UDP tap to disk during a recorded airing.
subprocess.Popen is faked; tests/test_stream_recorder_ffmpeg.py-style real
ffmpeg runs were done on the worker image (see docs/stream_recorder.md)."""
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import recording_budget as rb  # noqa: E402
import stream_recorder as sr  # noqa: E402


class FakeProc:
    """Writes `writes` bytes to the output file when started (unless
    exit_code is set, which makes it die immediately)."""

    def __init__(self, cmd, output, writes=1000, exit_code=None, ignore_sigint=False, **_):
        self.cmd = cmd
        self.output = Path(output)
        self.returncode = exit_code
        self.signals = []
        self.killed = False
        self.ignore_sigint = ignore_sigint
        if exit_code is None and writes:
            self.output.write_bytes(b"x" * writes)

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)
        if not self.ignore_sigint:
            self.returncode = 0

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def _popen_factory(**proc_kwargs):
    made = []

    def popen(cmd, **kwargs):
        proc = FakeProc(cmd, cmd[-1], **proc_kwargs)
        made.append(proc)
        return proc
    popen.made = made
    return popen


@pytest.fixture
def reserved(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    root = tmp_path / "recordings"
    d = rb.reserve(root, "ep1", {"events": []}, ["roundtable", "coder"], 5 * 1000 ** 3)
    return root, d


def _recorder(reserved, worker="roundtable", popen=None, max_bytes=None, clock=None):
    root, d = reserved
    ticks = iter(range(10_000))
    return sr.StreamRecorder(d["recording_id"], max_bytes or d["max_bytes_per_stream"], worker,
                             tap_url="udp://127.0.0.1:23000", root=root,
                             popen=popen or _popen_factory(), sleep=lambda s: None,
                             clock=clock or (lambda: next(ticks) * 0.1))


# ── request parsing ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("record", [
    None, "yes", {}, {"recording_id": "x"}, {"max_bytes": 5},
    {"recording_id": "x", "max_bytes": 0}, {"recording_id": "x", "max_bytes": "lots"},
    {"recording_id": "..", "max_bytes": 5},
])
def test_parse_record_request_rejects_anything_without_id_and_positive_cap(record):
    assert sr.parse_record_request(record) is None


def test_parse_record_request_sanitizes_id():
    assert sr.parse_record_request({"recording_id": "../x", "max_bytes": "10"}) == ("x", 10)


# ── command ──────────────────────────────────────────────────────────────────
def test_recorder_cmd_is_a_capped_stream_copy_to_fragmented_mp4(tmp_path):
    cmd = sr.build_recorder_cmd("udp://127.0.0.1:23000", tmp_path / "o.mp4", 123)
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[cmd.index("-fs") + 1] == str(sr.effective_fs_limit(123))
    assert cmd[cmd.index("-bsf:a") + 1] == "aac_adtstoasc"  # ADTS (TS) -> MP4
    assert "frag_keyframe" in cmd[cmd.index("-movflags") + 1]
    src = cmd[cmd.index("-i") + 1]
    assert src.startswith("udp://127.0.0.1:23000?") and "timeout=" in src
    assert cmd[-1] == str(tmp_path / "o.mp4")


@pytest.mark.parametrize("cap", [1, 2, 1000, 5_000_000, 27_334_125, 5 * 1000 ** 3])
def test_effective_fs_limit_leaves_room_for_one_fragment_overshoot(cap):
    """ffmpeg's -fs overshoots by up to a fragment (a GOP); the limit it's
    given must sit below the reserved cap so the file never exceeds it."""
    limit = sr.effective_fs_limit(cap)
    assert 1 <= limit <= cap
    if cap >= 2 * sr.FS_MARGIN_BYTES:
        assert cap - limit == sr.FS_MARGIN_BYTES


# ── lifecycle ────────────────────────────────────────────────────────────────
def test_start_and_stop_writes_file_and_done_marker(reserved):
    popen = _popen_factory(writes=5000)
    rec = _recorder(reserved, popen=popen)
    assert rec.start()
    assert rec.stop() == 5000
    assert popen.made[0].signals == [signal.SIGINT]  # finalize, not kill
    assert rec.done_path.exists()
    root, d = reserved
    assert rec.path == root / d["recording_id"] / "roundtable.mp4"


def test_no_reservation_means_no_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    popen = _popen_factory()
    rec = sr.StreamRecorder("20260101T000000Z_x_abcdef", 1000, "roundtable",
                            root=tmp_path / "rec", popen=popen, sleep=lambda s: None)
    assert rec.start() is False
    assert popen.made == []


def test_stream_not_in_reservation_is_not_recorded(reserved):
    """A duet follower handed the director's `record` must not record —
    its stream wasn't in the size estimate."""
    popen = _popen_factory()
    rec = _recorder(reserved, worker="tester", popen=popen)
    assert rec.start() is False
    assert popen.made == []


def test_cap_is_clamped_to_the_reservation(reserved):
    root, d = reserved
    popen = _popen_factory()
    rec = _recorder(reserved, popen=popen, max_bytes=d["max_bytes_per_stream"] * 100)
    rec.start()
    cmd = popen.made[0].cmd
    assert int(cmd[cmd.index("-fs") + 1]) == sr.effective_fs_limit(d["max_bytes_per_stream"])
    rec.stop()


def test_recorder_that_dies_immediately_reports_failure_and_marks_done(reserved):
    rec = _recorder(reserved, popen=_popen_factory(exit_code=1))
    assert rec.start() is False
    assert rec.done_path.exists()
    assert not rec.path.exists()


def test_popen_oserror_reports_failure(reserved):
    def boom(cmd, **kwargs):
        raise OSError("no ffmpeg")
    rec = _recorder(reserved, popen=boom)
    assert rec.start() is False
    assert rec.done_path.exists()


def test_stop_kills_recorder_that_ignores_sigint(reserved):
    popen = _popen_factory(ignore_sigint=True)
    rec = _recorder(reserved, popen=popen)
    rec.start()
    rec.stop()
    assert popen.made[0].killed


def test_empty_recording_is_discarded(reserved):
    rec = _recorder(reserved, popen=_popen_factory(writes=0))
    assert rec.start() is True  # alive, just no bytes yet — kept
    assert rec.stop() == 0
    assert not rec.path.exists()
    assert rec.done_path.exists()


def test_size_cap_reached_is_logged(reserved, capsys):
    rec = _recorder(reserved, popen=_popen_factory(writes=1000), max_bytes=1000)
    rec.start()
    rec.stop()
    assert "size_cap_reached" in capsys.readouterr().out


# ── context manager ──────────────────────────────────────────────────────────
def test_recording_context_is_noop_without_record():
    with sr.recording(None, "roundtable") as rec:
        assert rec is None


def test_recording_context_logs_invalid_record_and_airs_unrecorded(capsys):
    with sr.recording({"recording_id": "x"}, "roundtable") as rec:
        assert rec is None
    assert "invalid_record_request" in capsys.readouterr().out


def test_recording_context_stops_recorder_even_when_show_raises(reserved):
    root, d = reserved
    popen = _popen_factory()
    record = {"recording_id": d["recording_id"], "max_bytes": d["max_bytes_per_stream"]}
    with pytest.raises(RuntimeError):
        with sr.recording(record, "roundtable", root=root, popen=popen, sleep=lambda s: None):
            raise RuntimeError("show crashed")
    assert popen.made[0].signals == [signal.SIGINT]


def test_both_streams_done_releases_reservation(reserved):
    root, d = reserved
    for worker in ("roundtable", "coder"):
        rec = _recorder(reserved, worker=worker, popen=_popen_factory(writes=100))
        rec.start()
        rec.stop()
    status = rb.recording_status(root / d["recording_id"])
    assert not status["live"]
    assert rb.budget_usage(root) == status["bytes"]
