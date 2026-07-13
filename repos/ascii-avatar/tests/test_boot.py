"""Tests for the boot sequence frame set."""

from __future__ import annotations

import re

from avatar.frames.boot import BOOT_FRAME_RATE, BOOT_FRAMES

ANSI_ESCAPE = re.compile(r"\033\[[0-9;]*m")


class TestBootFrames:
    def test_boot_frames_non_empty(self):
        assert len(BOOT_FRAMES) > 0

    def test_each_frame_is_string(self):
        for i, frame in enumerate(BOOT_FRAMES):
            assert isinstance(frame, str), f"BOOT_FRAMES[{i}] is not a string"
            assert len(frame) > 0, f"BOOT_FRAMES[{i}] is empty"

    def test_at_least_six_frames(self):
        """Boot sequence needs enough frames to read as an animation."""
        assert len(BOOT_FRAMES) >= 6

    def test_frame_line_count(self):
        """Each frame should have between 10 and 25 lines."""
        for i, frame in enumerate(BOOT_FRAMES):
            lines = frame.strip("\n").split("\n")
            assert len(lines) >= 10, f"BOOT_FRAMES[{i}] too short: {len(lines)} lines"
            assert len(lines) <= 25, f"BOOT_FRAMES[{i}] too tall: {len(lines)} lines"

    def test_frame_line_width(self):
        """Each visible line (ANSI stripped) should be at most 60 chars wide."""
        for i, frame in enumerate(BOOT_FRAMES):
            for j, line in enumerate(frame.split("\n")):
                visible = ANSI_ESCAPE.sub("", line)
                assert len(visible) <= 60, (
                    f"BOOT_FRAMES[{i}] line {j} too wide: {len(visible)} chars"
                )

    def test_all_frames_same_line_count(self):
        """All frames should have identical line counts for flicker-free rendering."""
        counts = [len(f.strip("\n").split("\n")) for f in BOOT_FRAMES]
        assert len(set(counts)) == 1, f"Inconsistent line counts: {counts}"

    def test_frame_rate_is_positive_float(self):
        assert isinstance(BOOT_FRAME_RATE, float)
        assert BOOT_FRAME_RATE > 0

    def test_frame_rate_is_fast(self):
        """Boot animation should be snappy — under 0.5 s per frame."""
        assert BOOT_FRAME_RATE < 0.5
