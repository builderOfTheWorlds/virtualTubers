"""Multi-session state tracker for the avatar agent."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass
class SessionInfo:
    session_id: str
    project: str
    last_event: str
    last_update: float  # monotonic timestamp
    status: str = "active"  # "active" | "idle" | "error"
    tool_count: int = 0
    error_count: int = 0


ERROR_EVENTS = frozenset({"PostToolUseFailure"})


class SessionTracker:
    """Tracks multiple Claude Code sessions by session ID."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionInfo] = {}

    def update(self, session_id: str, cwd: str, hook_event: str) -> None:
        now = time.monotonic()
        project = PurePosixPath(cwd).name or "unknown"

        if session_id in self._sessions:
            info = self._sessions[session_id]
            info.last_event = hook_event
            info.last_update = now
            info.tool_count += 1
            if hook_event in ERROR_EVENTS:
                info.error_count += 1
                info.status = "error"
            else:
                info.status = "active"
        else:
            is_error = hook_event in ERROR_EVENTS
            self._sessions[session_id] = SessionInfo(
                session_id=session_id,
                project=project,
                last_event=hook_event,
                last_update=now,
                status="error" if is_error else "active",
                tool_count=1,
                error_count=1 if is_error else 0,
            )

    def get(self, session_id: str) -> SessionInfo | None:
        return self._sessions.get(session_id)

    def summarize(self) -> list[dict[str, str | int]]:
        """Produce per-session summaries for the agent prompt."""
        return [
            {
                "project": info.project,
                "last_event": info.last_event,
                "status": info.status,
                "tool_count": info.tool_count,
                "error_count": info.error_count,
            }
            for info in self._sessions.values()
        ]

    def mark_stale(self, threshold: float = 30) -> None:
        """Mark sessions as idle if no events received within threshold seconds."""
        now = time.monotonic()
        for info in self._sessions.values():
            if info.status != "idle" and (now - info.last_update) > threshold:
                info.status = "idle"

    def reset_counts(self) -> None:
        """Reset per-cycle counters after each agent decision."""
        for info in self._sessions.values():
            info.tool_count = 0
            info.error_count = 0

    @property
    def active_count(self) -> int:
        return sum(1 for info in self._sessions.values() if info.status != "idle")
