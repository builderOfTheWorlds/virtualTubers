"""Operator trigger for the live table GM (scene_request / scene_stop / table_status).

Today a scene starts only by editing table.start_index and restarting the GM. This
adds operator control over the bus (message-api is the sole external publisher; its
messages arrive with from="operator"):

  scene_request         {"next": true} | {"scene_id": id} | {"index": n}, optional "force"
  scene_stop            {} (skips the running scene through the arbiter's operator_override)
  table_status_request  {}
  -> the GM always answers table_status to the sender:
     {state, scene_id, round, phase, index, next_scene_id, total, mode, result, error}

table.start_mode: manual (wait for a request; idle after each scene) | auto (old behaviour).
The arc position (last started index) persists in table.position_file across restarts.
"""
import json

import pytest

from agent_handlers import table_gm as gm
from table import control, protocol

from test_table_gm_handler import (CONTRACT, Clock, FakeLLM, FakeProducer, _seat_replies,  # noqa: F401
                                   gm_cfg, providers, verdict)

CONTRACTS = [dict(CONTRACT, scene_id=f"s{i}", order=i) for i in range(4)]


@pytest.fixture
def three_scenes(monkeypatch):
    monkeypatch.setattr(gm, "contracts_provider", lambda agent_config: [dict(c) for c in CONTRACTS])


def cfg(tmp_path, **over):
    c = gm_cfg(start_mode="manual", position_file=str(tmp_path / "pos.json"), **over)
    return c


def op(type_, payload=None):
    from message_bus import build_message
    return build_message("operator", "tuber_0", type_, payload or {})


def statuses(prod):
    return [m["payload"] for m in prod.sent if m["type"] == control.TABLE_STATUS]


def starts(prod):
    return [protocol.parse(m)["scene_id"] for m in prod.sent if m["type"] == "scene_start"]


# ── validation ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload,expect", [
    ({"next": True}, {"next": True, "force": False}),
    ({"scene_id": "s2", "force": True}, {"scene_id": "s2", "force": True}),
    ({"index": 3}, {"index": 3, "force": False}),
])
def test_scene_request_validation_ok(payload, expect):
    assert control.parse_scene_request(payload) == expect


@pytest.mark.parametrize("payload", [{}, {"next": True, "index": 1}, {"index": -1}, {"index": "2"},
                                     {"scene_id": ""}, {"next": False}, {"force": True}, "x"])
def test_scene_request_validation_rejects(payload):
    with pytest.raises(control.ControlError):
        control.parse_scene_request(payload)


# ── manual mode ────────────────────────────────────────────────────────────

def test_manual_mode_waits_for_a_request(tmp_path, three_scenes):
    prod = FakeProducer()
    gm.table_gm_idle_tick("tuber_0", cfg(tmp_path), FakeLLM([]), prod, None, clock=Clock())
    gm.table_gm_idle_tick("tuber_0", cfg(tmp_path), FakeLLM([]), prod, None, clock=Clock())
    assert starts(prod) == []


def test_next_starts_the_first_scene_then_the_following_one(tmp_path, three_scenes):
    prod, clock, c = FakeProducer(), Clock(), cfg(tmp_path)
    llm = FakeLLM(["Dir 0.", verdict(), "Dir 1."])
    gm.handle_table_message("tuber_0", c, llm, prod, op("scene_request", {"next": True}), None)
    rt = gm.runtime_for("tuber_0")
    rt.clock = clock
    _seat_replies(prod, rt, {"tuber_1": ["A."], "tuber_2": ["B."]})
    gm.table_gm_idle_tick("tuber_0", c, llm, prod, None, clock=clock)          # resolved -> idle
    assert starts(prod) == ["s0"] and rt.arbiter is None
    gm.handle_table_message("tuber_0", c, llm, prod, op("scene_request", {"next": True}), None)
    assert starts(prod) == ["s0", "s1"]
    assert statuses(prod)[0]["result"] == "started" and statuses(prod)[0]["scene_id"] == "s0"


def test_request_by_scene_id_and_index(tmp_path, three_scenes):
    prod, c = FakeProducer(), cfg(tmp_path)
    llm = FakeLLM(["Dir.", "Dir."])
    gm.handle_table_message("tuber_0", c, llm, prod, op("scene_request", {"scene_id": "s2"}), None)
    assert starts(prod) == ["s2"]
    gm.handle_table_message("tuber_0", c, llm, prod, op("scene_request", {"index": 3, "force": True}), None)
    assert starts(prod) == ["s2", "s3"]


def test_busy_without_force_is_refused(tmp_path, three_scenes):
    prod, c = FakeProducer(), cfg(tmp_path)
    llm = FakeLLM(["Dir."])
    gm.handle_table_message("tuber_0", c, llm, prod, op("scene_request", {"next": True}), None)
    gm.handle_table_message("tuber_0", c, llm, prod, op("scene_request", {"next": True}), None)
    assert starts(prod) == ["s0"]
    last = statuses(prod)[-1]
    assert last["result"] == "busy" and last["state"] == "running" and last["scene_id"] == "s0"


def test_force_ends_the_running_scene_through_the_arbiter(tmp_path, three_scenes):
    prod, c = FakeProducer(), cfg(tmp_path)
    llm = FakeLLM(["Dir 0.", "Dir 2."])
    gm.handle_table_message("tuber_0", c, llm, prod, op("scene_request", {"next": True}), None)
    old = gm.runtime_for("tuber_0").arbiter
    gm.handle_table_message("tuber_0", c, llm, prod,
                            op("scene_request", {"scene_id": "s2", "force": True}), None)
    assert old.state["phase"] in ("resolved", "aborted")
    assert starts(prod) == ["s0", "s2"]


def test_unknown_scene_is_an_error_reply(tmp_path, three_scenes):
    prod, c = FakeProducer(), cfg(tmp_path)
    gm.handle_table_message("tuber_0", c, FakeLLM([]), prod, op("scene_request", {"scene_id": "nope"}), None)
    gm.handle_table_message("tuber_0", c, FakeLLM([]), prod, op("scene_request", {"index": 99}), None)
    gm.handle_table_message("tuber_0", c, FakeLLM([]), prod, op("scene_request", {"bogus": 1}), None)
    assert [s["result"] for s in statuses(prod)] == ["error", "error", "error"]
    assert all(s["error"] for s in statuses(prod)) and starts(prod) == []


def test_scene_stop_skips_the_running_scene(tmp_path, three_scenes):
    prod, c = FakeProducer(), cfg(tmp_path)
    gm.handle_table_message("tuber_0", c, FakeLLM(["Dir."]), prod, op("scene_request", {"next": True}), None)
    arb = gm.runtime_for("tuber_0").arbiter
    gm.handle_table_message("tuber_0", c, FakeLLM([]), prod, op("scene_stop"), None)
    assert arb.state["phase"] in ("resolved", "aborted")
    gm.table_gm_idle_tick("tuber_0", c, FakeLLM([]), prod, None, clock=Clock())
    assert gm.runtime_for("tuber_0").arbiter is None                  # manual: stays idle
    assert statuses(prod)[-1]["result"] == "stopped"


def test_status_reply_shape_and_no_private_text(tmp_path, three_scenes):
    prod, c = FakeProducer(), cfg(tmp_path)
    gm.handle_table_message("tuber_0", c, FakeLLM([]), prod, op(control.STATUS_REQUEST), None)
    s = statuses(prod)[-1]
    assert s["state"] == "idle" and s["mode"] == "manual" and s["total"] == 4
    assert s["next_scene_id"] == "s0" and s["result"] == "status"
    msg = [m for m in prod.sent if m["type"] == control.TABLE_STATUS][-1]
    assert msg["to"] == "operator" and msg["from"] == "tuber_0"
    assert not (set(s) & set(protocol.PRIVATE_KEYS)) and "transcript" not in s


def test_position_persists_across_a_restart(tmp_path, three_scenes):
    prod, c = FakeProducer(), cfg(tmp_path)
    gm.handle_table_message("tuber_0", c, FakeLLM(["Dir."]), prod, op("scene_request", {"index": 2}), None)
    assert json.loads((tmp_path / "pos.json").read_text())["index"] == 2
    gm.reset_runtimes()                                                  # the GM container restarts
    prod2 = FakeProducer()
    gm.handle_table_message("tuber_0", c, FakeLLM([]), prod2, op(control.STATUS_REQUEST), None)
    assert statuses(prod2)[-1]["next_scene_id"] == "s3"


def test_unwritable_position_file_does_not_break_the_table(tmp_path, three_scenes):
    prod = FakeProducer()
    c = gm_cfg(start_mode="manual", position_file="/proc/nope/pos.json")
    gm.handle_table_message("tuber_0", c, FakeLLM(["Dir."]), prod, op("scene_request", {"next": True}), None)
    assert starts(prod) == ["s0"]


# ── auto mode keeps today's behaviour ──────────────────────────────────────

def test_auto_mode_starts_by_itself_and_honours_max_scenes(tmp_path, three_scenes):
    prod, clock = FakeProducer(), Clock()
    c = gm_cfg(start_mode="auto", max_scenes=1, position_file=str(tmp_path / "p.json"))
    llm = FakeLLM(["Dir 0.", verdict(), "Dir 1."])
    gm.table_gm_idle_tick("tuber_0", c, llm, prod, None, clock=clock)
    rt = gm.runtime_for("tuber_0")
    _seat_replies(prod, rt, {"tuber_1": ["A."], "tuber_2": ["B."]})
    gm.table_gm_idle_tick("tuber_0", c, llm, prod, None, clock=clock)
    gm.table_gm_idle_tick("tuber_0", c, llm, prod, None, clock=clock)
    assert starts(prod) == ["s0"]                                      # 1 automatic scene, then idle


def test_request_works_in_auto_mode_too(tmp_path, three_scenes):
    prod = FakeProducer()
    c = gm_cfg(start_mode="auto", max_scenes=0, position_file=str(tmp_path / "p.json"))
    gm.table_gm_idle_tick("tuber_0", c, FakeLLM([]), prod, None, clock=Clock())
    gm.handle_table_message("tuber_0", c, FakeLLM(["Dir."]), prod, op("scene_request", {"scene_id": "s1"}), None)
    assert starts(prod) == ["s1"]


# ── wiring ─────────────────────────────────────────────────────────────────

def test_control_types_registered_and_role_checked():
    from agent_handlers import MESSAGE_HANDLERS
    for t in (control.SCENE_REQUEST, control.SCENE_STOP, control.STATUS_REQUEST):
        assert MESSAGE_HANDLERS[t] is gm.handle_table_message
    prod = FakeProducer()
    gm.handle_table_message("tuber_3", {"role": "table_seat"}, None, prod,
                            op("scene_request", {"next": True}), None)
    assert prod.sent == [] and gm.runtime_for("tuber_3") is None
