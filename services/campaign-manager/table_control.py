"""Live agent table control for the campaign manager ("Live table" page).

Everything goes through message-api, the stack's only external publisher: a
control message to the GM worker (tuber_0), then the GM's `table_status` reply
read back from message-api's GET /logs/messages (message-logger persists the
bus to Postgres). Same protocol as scripts/table_ctl.py and the control
panel; see app/table/control.py for the payloads. Nothing is written to the
host and the GM is never restarted: run mode lives in the GM runtime.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

MESSAGE_API_URL = os.environ.get("MESSAGE_API_URL", "http://localhost:8090").rstrip("/")
GM_WORKER = os.environ.get("TABLE_GM_WORKER", "tuber_0")
ROUNDTABLE_WORKER = os.environ.get("TABLE_ROUNDTABLE_WORKER", "roundtable")

# UI action -> (message type, payload builder). Payload builders get the form.
ACTIONS = ("run_start", "run_stop", "run_stop_now", "next", "skip")


class TableControlError(RuntimeError):
    """message-api unreachable or the request was invalid."""


def _payload(v: Any) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def build_action(action: str, *, duration_min: str = "", start_from: str = "next",
                 start_index: str = "") -> tuple[str, dict]:
    """Map a UI button to (message type, payload). Raises TableControlError."""
    if action == "run_start":
        payload: dict = {}
        if start_from == "index":
            if not str(start_index).strip().isdigit():
                raise TableControlError("pick a scene to start from")
            payload["from"] = int(str(start_index).strip())
        elif start_from in ("next", "beginning"):
            payload["from"] = start_from
        else:
            raise TableControlError(f"unknown start point {start_from!r}")
        if str(duration_min).strip():
            try:
                minutes = float(duration_min)
            except ValueError:
                raise TableControlError("duration must be a number of minutes") from None
            if minutes <= 0:
                raise TableControlError("duration must be more than 0 minutes")
            payload["duration_min"] = minutes
        return "table_run_start", payload
    if action == "run_stop":
        return "table_run_stop", {"now": False}
    if action == "run_stop_now":
        return "table_run_stop", {"now": True}
    if action == "next":
        return "scene_request", {"next": True}
    if action == "skip":
        return "scene_stop", {}
    raise TableControlError(f"unknown action {action!r}")


async def send(client: httpx.AsyncClient, type_: str, payload: dict) -> str:
    """Publish one operator message to the GM; returns the ISO time to read replies from."""
    since = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    try:
        r = await client.post(f"{MESSAGE_API_URL}/messages",
                              json={"to": GM_WORKER, "type": type_, "payload": payload})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise TableControlError(f"message-api unreachable at {MESSAGE_API_URL}: {exc}") from exc
    return since


async def _messages(client, worker: str, since: str, limit: int = 500) -> list:
    r = await client.get(f"{MESSAGE_API_URL}/logs/messages",
                         params={"worker_id": worker, "since": since, "limit": limit})
    r.raise_for_status()
    return r.json().get("messages", [])


async def wait_status(client: httpx.AsyncClient, since: str, timeout_s: float,
                      poll_s: float = 1.0, action: bool = False) -> Optional[dict]:
    """The newest table_status the GM sent the operator after `since`, or None.
    action=True skips plain status replies, so a status poll from another
    browser tab can't be mistaken for the answer to a button press."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        try:
            rows = await _messages(client, GM_WORKER, since)
        except httpx.HTTPError:
            rows = []          # logger lag / transient: keep polling
        replies = [m for m in rows if m.get("type") == "table_status"
                   and m.get("from") == GM_WORKER and m.get("to") == "operator"
                   and not (action and _payload(m.get("payload")).get("result") == "status")]
        if replies:
            return _payload(replies[-1].get("payload"))
        if asyncio.get_event_loop().time() >= deadline:
            return None
        await asyncio.sleep(poll_s)


async def request(client: httpx.AsyncClient, type_: str, payload: dict,
                  timeout_s: float) -> Optional[dict]:
    since = await send(client, type_, payload)
    return await wait_status(client, since, timeout_s,
                             action=type_ != "table_status_request")


async def recent_lines(client: httpx.AsyncClient, minutes: float = 15, limit: int = 12) -> list:
    """The table's committed lines (table_line, GM -> roundtable), newest last."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    try:
        rows = await _messages(client, ROUNDTABLE_WORKER, since, limit=500)   # message-api MAX_LOG_LIMIT; newest 500
    except httpx.HTTPError as exc:
        raise TableControlError(f"message log unavailable: {exc}") from exc
    lines = []
    for m in rows:
        if m.get("type") != "table_line":
            continue
        p = _payload(m.get("payload"))
        if p.get("text"):
            lines.append({"seat": p.get("seat"), "text": p["text"], "kind": p.get("kind"),
                          "time": str(m.get("timestamp") or "")[11:19]})
    return lines[-limit:]


def describe(status: Optional[dict]) -> dict:
    """Template-friendly view of a table_status payload."""
    if not status:
        return {"ok": False}
    run = status.get("run") or {}

    def mmss(s):
        if s is None:
            return None
        s = int(s)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    if run.get("halted"):
        run_state = "halted (scenes kept getting stuck: is the model up?)"
    elif run.get("active"):
        run_state = "running unattended"
    elif run.get("finished"):
        run_state = "finished"
    elif status.get("mode") == "auto":
        run_state = "auto"
    else:
        run_state = "manual (idle between scenes)"
    return {
        "ok": True,
        "status": status,
        "run": run,
        "run_state": run_state,
        "elapsed": mmss(run.get("elapsed_s")),
        "remaining": mmss(run.get("remaining_s")),
        "duration": mmss(run.get("duration_s")),
        "seat_names": status.get("seat_names") or {},
    }
