"""Operator control messages for the live table GM (not part of the arbiter protocol).

message-api (the sole external publisher; its messages arrive from "operator") or the
control panel / scripts/table_ctl.py sends these to the GM worker (tuber_0):

  scene_request         exactly one of {"next": true} | {"scene_id": "<id>"} | {"index": <int>}
                        plus optional "force": bool (end the running scene first)
  scene_stop            {} : skip the running scene (arbiter operator_override skip_scene)
  table_status_request  {}
  table_run_start       {"from": "next" | "beginning" | <index>, "duration_min": <number>?}
                        switch the GM to auto and play scenes unattended for duration_min
                        (default: the config's auto_duration_s; none = until max_scenes/the end).
                        No restart, nothing written to the host: the mode lives in the runtime
                        and a GM restart returns to the config's start_mode.
  table_run_stop        {"now": bool}: back to manual. now=false lets the running scene finish
                        (no new one starts); now=true also ends the running scene.

The GM answers every one of them with `table_status` to the sender (see TableRuntime.status):
  {state: idle|running, scene_id, round, phase, index, next_scene_id, total, mode,
   result: started|busy|stopped|idle|status|error, error}
Nothing private (no transcript, no intent, no reasoning) is ever in a status.
"""
from __future__ import annotations

SCENE_REQUEST = "scene_request"
SCENE_STOP = "scene_stop"
STATUS_REQUEST = "table_status_request"
TABLE_STATUS = "table_status"
RUN_START = "table_run_start"
RUN_STOP = "table_run_stop"
CONTROL_TYPES = (SCENE_REQUEST, SCENE_STOP, STATUS_REQUEST, RUN_START, RUN_STOP)
MAX_RUN_MIN = 24 * 60
MODES = ("manual", "auto")


class ControlError(ValueError):
    """An invalid control payload; the message names the problem."""


def parse_scene_request(payload) -> dict:
    """Validate a scene_request payload -> {"next"|"scene_id"|"index": ..., "force": bool}."""
    if not isinstance(payload, dict):
        raise ControlError("scene_request payload must be an object")
    selectors = [k for k in ("next", "scene_id", "index") if k in payload]
    if len(selectors) != 1:
        raise ControlError('scene_request needs exactly one of "next", "scene_id", "index"')
    key = selectors[0]
    value = payload[key]
    if key == "next" and value is not True:
        raise ControlError('"next" must be true')
    if key == "scene_id" and (not isinstance(value, str) or not value.strip()):
        raise ControlError('"scene_id" must be a non-empty string')
    if key == "index" and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ControlError('"index" must be an integer >= 0')
    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise ControlError('"force" must be true or false')
    return {key: value.strip() if key == "scene_id" else value, "force": force}


def parse_run_start(payload) -> dict:
    """Validate table_run_start -> {"from": "next"|"beginning"|int, "duration_s": float|None}."""
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ControlError("table_run_start payload must be an object")
    start = payload.get("from", "next")
    if isinstance(start, str) and start.strip().isdigit():
        start = int(start.strip())
    if isinstance(start, bool) or not (start in ("next", "beginning")
                                       or (isinstance(start, int) and start >= 0)):
        raise ControlError('"from" must be "next", "beginning" or a scene index >= 0')
    minutes = payload.get("duration_min")
    if minutes in (None, ""):
        return {"from": start, "duration_s": None}
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        raise ControlError('"duration_min" must be a number') from None
    if not 0 < minutes <= MAX_RUN_MIN:
        raise ControlError(f'"duration_min" must be > 0 and <= {MAX_RUN_MIN}')
    return {"from": start, "duration_s": minutes * 60.0}


def parse_run_stop(payload) -> dict:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ControlError("table_run_stop payload must be an object")
    now = payload.get("now", False)
    if not isinstance(now, bool):
        raise ControlError('"now" must be true or false')
    return {"now": now}
