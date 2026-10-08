"""P4.1: committed table lines reach the roundtable's existing live path (OB-32).

The arbiter is the only authority on what is committed, so the GM runtime
publishes each NEWLY committed transcript entry as `table_line` {seat, text,
kind, scene_id} to "roundtable", sent by the arbiter's worker id. The roundtable
spools it in the SAME entry format as office_line, so the director
(LiveDirector) and the tiles need no change. A line is published exactly once,
pending/retaken replies never, the observer seat never.
"""
import json

import pytest

import live_pane
from agent_handlers import table_gm
from table import live_feed, protocol

from test_table_gm_handler import (CONTRACT, Clock, FakeLLM, FakeProducer, _seat_replies,  # noqa: F401
                                   gm_cfg, providers, verdict)


def _lines(prod):
    return [m for m in prod.sent if m["type"] == live_feed.TABLE_LINE]


def test_runtime_publishes_each_commit_once_in_order_retakes_excluded():
    prod, clock = FakeProducer(), Clock()
    llm = FakeLLM(["The vault door groans open.", verdict()])
    table_gm.table_gm_idle_tick("tuber_0", gm_cfg(), llm, prod, None, clock=clock)
    rt = table_gm.runtime_for("tuber_0")
    _seat_replies(prod, rt, {"tuber_1": ["*draws* Back!", "Back, all of you."], "tuber_2": ["It hums."]})
    table_gm.table_gm_idle_tick("tuber_0", gm_cfg(), FakeLLM(["next"]), prod, None, clock=clock)
    lines = [m["payload"] for m in _lines(prod)]
    assert [(p["seat"], p["kind"], p["text"]) for p in lines] == [
        ("tuber_0", "direction", "The vault door groans open."),
        ("tuber_1", "reply", "Back, all of you."),
        ("tuber_2", "reply", "It hums."),
    ]
    assert all(m["to"] == live_feed.ROUNDTABLE and m["from"] == "tuber_0" for m in _lines(prod))
    assert all(p["scene_id"] == "s1" for p in lines)
    assert "*draws*" not in json.dumps(lines)


def test_overrule_published_as_kind_overrule_for_the_seat():
    prod, clock = FakeProducer(), Clock()
    cfg = gm_cfg()
    llm = FakeLLM(["Direction.", 'Chadwick: "We hold the door."', verdict()])
    table_gm.table_gm_idle_tick("tuber_0", cfg, llm, prod, None, clock=clock)
    rt = table_gm.runtime_for("tuber_0")
    _seat_replies(prod, rt, {"tuber_1": ["*a*", "*b*"], "tuber_2": ["It hums."]})   # 2 failures > max 1
    kinds = [(m["payload"]["seat"], m["payload"]["kind"]) for m in _lines(prod)]
    assert ("tuber_1", "overrule") in kinds


def test_feed_disabled_by_config():
    prod, clock = FakeProducer(), Clock()
    table_gm.table_gm_idle_tick("tuber_0", gm_cfg(live_feed=False), FakeLLM(["D."]), prod, None, clock=clock)
    assert _lines(prod) == []


# ── roundtable side ─────────────────────────────────────────────────────────

@pytest.fixture
def live_roundtable(monkeypatch, tmp_path):
    monkeypatch.setenv("TILE_RELAY_DIR", str(tmp_path))
    return {"agent": {"role": "roundtable", "live": {"enabled": True, "table_arbiter": "tuber_0",
                                                    "observer_slot": "tuber_7"}}}, tmp_path


def _table_line(seat="tuber_1", sender="tuber_0", text="Back, all of you.", kind="reply"):
    return live_feed.build_table_line(sender, {"speaker": seat, "text": text, "kind": kind}, "s1")


def test_roundtable_spools_table_line_in_the_office_line_format(live_roundtable):
    config, relay = live_roundtable
    path = live_feed.handle_table_line("roundtable", config, _table_line())
    assert path
    entry = json.loads(open(path).read())
    assert entry["type"] == live_pane.OFFICE_LINE          # LiveDirector consumes it unchanged
    assert entry["seat"] == "tuber_1" and entry["text"] == "Back, all of you."
    assert entry["kind"] == "reply"
    assert live_pane.list_spool(str(relay)) == [path]


@pytest.mark.parametrize("msg,why", [
    (_table_line(sender="tuber_3"), "not the arbiter"),
    (_table_line(seat="tuber_7"), "observer"),
    (_table_line(seat="tuber_99"), "unknown seat"),
    (_table_line(text="   "), "empty"),
])
def test_roundtable_refuses(live_roundtable, msg, why):
    config, relay = live_roundtable
    assert live_feed.handle_table_line("roundtable", config, msg) is None, why
    assert live_pane.list_spool(str(relay)) == []


def test_roundtable_ignores_when_live_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("TILE_RELAY_DIR", str(tmp_path))
    assert live_feed.handle_table_line("roundtable", {"agent": {"role": "roundtable"}},
                                       _table_line()) is None


def test_registered_handler():
    from agent_handlers import MESSAGE_HANDLERS
    assert live_feed.TABLE_LINE in MESSAGE_HANDLERS


def test_table_line_is_not_a_table_protocol_message():
    # it is roundtable presentation traffic, not part of the arbiter protocol
    assert not protocol.is_table_message(_table_line())
