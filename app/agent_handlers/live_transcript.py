"""
agent_handlers/live_transcript.py
Roundtable-side bus handlers for the live office transcript (OB-32,
docs/live_pane.md). Same job replay_relay.py does for replay_request: turn
a bus message into a relay FILE the panes poll, so no pane ever consumes
Kafka. All logic lives in app/live_pane.py; these are the dispatch-table
adapters with the standard handler signature.

Both are no-ops outside a live roundtable (live_pane.roundtable_live_enabled:
TILE_RELAY_DIR env + role roundtable + agent.live.enabled), so every other
worker that happens to receive an `observer_pose` broadcast ignores it.
"""
import live_pane


def handle_office_line(worker_id, agent_config, llm_client, producer, msg,
                       state_path=None, coding_backend=None):
    """`office_line` -> <relay>/live/<ns>-<id>.json for the director."""
    return live_pane.handle_office_line(worker_id, agent_config, msg)


def handle_table_line(worker_id, agent_config, llm_client, producer, msg,
                      state_path=None, coding_backend=None):
    """`table_line` (the table arbiter's committed lines, P4.1) -> the same live spool."""
    from table import live_feed
    return live_feed.handle_table_line(worker_id, agent_config, msg)


def handle_observer_pose(worker_id, agent_config, llm_client, producer, msg,
                         state_path=None, coding_backend=None):
    """`observer_pose` -> <relay>/<observer>.pose.json for the observer tile's head."""
    return live_pane.handle_observer_pose(worker_id, agent_config, msg)
