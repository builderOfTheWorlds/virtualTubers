"""
agent_handlers
Every bus message handler the agent loop (app/agent.py) dispatches, split
by role/concern into focused modules, plus the MESSAGE_HANDLERS dispatch
table built from them. See docs/agent_handlers.md for the module layout.

All handlers share one signature:
    handler(worker_id, agent_config, llm_client, producer, msg,
            state_path=None, coding_backend=None)

IDLE_TICK_HOOKS maps a role to an optional per-tick hook the loop calls
after polling (only on enabled ticks):
    hook(worker_id, agent_config, llm_client, producer, state_path=None)
"""
from .coder import handle_task_assignment
from .tester import handle_commit_notification, handle_retest_request
from .manager import (
    handle_bug_report,
    handle_clarification_request,
    handle_task_complete,
    handle_test_passed,
    manager_idle_tick,
)
from .operator import handle_operator_message
from .viewer import handle_viewer_joined
from .replay_relay import (
    handle_replay_cue,
    handle_replay_end,
    handle_replay_invite,
    handle_replay_ready,
    handle_replay_request,
    handle_replay_stop,
)


# Dispatch table — the 8 message types from docs/VTuber_AI_Dev_Team_Concept.md
# §3.4 (status_update is send-only heartbeat traffic; operator_message is
# message-api's default type standing in for direct operator chat), plus the
# viewer-join rerun trigger, replay_stop, and the 5 duet replay relay types.
MESSAGE_HANDLERS = {
    "task_assignment": handle_task_assignment,
    "commit_notification": handle_commit_notification,
    "retest_request": handle_retest_request,
    "bug_report": handle_bug_report,
    "test_passed": handle_test_passed,
    "task_complete": handle_task_complete,
    "clarification_request": handle_clarification_request,
    "operator_message": handle_operator_message,
    "replay_request": handle_replay_request,
    "replay_stop": handle_replay_stop,
    "viewer_joined": handle_viewer_joined,
    "replay_invite": handle_replay_invite,
    "replay_ready": handle_replay_ready,
    "replay_cue": handle_replay_cue,
    "replay_end": handle_replay_end,
}

# Per-role idle-tick hooks (role -> hook). The manager's feeds the opt-in
# task backlog (docs/task_backlog.md); a hook must never raise.
IDLE_TICK_HOOKS = {
    "manager": manager_idle_tick,
}

__all__ = ["MESSAGE_HANDLERS", "IDLE_TICK_HOOKS",
           *(h.__name__ for h in MESSAGE_HANDLERS.values()),
           *(h.__name__ for h in IDLE_TICK_HOOKS.values())]
