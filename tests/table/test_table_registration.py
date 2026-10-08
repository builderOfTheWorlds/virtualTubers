"""P3.5/P3.6 wiring: the table handlers are registered in agent_handlers."""
from agent_handlers import IDLE_TICK_HOOKS, MESSAGE_HANDLERS
from agent_handlers import table, table_gm


def test_seat_and_gm_handlers_registered():
    assert MESSAGE_HANDLERS["think_request"] is table.handle_think_request
    assert MESSAGE_HANDLERS["turn_assignment"] is table.handle_turn_assignment
    assert MESSAGE_HANDLERS["retake"] is table.handle_retake
    for t in ("think_done", "character_reply", "operator_override"):
        assert MESSAGE_HANDLERS[t] is table_gm.handle_table_message
    assert IDLE_TICK_HOOKS["table_gm"] is table_gm.table_gm_idle_tick


def test_table_handlers_are_noops_for_other_roles():
    # every worker gets every type: a coder must never act on table traffic
    class P:
        sent = []

        def send(self, m):
            self.sent.append(m)
    p = P()
    for fn in (table.handle_think_request, table.handle_turn_assignment, table.handle_retake,
               table_gm.handle_table_message):
        fn("coder", {"role": "coder"}, None, p, {"type": "x", "payload": {}}, None)
    table_gm.table_gm_idle_tick("coder", {"role": "coder"}, None, p, None)
    assert p.sent == []
