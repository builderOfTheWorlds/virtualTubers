"""
agent_handlers/relay_files.py
Agent -> replay pane relay files (split out of app/agent.py): env-overridable
paths plus the atomic JSON write / best-effort read helpers used by the
viewer_joined handler and the replay relay handlers. app/replay_pane.py
polls these files.
"""
import json
import os


# Agent -> replay pane handoff file (see app/replay_pane.py, which polls it).
REPLAY_REQUEST_FILE_ENV = "REPLAY_REQUEST_FILE"
DEFAULT_REPLAY_REQUEST_FILE = "/tmp/replay_request.json"

# Duet replay relay files (agent -> pane): the cue file carries the latest
# scene cue/end for the ratchet loop in replay_pane.py; the ready file
# accumulates which followers have loaded a duet airing. Same env-override +
# atomic-write convention as REPLAY_REQUEST_FILE_ENV above.
REPLAY_CUE_FILE_ENV = "REPLAY_CUE_FILE"
DEFAULT_REPLAY_CUE_FILE = "/tmp/replay_cue.json"

REPLAY_READY_FILE_ENV = "REPLAY_READY_FILE"
DEFAULT_REPLAY_READY_FILE = "/tmp/replay_ready.json"

# Agent -> pane stop signal (docs/operator_commands.md `replay_stop`): the
# pane's Performer polls it via Pacer.should_stop (docs/replay.md
# ReplayStopped), same env-override + atomic-write convention as
# REPLAY_REQUEST_FILE_ENV above.
REPLAY_STOP_FILE_ENV = "REPLAY_STOP_FILE"
DEFAULT_REPLAY_STOP_FILE = "/tmp/replay_stop.json"


def _resolve_replay_request_file():
    return os.environ.get(REPLAY_REQUEST_FILE_ENV) or DEFAULT_REPLAY_REQUEST_FILE


def _resolve_replay_stop_file():
    return os.environ.get(REPLAY_STOP_FILE_ENV) or DEFAULT_REPLAY_STOP_FILE


def _resolve_replay_cue_file():
    return os.environ.get(REPLAY_CUE_FILE_ENV) or DEFAULT_REPLAY_CUE_FILE


def _resolve_replay_ready_file():
    return os.environ.get(REPLAY_READY_FILE_ENV) or DEFAULT_REPLAY_READY_FILE


def _atomic_write_json(path, data):
    """Atomic write of a small agent -> replay-pane relay file (same
    temp+replace pattern as agent_state.py) so a polling pane never reads a
    half-written file. Raises OSError — callers decide how loudly to report
    a failure."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def _read_json_file(path):
    """Best-effort read of a relay file this module owns. Missing or
    corrupt content returns None — callers treat that as "start fresh"
    rather than crashing the handler."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_replay_request(request):
    """Atomic write of the agent -> replay pane request file. Raises
    OSError — callers decide how loudly to report a failure."""
    request_file = _resolve_replay_request_file()
    _atomic_write_json(request_file, request)
    return request_file
