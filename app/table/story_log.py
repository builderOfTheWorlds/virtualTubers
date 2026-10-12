"""The live agent table's running memory of the scenes already played.

This module provides StoryEntry and StoryLog to track what has already happened
in the office story, so the GM and seats don't restart the story every scene.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Mapping, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

HEADING = "# What has already happened"


@dataclasses.dataclass(frozen=True)
class StoryEntry:
    """A single finished scene's summary."""
    scene_id: str
    goal: str = ""
    notes: str = ""
    state_delta: dict = dataclasses.field(default_factory=dict)
    resolved: bool = True
    skipped: bool = False
    lines: tuple = dataclasses.field(default_factory=tuple)
    directions: tuple = dataclasses.field(default_factory=tuple)

    def to_dict(self) -> dict:
        """JSON-safe dict representation."""
        return {
            "scene_id": self.scene_id,
            "goal": self.goal,
            "notes": self.notes,
            "state_delta": dict(self.state_delta),
            "resolved": self.resolved,
            "skipped": self.skipped,
            "lines": list(self.lines),
            "directions": list(self.directions),
        }

    @classmethod
    def from_dict(cls, d) -> "StoryEntry":
        """Create a StoryEntry from a mapping.

        Raises ValueError if d is not a Mapping, or scene_id is missing/not a
        non-empty string.
        """
        if not isinstance(d, Mapping):
            raise ValueError("d must be a Mapping")
        scene_id = d.get("scene_id")
        if not isinstance(scene_id, str) or not scene_id:
            raise ValueError("scene_id must be a non-empty string")

        lines_raw = d.get("lines", ())
        if not isinstance(lines_raw, (list, tuple)):
            lines_raw = ()
        lines = tuple(str(x) for x in lines_raw if str(x).strip())

        directions_raw = d.get("directions", ())
        if not isinstance(directions_raw, (list, tuple)):
            directions_raw = ()
        directions = tuple(str(x) for x in directions_raw if str(x).strip())

        state_delta = d.get("state_delta", {})
        if not isinstance(state_delta, dict):
            state_delta = {}

        resolved = d.get("resolved", True)
        if resolved is None:
            resolved = True
        resolved = bool(resolved)

        skipped = d.get("skipped", False)
        if skipped is None:
            skipped = False
        skipped = bool(skipped)

        return cls(
            scene_id=scene_id,
            goal=str(d.get("goal", "")).strip(),
            notes=str(d.get("notes", "")).strip(),
            state_delta=state_delta,
            resolved=resolved,
            skipped=skipped,
            lines=lines,
            directions=directions,
        )


def entry_from_state(
    state: dict,
    contract: dict,
    *,
    skipped: bool = False,
    max_lines: int = 12,
) -> StoryEntry:
    """Build a StoryEntry from the arbiter state and contract.

    state: dict with keys scene_id, resolved, state_delta, notes, transcript.
    contract: dict with keys scene_id, canon_goal.
    """
    scene_id = state.get("scene_id") or contract.get("scene_id", "")
    goal = str(contract.get("canon_goal", "")).strip()
    notes = str(state.get("notes") or "").strip()
    resolved_raw = state.get("resolved", True)
    if resolved_raw is None:
        resolved_raw = True
    resolved = bool(resolved_raw)
    state_delta = state.get("state_delta", {})
    if not isinstance(state_delta, dict):
        state_delta = {}

    transcript = state.get("transcript", [])
    if not isinstance(transcript, list):
        transcript = []

    directions: list[str] = []
    lines: list[str] = []

    for item in transcript:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind", "")
        text = item.get("text", "")
        if not isinstance(text, str):
            text = str(text)
        text = text.strip()
        if not text:
            continue

        if kind == "direction":
            directions.append(text)
        else:
            lines.append(text)

    # Keep only the last max_lines items
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    if len(directions) > max_lines:
        directions = directions[-max_lines:]

    return StoryEntry(
        scene_id=scene_id,
        goal=goal,
        notes=notes,
        state_delta=state_delta,
        resolved=resolved,
        skipped=skipped,
        lines=tuple(lines),
        directions=tuple(directions),
    )


class StoryLog:
    """A log of StoryEntry objects, keeping only the last `keep` entries."""

    def __init__(self, entries: Sequence[StoryEntry] = (), keep: int = 12) -> None:
        self._keep = keep
        self._entries: list[StoryEntry] = list(entries)
        if len(self._entries) > keep:
            self._entries = self._entries[-keep:]

    @property
    def entries(self) -> tuple[StoryEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def append(self, entry: StoryEntry) -> None:
        self._entries.append(entry)
        if len(self._entries) > self._keep:
            self._entries = self._entries[-self._keep:]

    def clear(self) -> None:
        self._entries.clear()

    def to_list(self) -> list[dict]:
        return [e.to_dict() for e in self._entries]

    @classmethod
    def from_list(cls, items: Optional[list], keep: int = 12) -> "StoryLog":
        """Create a StoryLog from a list of dicts.

        Bad items are skipped with a warning.
        """
        if items is None:
            return cls(keep=keep)
        entries: list[StoryEntry] = []
        for item in items:
            try:
                if not isinstance(item, Mapping):
                    log.warning("Skipping bad item in from_list: not a mapping")
                    continue
                entry = StoryEntry.from_dict(item)
                entries.append(entry)
            except (ValueError, TypeError) as exc:
                log.warning("Skipping bad item in from_list: %s", exc)
                continue
        return cls(entries=entries, keep=keep)

    def recent_lines(self, n_scenes: int = 2) -> tuple[str]:
        """Lines of the last n_scenes NON-skipped entries, oldest first."""
        if n_scenes <= 0:
            return ()
        non_skipped = [e for e in self._entries if not e.skipped]
        last_n = non_skipped[-n_scenes:] if len(non_skipped) >= n_scenes else non_skipped
        result: list[str] = []
        for e in last_n:
            result.extend(e.lines)
        return tuple(result)

    def recent_directions(self, n_scenes: int = 3) -> tuple[str]:
        """Directions of the last n_scenes NON-skipped entries, oldest first."""
        if n_scenes <= 0:
            return ()
        non_skipped = [e for e in self._entries if not e.skipped]
        last_n = non_skipped[-n_scenes:] if len(non_skipped) >= n_scenes else non_skipped
        result: list[str] = []
        for e in last_n:
            result.extend(e.directions)
        return tuple(result)

    def render(self, max_entries: int = 6, max_chars: int = 2500) -> str:
        """Render the log as a human-readable string."""
        if not self._entries or max_entries <= 0:
            return ""

        # Take the last max_entries entries, oldest first
        entries_to_render = self._entries[-max_entries:] if len(self._entries) >= max_entries else self._entries

        lines: list[str] = []
        for e in entries_to_render:
            if e.skipped:
                line = f"- [{e.scene_id}] skipped (not played)"
            else:
                line = f"- [{e.scene_id}] {e.goal}"
                if e.notes:
                    line += f" - {e.notes}"
                if e.state_delta:
                    parts = [f"{k}={v}" for k, v in e.state_delta.items()]
                    line += f" (state: {'; '.join(parts)})"
            lines.append(line)

        # Build the full text
        full_text = HEADING + "\n" + "\n".join(lines)

        log.debug("render: %d entries, %d chars before cap", len(lines), len(full_text))

        if len(full_text) <= max_chars:
            return full_text

        # Need to trim. Drop oldest lines first.
        # Try dropping oldest lines one by one
        while len(lines) > 1:
            lines.pop(0)
            full_text = HEADING + "\n" + "\n".join(lines)
            if len(full_text) <= max_chars:
                break

        if len(full_text) > max_chars:
            # Even with one line, it's too long. Shorten the newest line.
            # The newest line is lines[-1]
            newest_line = lines[-1]
            # We need to shorten it so that HEADING + "\n" + shortened_line fits
            # But we must keep the "- [<scene_id>] " prefix
            # Find the prefix length
            # The prefix is "- [<scene_id>] "
            # Actually, we need to find where the content starts after the prefix
            # Let's compute the prefix for the newest entry
            e = entries_to_render[-1] if entries_to_render else None
            if e is None:
                return ""

            # Reconstruct the prefix
            if e.skipped:
                prefix = f"- [{e.scene_id}] skipped (not played)"
            else:
                prefix = f"- [{e.scene_id}] {e.goal}"
                if e.notes:
                    prefix += f" - {e.notes}"
                if e.state_delta:
                    parts = [f"{k}={v}" for k, v in e.state_delta.items()]
                    prefix += f" (state: {'; '.join(parts)})"

            # The prefix is the entire line if it's skipped or if we can't shorten
            # Actually, we need to shorten the line on a word boundary, keeping the prefix
            # But the prefix itself might be long. Let's think differently.
            # We need the final line to fit in max_chars - len(HEADING) - 1 (for newline)
            available = max_chars - len(HEADING) - 1
            if available <= 0:
                return ""

            # The prefix is the part we must keep. But if the prefix itself is longer than available,
            # we can't keep it all. The spec says: "always keep its '- [<scene_id>] ' prefix"
            # So we need to find the prefix length.
            # Let's extract the prefix from the newest_line
            # The prefix is "- [<scene_id>] "
            # For skipped: "- [<scene_id>] skipped (not played)" - this is the whole line
            # For non-skipped: "- [<scene_id>] <goal>" then optional notes and state

            # Let's just try to shorten the line by removing words from the end
            # until it fits, but we must keep the "- [<scene_id>] " part
            # Actually, the spec says: "shorten that line on a word boundary ending with '…'
            # so the result fits, but always keep its '- [<scene_id>] ' prefix"

            # So we need to find the position after "- [<scene_id>] "
            prefix_str = f"- [{e.scene_id}] "
            if newest_line.startswith(prefix_str):
                content_start = len(prefix_str)
            else:
                # This shouldn't happen, but fallback
                content_start = len(newest_line)

            # The available space for the entire line is `available`
            # The prefix is `prefix_str` (len = content_start)
            # So we can have at most `available - content_start` chars for the content after the prefix
            # But wait, the spec says "keep its '- [<scene_id>] ' prefix", so we keep that exact string
            # and shorten the rest.

            # However, if the prefix itself is longer than available, we can't fit it.
            # The spec says: "if the heading plus that prefix plus 2 chars cannot fit, return ''"
            # So let's check: len(HEADING) + 1 + len(prefix_str) + 2 > max_chars?
            if len(HEADING) + 1 + len(prefix_str) + 2 > max_chars:
                return ""

            # Now shorten the content after the prefix
            content = newest_line[content_start:]
            # We need to shorten content so that prefix_str + shortened_content fits in `available`
            max_content_len = available - len(prefix_str)
            if max_content_len <= 0:
                return ""

            if len(content) <= max_content_len:
                # Shouldn't happen since we're in the cap case, but just in case
                pass
            else:
                # Shorten on a word boundary
                if max_content_len <= 0:
                    shortened_content = ""
                else:
                    # Try to cut at a word boundary
                    if max_content_len >= len(content):
                        shortened_content = content
                    else:
                        # Find the last space before max_content_len
                        cut_pos = content[:max_content_len - 1].rsplit(" ", 1)
                        if len(cut_pos) == 2 and cut_pos[1]:
                            shortened_content = cut_pos[0] + "…"
                        else:
                            # No space found, just truncate
                            shortened_content = content[:max_content_len - 1] + "…"

                newest_line = prefix_str + shortened_content

            lines[-1] = newest_line
            full_text = HEADING + "\n" + "\n".join(lines)

        return full_text
