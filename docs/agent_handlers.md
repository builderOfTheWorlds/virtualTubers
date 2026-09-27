# agent_handlers (package)

## Overview

`app/agent_handlers/` holds every bus message handler the agent loop
(`app/agent.py`, docs/agent.md) dispatches, split by role/concern into small
focused modules so later work (a correlation ID threaded through handlers,
a manager backlog, campaign integration) lands on one small file rather
than a 1,200-line `agent.py`. The package's `__init__.py` builds the
`MESSAGE_HANDLERS` dispatch table (message type → handler) that `main()`
looks each incoming message up in.

Behaviour, message types, payloads and log lines are documented in
docs/agent.md — this page only covers the layout and the contracts that
matter when editing or testing it.

| Module | Handles | Also contains |
|---|---|---|
| `__init__.py` | — | `MESSAGE_HANDLERS` |
| `common.py` | — | `_complete_with_emotion` (structured LLM narration, via `emotion.py`), `_send_manager_report` |
| `relay_files.py` | — | agent → replay pane relay files: `REPLAY_{REQUEST,STOP,CUE,READY}_FILE_ENV` + `DEFAULT_*` paths, `_resolve_replay_*_file`, `_atomic_write_json`, `_read_json_file`, `_write_replay_request` |
| `coder.py` | `task_assignment` | tmux demos `demo_editor_note`, `demo_filetree_ls`, `show_commit_in_filetree` |
| `tester.py` | `commit_notification`, `retest_request` | `_run_tests_and_report`, `_decide_test_outcome` + stub constants, `_resolve_workspace`, `WORKSPACE_MOUNT_PATTERN`, `_severity_from_failures` |
| `manager.py` | `bug_report`, `test_passed`, `task_complete`, `clarification_request` | `MAX_BUG_RETRIES` |
| `operator.py` | `operator_message` | — |
| `viewer.py` | `viewer_joined` | `_pick_rerun_episode` |
| `replay_relay.py` | `replay_request`, `replay_stop`, `replay_invite`, `replay_ready`, `replay_cue`, `replay_end` | `_is_valid_cast` |

Dependency direction is one-way: handler modules import from `common` /
`relay_files` (and from sibling app modules such as `message_bus`,
`agent_state`, `tmux_control`, `test_runner`, `episode_store`); `common` and
`relay_files` import nothing from the package; nothing in the package
imports `agent`.

## Signature

Every handler shares one signature:

```python
def handle_<type>(worker_id: str, agent_config: dict, llm_client,
                  producer, msg: dict, state_path: str | None = None,
                  coding_backend=None) -> None
```

```python
MESSAGE_HANDLERS: dict[str, Callable]  # 15 entries, see __init__.py
```

## Parameters

Identical for every handler — see docs/agent.md "Parameters".
`coding_backend` is only used by `handle_task_assignment`; the rest accept
and ignore it so dispatch stays uniform.

## Return Value

Handlers return `None` (side effects only: bus sends, avatar state writes,
relay-file writes, tmux keystrokes, console prints).

## Dependencies

- `message_bus.build_message`, `agent_state.write_state` (most handlers)
- `emotion` (`common.py`), `tmux_control` (`coder.py`),
  `test_runner` (`tester.py`), `episode_store` (`viewer.py`)
- stdlib: `json`, `os`, `random`, `time`

## Usage Examples

```python
# The agent loop (app/agent.py main()):
from agent_handlers import MESSAGE_HANDLERS

handler = MESSAGE_HANDLERS.get(msg["type"])
if handler:
    handler(worker_id, agent_config, llm_client, producer, msg, state_path,
            coding_backend=coding_backend)
```

```python
# Tests: patch the module that LOOKS THE NAME UP, not `agent`.
from agent_handlers import tester
from agent_handlers.tester import handle_commit_notification

def test_pass(monkeypatch):
    monkeypatch.setattr(tester, "_decide_test_outcome", lambda: (True, None))
    handle_commit_notification("tester", {"role": "tester"}, llm, producer, msg)
```

Where to patch:

| To stub | Patch |
|---|---|
| tmux calls / demo helpers | `agent_handlers.coder` (`select_pane`, `send_*`, `demo_*`) |
| test outcome / pytest run | `agent_handlers.tester` (`_decide_test_outcome`, `workspace_testable`, `run_pytest`) |
| relay-file writes in replay handlers | `agent_handlers.replay_relay` (`_atomic_write_json`, `_write_replay_request`) |
| relay-file writes in viewer_joined | `agent_handlers.viewer` (`_write_replay_request`) |
| episode library | `agent_handlers.viewer.episode_store` (the shared `episode_store` module object) |
| relay-file paths | env vars `REPLAY_REQUEST_FILE` / `REPLAY_STOP_FILE` / `REPLAY_CUE_FILE` / `REPLAY_READY_FILE` |

`app/agent.py` re-exports all these names for `from agent import ...`
callers, but `monkeypatch.setattr(agent, ...)` only rebinds the re-export
and does **not** reach the handlers.

## Error Handling

Unchanged from before the split — see docs/agent.md "Error Handling"
(LLM failures are caught per handler; role mismatches log and no-op; relay
file write failures log and never raise out of the tick loop).

## Changelog

- v1.0.0 (2026-09-27) — Created by splitting `app/agent.py` (pure
  refactor, zero behaviour change). Function bodies moved verbatim;
  `agent.py` keeps `main()` and backward-compatible re-exports.
