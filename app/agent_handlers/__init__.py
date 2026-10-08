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
from .office import (
    ceo_idle_tick,
    handle_character_refresh,
    handle_directive,
    handle_functional_plan,
    handle_phase_change,
    handle_status_report,
    handle_technical_plan,
    handle_test_request,
    handle_wrap_up,
    observer_idle_tick,
    office_manager_idle_tick,
)
from .live_transcript import handle_observer_pose, handle_office_line
from .operator import handle_operator_message
from .viewer import handle_viewer_joined
from .table import handle_retake, handle_think_request, handle_turn_assignment
from .table_gm import handle_table_message, table_gm_idle_tick
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
    # ashiorid_office protocol (app/office/protocol.py; docs/agent_handlers.md
    # "Office handlers"). day_start / day_end are clock broadcasts with no
    # handler (the day runner emits them; nothing needs to react).
    "directive": handle_directive,
    "functional_plan": handle_functional_plan,
    "technical_plan": handle_technical_plan,
    "test_request": handle_test_request,
    "status_report": handle_status_report,
    "phase_change": handle_phase_change,
    # 23:45 day-runner broadcast -> one end-of-day status_report per seat.
    "wrap_up": handle_wrap_up,
    # Sunday weekly_reset broadcast -> clear office state, check out loop/<W>.
    "character_refresh": handle_character_refresh,
    # OB-32 live roundtable transcript (docs/live_pane.md): roundtable-side
    # relay writers; no-ops on every worker that isn't a live roundtable.
    "office_line": handle_office_line,
    "observer_pose": handle_observer_pose,
    # Live agent table (build plan P3.5/P3.6). Keyed by type like everything
    # here, so each handler checks agent.role itself: table_seat answers the
    # arbiter's think_request / turn_assignment / retake; table_gm feeds the
    # seats' think_done / character_reply and operator_override to its arbiter.
    "think_request": handle_think_request,
    "turn_assignment": handle_turn_assignment,
    "retake": handle_retake,
    "think_done": handle_table_message,
    "character_reply": handle_table_message,
    "operator_override": handle_table_message,
}

# Per-role idle-tick hooks (role -> hook). The manager's feeds the opt-in
# task backlog (docs/task_backlog.md); the office ones drive the CEO's day
# runner, the Office Manager's chores and the Party Member's gaze. A hook
# must never raise.
IDLE_TICK_HOOKS = {
    "manager": manager_idle_tick,
    # ashiorid_office (keyed by agent.role, build plan E2).
    "ceo": ceo_idle_tick,
    "office_manager": office_manager_idle_tick,
    "observer": observer_idle_tick,
    # Live agent table: the GM worker drives the turn arbiter (P3.6).
    "table_gm": table_gm_idle_tick,
}

__all__ = ["MESSAGE_HANDLERS", "IDLE_TICK_HOOKS",
           *(h.__name__ for h in MESSAGE_HANDLERS.values()),
           *(h.__name__ for h in IDLE_TICK_HOOKS.values())]
