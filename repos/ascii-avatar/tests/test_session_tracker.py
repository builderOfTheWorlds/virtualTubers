import time
import pytest
from avatar.session_tracker import SessionInfo, SessionTracker


class TestSessionTracker:
    def test_update_creates_new_session(self):
        tracker = SessionTracker()
        tracker.update(
            session_id="abc123",
            cwd="/home/user/projects/vyzibl",
            hook_event="PreToolUse",
        )
        info = tracker.get("abc123")
        assert info is not None
        assert info.project == "vyzibl"
        assert info.status == "active"
        assert info.tool_count == 1
        assert info.error_count == 0
        assert info.last_event == "PreToolUse"

    def test_update_increments_tool_count(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PreToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PreToolUse")
        info = tracker.get("s1")
        assert info.tool_count == 3

    def test_update_counts_errors(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUseFailure")
        info = tracker.get("s1")
        assert info.error_count == 1
        assert info.status == "error"

    def test_project_from_cwd(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/ascii-avatar", "PreToolUse")
        assert tracker.get("s1").project == "ascii-avatar"

    def test_project_from_home_dir(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user", "PreToolUse")
        assert tracker.get("s1").project == "user"


class TestSessionSummary:
    def test_summarize_single_session(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        summary = tracker.summarize()
        assert len(summary) == 1
        assert summary[0]["project"] == "vyzibl"
        assert summary[0]["tool_count"] == 2
        assert summary[0]["error_count"] == 0
        assert summary[0]["status"] == "active"

    def test_summarize_multiple_sessions(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s2", "/home/user/projects/xentra", "PreToolUse")
        summary = tracker.summarize()
        assert len(summary) == 2
        projects = {s["project"] for s in summary}
        assert projects == {"vyzibl", "xentra"}

    def test_summarize_empty(self):
        tracker = SessionTracker()
        assert tracker.summarize() == []

    def test_mark_stale_sessions_idle(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        # Manually backdate the session
        tracker._sessions["s1"].last_update = time.monotonic() - 35
        tracker.mark_stale(threshold=30)
        assert tracker.get("s1").status == "idle"

    def test_reset_counts(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.reset_counts()
        info = tracker.get("s1")
        assert info.tool_count == 0
        assert info.error_count == 0

    def test_active_session_count(self):
        tracker = SessionTracker()
        tracker.update("s1", "/home/user/projects/vyzibl", "PostToolUse")
        tracker.update("s2", "/home/user/projects/xentra", "PreToolUse")
        assert tracker.active_count == 2
