"""Tests for app/recording_budget.py — the 5 GB replay-recording budget:
size estimate, admission, reservations, and the bookkeeping that keeps
concurrent recordings under the cap."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import recording_budget as rb  # noqa: E402
import stream_supervisor  # noqa: E402

GB = 1000 ** 3


def _script(n_lines=10, words=20, tool_calls=0):
    events = []
    for i in range(n_lines):
        events.append({"type": "assistant_text", "text": " ".join(["word"] * words)})
        for _ in range(tool_calls):
            events.append({"type": "tool_call", "tool": "Bash",
                           "detail": {"command": "ls -la", "output": "a\nb\nc"}})
    return {"source": "ep", "events": events}


# ── config ───────────────────────────────────────────────────────────────────
def test_default_limit_is_5gb(monkeypatch):
    monkeypatch.delenv(rb.RECORDINGS_MAX_BYTES_ENV, raising=False)
    assert rb.resolve_max_bytes() == 5 * GB


@pytest.mark.parametrize("raw,expected", [("1000", 1000), ("2e9", 2 * GB),
                                           ("garbage", 5 * GB), ("-5", 5 * GB), ("0", 5 * GB)])
def test_limit_env_override_and_bad_values_fall_back_to_default(monkeypatch, raw, expected):
    monkeypatch.setenv(rb.RECORDINGS_MAX_BYTES_ENV, raw)
    assert rb.resolve_max_bytes() == expected


def test_bitrate_matches_the_broadcaster_it_copies():
    """The recording is a stream copy of stream_supervisor's encode — if
    that bitrate changes, the estimate must change with it."""
    assert rb.STREAM_VIDEO_KBPS == stream_supervisor.TWITCH_BITRATE_KBPS


@pytest.mark.parametrize("raw,expected", [
    ("20260927T120000Z_ep_abc123", "20260927T120000Z_ep_abc123"),
    ("../../etc/passwd", "etc_passwd"),
    ("a/b", "a_b"),
    ("..", ""),
    ("", ""),
    (None, ""),
])
def test_sanitize_recording_id_is_a_single_safe_path_component(raw, expected):
    assert rb.sanitize_recording_id(raw) == expected


# ── estimate ─────────────────────────────────────────────────────────────────
def test_estimate_is_linear_in_streams():
    one = rb.estimate_recording(_script(), streams=1)
    seven = rb.estimate_recording(_script(), streams=7)
    assert seven["bytes_per_stream"] == one["bytes_per_stream"]
    assert seven["total_bytes"] == 7 * one["total_bytes"]


def test_estimate_grows_with_episode_length_and_shrinks_with_speed():
    short = rb.estimate_recording(_script(n_lines=5))["total_bytes"]
    long = rb.estimate_recording(_script(n_lines=50))["total_bytes"]
    assert long > short
    # Speed only shortens the VISUAL side (typing/scrolling); spoken dialogue
    # runs at TTS pace whatever the speed, so it's a tool-heavy episode
    # that gets cheaper when played faster...
    tools = _script(n_lines=5, words=1, tool_calls=8)
    assert (rb.estimate_recording(tools, speed=2.0)["total_bytes"]
            < rb.estimate_recording(tools)["total_bytes"])
    # ...while a dialogue-only one is still bounded by its speech.
    talk = _script(n_lines=50)
    assert (rb.estimate_recording(talk, speed=2.0)["total_bytes"]
            == rb.estimate_recording(talk)["total_bytes"])


def test_estimate_covers_spoken_line_when_it_outlasts_the_visual():
    """400 words take ~9s to type at 45 cps but ~160s to speak — the scene
    is held by the audio, so the estimate must be bounded by speech."""
    scene = {"kind": "coder_talk", "events": [{"type": "assistant_text",
                                              "text": " ".join(["w"] * 400)}]}
    seconds = rb.estimate_scene_seconds(scene)
    assert seconds >= 400 / rb.WORDS_PER_SECOND


def test_estimate_empty_episode_is_just_fixed_overhead():
    est = rb.estimate_recording({"events": []})
    assert est["duration_s"] == pytest.approx(rb.FIXED_OVERHEAD_S)
    assert est["total_bytes"] > 0


def test_estimate_bytes_per_second_is_about_the_stream_bitrate():
    # 4.628 Mbit/s * 1.05 overhead ~ 607 KB/s -> ~2.2 GB/hour per stream
    assert 550_000 < rb.stream_bytes_per_second() < 650_000


def test_estimate_tolerates_bad_speed_and_line_gap():
    script = _script()
    script["show"] = {"audio": {"line_gap_s": "nope"}}
    assert rb.estimate_duration_seconds(script, speed="fast") > 0


# ── admission ────────────────────────────────────────────────────────────────
def _est(total, streams=1, duration=60.0):
    return {"duration_s": duration, "bytes_per_stream": total // streams,
            "streams": streams, "total_bytes": total}


def test_check_budget_allows_when_estimate_fits():
    d = rb.check_budget(_est(1 * GB), used_bytes=1 * GB, limit_bytes=5 * GB)
    assert d["allowed"]
    assert d["remaining_bytes"] == 4 * GB


def test_check_budget_refuses_when_estimate_exceeds_remaining():
    d = rb.check_budget(_est(3 * GB), used_bytes=3 * GB, limit_bytes=5 * GB)
    assert not d["allowed"]
    assert "exceeds" in d["reason"] and "5.00 GB" in d["reason"]


def test_check_budget_per_stream_cap_never_lets_all_streams_exceed_remaining():
    d = rb.check_budget(_est(int(3.9 * GB), streams=7), used_bytes=1 * GB, limit_bytes=5 * GB)
    assert d["allowed"]
    assert d["max_bytes_per_stream"] * 7 <= d["remaining_bytes"]
    assert d["reserved_bytes"] == d["max_bytes_per_stream"] * 7


def test_check_budget_cap_is_estimate_times_headroom_when_space_allows():
    d = rb.check_budget(_est(100_000_000), used_bytes=0, limit_bytes=5 * GB)
    assert d["max_bytes_per_stream"] == int(100_000_000 * rb.CAP_HEADROOM)


# ── reservations / usage ─────────────────────────────────────────────────────
def test_reserve_creates_dir_and_reservation(tmp_path):
    d = rb.reserve(tmp_path, "ep1", _script(), ["roundtable"], 5 * GB, now=1000.0)
    assert d["allowed"] and d["recording_id"]
    res = json.loads((tmp_path / d["recording_id"] / rb.RESERVATION_FILE).read_text())
    assert res["streams"] == ["roundtable"]
    assert res["max_bytes_per_stream"] == d["max_bytes_per_stream"]
    assert res["expires_at"] > 1000.0


def test_reserve_refusal_writes_nothing(tmp_path):
    d = rb.reserve(tmp_path, "ep1", _script(n_lines=200), ["roundtable"], limit_bytes=1000)
    assert not d["allowed"]
    assert d["recording_id"] is None
    assert list(tmp_path.iterdir()) == []


def test_live_reservation_counts_against_budget_so_second_play_is_refused(tmp_path):
    script = _script(n_lines=40)
    per = rb.estimate_recording(script)["total_bytes"]
    limit = int(per * 1.6)  # room for one estimate, not two
    first = rb.reserve(tmp_path, "ep1", script, ["roundtable"], limit, now=0.0)
    assert first["allowed"]
    second = rb.reserve(tmp_path, "ep1", script, ["roundtable"], limit, now=1.0)
    assert not second["allowed"]


def test_finished_recording_counts_at_its_real_size(tmp_path):
    d = rb.reserve(tmp_path, "ep1", _script(), ["roundtable"], 5 * GB, now=0.0)
    directory = tmp_path / d["recording_id"]
    (directory / "roundtable.mp4").write_bytes(b"x" * 1234)
    assert rb.budget_usage(tmp_path, now=1.0) >= d["reserved_bytes"]  # still live
    (directory / rb.done_marker_name("roundtable")).write_text("{}")
    used = rb.budget_usage(tmp_path, now=2.0)
    assert d["reserved_bytes"] > used >= 1234


def test_reservation_expires_if_a_stream_never_reports_done(tmp_path):
    d = rb.reserve(tmp_path, "ep1", _script(), ["coder", "roundtable"], 5 * GB, now=0.0)
    directory = tmp_path / d["recording_id"]
    (directory / rb.done_marker_name("roundtable")).write_text("{}")
    assert rb.recording_status(directory, now=10.0)["live"]  # coder still pending
    res = rb.read_reservation(directory)
    assert not rb.recording_status(directory, now=res["expires_at"] + 1)["live"]


def test_usage_counts_real_bytes_when_a_recording_outgrows_its_reservation(tmp_path):
    d = rb.reserve(tmp_path, "ep1", {"events": []}, ["roundtable"], 5 * GB, now=0.0)
    directory = tmp_path / d["recording_id"]
    big = d["reserved_bytes"] + 10
    with open(directory / "roundtable.mp4", "wb") as f:
        f.truncate(big)
    assert rb.budget_usage(tmp_path, now=1.0) >= big


def test_list_recordings_newest_first_with_mp4_files_only(tmp_path):
    (tmp_path / "20260101T000000Z_a_111111").mkdir()
    newer = tmp_path / "20260201T000000Z_b_222222"
    newer.mkdir()
    (newer / "roundtable.mp4").write_bytes(b"12345")
    (newer / rb.done_marker_name("roundtable")).write_text("{}")
    listed = rb.list_recordings(tmp_path)
    assert [r["id"] for r in listed] == [newer.name, "20260101T000000Z_a_111111"]
    assert listed[0]["files"] == [{"name": "roundtable.mp4", "bytes": 5}]


def test_delete_recording_refuses_traversal(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "rec"
    root.mkdir()
    assert rb.delete_recording(root, "../outside") is False
    assert outside.exists()


def test_delete_recording_removes_directory(tmp_path):
    d = rb.reserve(tmp_path, "ep1", _script(), ["roundtable"], 5 * GB)
    assert rb.delete_recording(tmp_path, d["recording_id"])
    assert not (tmp_path / d["recording_id"]).exists()
    assert rb.budget_usage(tmp_path) == 0


def test_missing_root_has_zero_usage(tmp_path):
    assert rb.budget_usage(tmp_path / "nope") == 0
    assert rb.list_recordings(tmp_path / "nope") == []


@pytest.mark.parametrize("n,expected", [(0, "0 B"), (999, "999 B"), (1500, "1.5 KB"),
                                        (2_500_000, "2.5 MB"), (5 * GB, "5.00 GB")])
def test_format_bytes(n, expected):
    assert rb.format_bytes(n) == expected
