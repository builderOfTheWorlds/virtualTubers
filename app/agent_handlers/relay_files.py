"""
agent_handlers/relay_files.py
Agent -> replay pane relay files (split out of app/agent.py). The paths
and the atomic write / tolerant read / consume helpers themselves live in
app/relay_io.py (docs/relay_io.md) — the one implementation shared with
app/replay_pane.py and app/tile_pane.py. The underscore names below are
kept as thin aliases because app/agent.py re-exports them and the handlers
(and their tests) look them up here.
"""
import relay_io


# Agent -> replay pane handoff file (see app/replay_pane.py, which polls it).
REPLAY_REQUEST_FILE_ENV = relay_io.REPLAY_REQUEST_FILE_ENV
DEFAULT_REPLAY_REQUEST_FILE = relay_io.DEFAULT_REPLAY_REQUEST_FILE

# Duet replay relay files (agent -> pane): the cue file carries the latest
# scene cue/end for the ratchet loop in replay_pane.py; the ready file
# accumulates which followers have loaded a duet airing.
REPLAY_CUE_FILE_ENV = relay_io.REPLAY_CUE_FILE_ENV
DEFAULT_REPLAY_CUE_FILE = relay_io.DEFAULT_REPLAY_CUE_FILE

REPLAY_READY_FILE_ENV = relay_io.REPLAY_READY_FILE_ENV
DEFAULT_REPLAY_READY_FILE = relay_io.DEFAULT_REPLAY_READY_FILE

# Agent -> pane stop signal (docs/operator_commands.md `replay_stop`): the
# pane's Performer polls it via Pacer.should_stop (docs/replay.md
# ReplayStopped).
REPLAY_STOP_FILE_ENV = relay_io.REPLAY_STOP_FILE_ENV
DEFAULT_REPLAY_STOP_FILE = relay_io.DEFAULT_REPLAY_STOP_FILE

_resolve_replay_request_file = relay_io.resolve_replay_request_file
_resolve_replay_stop_file = relay_io.resolve_replay_stop_file
_resolve_replay_cue_file = relay_io.resolve_replay_cue_file
_resolve_replay_ready_file = relay_io.resolve_replay_ready_file

# Atomic write (unique temp file + os.replace) — raises OSError, callers
# decide how loudly to report a failure.
_atomic_write_json = relay_io.atomic_write_json
# Tolerant read — missing/corrupt content returns None ("start fresh").
_read_json_file = relay_io.read_json


def _write_replay_request(request, if_absent=False):
    """Atomic write of the agent -> replay pane request file; returns its
    path, or None when if_absent=True and a request was already pending
    (left untouched — the race-free "don't clobber a pending request"
    rule). Raises OSError — callers decide how loudly to report a failure."""
    request_file = _resolve_replay_request_file()
    if if_absent:
        if not relay_io.atomic_create_json(request_file, request):
            return None
        return request_file
    _atomic_write_json(request_file, request)
    return request_file
