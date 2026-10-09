"""Operator control messages for the live table GM (not part of the arbiter protocol).

message-api (the sole external publisher; its messages arrive from "operator") or the
control panel / scripts/table_ctl.py sends these to the GM worker (tuber_0):

  scene_request         exactly one of {"next": true} | {"scene_id": "<id>"} | {"index": <int>}
                        plus optional "force": bool (end the running scene first)
  scene_stop            {} : skip the running scene (arbiter operator_override skip_scene)
  table_status_request  {}

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
CONTROL_TYPES = (SCENE_REQUEST, SCENE_STOP, STATUS_REQUEST)
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
