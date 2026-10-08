"""Committed table lines -> the roundtable's live transcript (build plan P4.1).

The roundtable already has a live path (OB-32, app/live_pane.py, docs/live_pane.md):
bus -> roundtable agent.py handler -> spool file -> LiveDirector (TTS) -> tiles.
The office feeds it with `office_line`, which accept_office_line only takes from
the speaking seat itself ("a seat commits only its own lines").

At the table the ARBITER decides what is committed (a seat cannot know whether
its line will be retaken), so the GM runtime publishes `table_line` from the
arbiter's worker id, once per newly committed transcript entry, and the
roundtable accepts it only from the configured arbiter (agent.live.table_arbiter,
default tuber_0). The spool entry is the office_line format plus `kind`
(direction | reply | overrule), so the director and tiles work unchanged.
"""
from __future__ import annotations

import logging
import time
import uuid

import live_pane
from message_bus import build_message, correlation_of

log = logging.getLogger(__name__)

TABLE_LINE = "table_line"
ROUNDTABLE = live_pane.ROUNDTABLE_WORKER_ID
DEFAULT_ARBITER = "tuber_0"
KINDS = ("direction", "reply", "overrule")


def build_table_line(arbiter_id, entry, scene_id):
    """One committed transcript entry -> a table_line message to the roundtable.

    The GM's own lines (speaker "gm") are spoken by the GM's seat, arbiter_id."""
    speaker = entry.get("speaker")
    seat = arbiter_id if speaker in ("gm", None, "") else speaker
    payload = {"seat": seat, "text": str(entry.get("text") or "").strip()[:live_pane.MAX_TEXT_CHARS],
               "kind": entry.get("kind") or "reply", "scene_id": scene_id, "emotion": None}
    return build_message(arbiter_id, ROUNDTABLE, TABLE_LINE, payload, correlation_id=scene_id)


class CommitPublisher:
    """Publishes each newly committed entry of an arbiter's transcript exactly once."""

    def __init__(self, arbiter_id, send):
        self.arbiter_id = arbiter_id
        self.send = send
        self._published = {}           # scene_id -> number of entries already published

    def publish_entry(self, entry, scene_id):
        """Publish one entry at commit time (Arbiter on_commit); counted so a later
        publish_new(state) does not send it again."""
        if str(entry.get("text") or "").strip():
            try:
                self.send(build_table_line(self.arbiter_id, entry, scene_id))
            except Exception as exc:  # noqa: BLE001
                log.error("table_line publish failed scene=%s: %s", scene_id, exc)
                return
        self._published[scene_id] = self._published.get(scene_id, 0) + 1

    def publish_new(self, state):
        scene_id = state.get("scene_id")
        transcript = state.get("transcript") or []
        done = self._published.get(scene_id, 0)
        for entry in transcript[done:]:
            if str(entry.get("text") or "").strip():
                try:
                    self.send(build_table_line(self.arbiter_id, entry, scene_id))
                except Exception as exc:  # noqa: BLE001 - presentation must not break the table
                    log.error("table_line publish failed scene=%s: %s", scene_id, exc)
                    return            # retry the rest on the next call
            done += 1
            self._published[scene_id] = done


# ── roundtable side ──────────────────────────────────────────────────────────

def accept_table_line(msg, config):
    """Validate one table_line into a spool entry (office_line format + kind), or None."""
    payload = msg.get("payload") if isinstance(msg, dict) else None
    if not isinstance(payload, dict):
        return None
    live = live_pane.live_settings(config)
    arbiter = str((live_pane._agent_block(config).get("live") or {}).get("table_arbiter")
                  or DEFAULT_ARBITER)
    seat = str(payload.get("seat") or "")
    reason = None
    if msg.get("from") != arbiter:
        reason = "not_the_arbiter"
    elif seat not in live_pane.seat_ids():
        reason = "unknown_seat"
    elif seat == live["observer_slot"]:
        reason = "observer_never_speaks"
    elif not str(payload.get("text") or "").strip():
        reason = "empty_text"
    if reason:
        log.warning("table_line refused reason=%s seat=%s sender=%s", reason, seat, msg.get("from"))
        return None
    kind = payload.get("kind") if payload.get("kind") in KINDS else "reply"
    return {
        "type": live_pane.OFFICE_LINE,         # LiveDirector consumes office_line entries
        "line_id": str(msg.get("id") or uuid.uuid4()),
        "seat": seat,
        "text": str(payload["text"]).strip()[:live_pane.MAX_TEXT_CHARS],
        "emotion": payload.get("emotion"),
        "kind": kind,
        "scene_id": payload.get("scene_id"),
        "correlation_id": correlation_of(msg),
        "sent_at": msg.get("timestamp"),
        "received_at": time.time(),
    }


def handle_table_line(worker_id, agent_config, msg, relay_dir=None):
    """Roundtable: spool one committed table line. Returns the path or None. Never raises."""
    try:
        if not live_pane.roundtable_live_enabled(agent_config):
            return None
        entry = accept_table_line(msg, agent_config)
        if entry is None:
            return None
        path = live_pane.enqueue_line(live_pane.resolve_relay_dir(relay_dir), entry,
                                      live_pane.live_settings(agent_config)["spool_max"])
        log.info("table_line spooled seat=%s kind=%s", entry["seat"], entry["kind"])
        return path
    except Exception as exc:  # noqa: BLE001
        log.error("table_line spool failed: %s", exc)
        return None
