"""P3.6 frozen tests for app/agent_handlers/table_gm.py: the GM agent + table runtime.

Build plan P3.6. The GM worker (role table_gm) owns the turn arbiter (app/turns.py):
- LLMGM implements the arbiter's GMPort over the shared model (direction,
  JSON adjudication, overrule line), with per-call token + reasoning budgets;
- TableRuntime loads scene contracts, starts an Arbiter per scene, feeds it
  think_done / character_reply / operator_override, ticks it from the idle hook
  and advances to the next scene when one resolves;
- commit validation (table.commit_check) is wired per seat.
"""
import json

import pytest

from pending import require

gm = require("agent_handlers.table_gm", "app/agent_handlers/table_gm.py", wp="P3.6")
from table import protocol  # noqa: E402

SEATS = ["tuber_1", "tuber_2"]
NAMES = {"gm": "GM", "tuber_0": "GM", "tuber_1": "Chadwick", "tuber_2": "Leena"}
CONTRACT = {"scene_id": "s1", "run_id": "r", "segment_id": "seg", "leaf_id": "leaf",
            "slot_id": "slot", "order": 0, "canon_goal": "Open the vault door.",
            "participants": ["tuber_1", "tuber_2"], "lore": [], "continuity_in": "",
            "continuity_out": "Door open.", "must_resolve": ["door opened"],
            "expected_beats": [], "max_rounds": 2, "max_retries_per_turn": 1}


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete_stream(self, system_prompt, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, reasoning_budget=None):
        self.calls.append({"system": system_prompt, "messages": messages,
                           "max_tokens": max_tokens, "reasoning_budget": reasoning_budget})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return "gm reasoning", reply


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, m):
        self.sent.append(m)
        return m


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def gm_cfg(**over):
    table = {"seats": SEATS, "gm_seat": "tuber_0", "seat_names": NAMES,
             "think_s": 60, "speak_s": 45, "auto_advance": True,
             "direct": {"max_tokens": 700, "reasoning_budget": 300},
             "adjudicate": {"max_tokens": 200, "reasoning_budget": 64},
             "overrule": {"max_tokens": 120, "reasoning_budget": 32},
             "forbidden_phrases": {"tuber_2": ["four cribs"]}}
    table.update(over)
    return {"role": "table_gm", "table": table}


@pytest.fixture(autouse=True)
def providers(monkeypatch):
    gm.reset_runtimes()
    monkeypatch.setattr(gm, "contracts_provider", lambda agent_config: [dict(CONTRACT),
                        dict(CONTRACT, scene_id="s2", order=1)])
    monkeypatch.setattr(gm, "context_provider", lambda agent_config, contract: "GM CONTEXT " + contract["scene_id"])
    yield
    gm.reset_runtimes()


def verdict(**kw):
    base = {"verdict": "pass", "retake_seat": None, "notes": "fine", "resolved": True, "state_delta": {"door": "open"}}
    base.update(kw)
    return json.dumps(base)


# ── LLMGM ───────────────────────────────────────────────────────────────────

def test_direct_strips_gm_label_and_sets_expects_and_must_resolve():
    llm = FakeLLM(["GM: The door groans. Chadwick, Leena: react."])
    port = gm.LLMGM(llm, lambda c: "CTX", seat_names=NAMES, budgets=gm_cfg()["table"])
    d = port.direct(dict(CONTRACT), [], 1)
    assert d.text == "The door groans. Chadwick, Leena: react."
    assert d.expects == ["tuber_1", "tuber_2"]
    assert "door opened" in d.must_resolve
    call = llm.calls[0]
    assert call["system"].startswith("CTX")
    assert (call["max_tokens"], call["reasoning_budget"]) == (700, 300)


@pytest.mark.parametrize("raw,expect", [
    (verdict(), ("pass", None, True)),
    ('Sure! {"verdict": "reject", "retake_seat": "Leena", "notes": "leak", "resolved": false}', ("reject", "tuber_2", False)),
    ('```json\n{"verdict": "retake", "retake_seat": "tuber_1", "notes": "x"}\n```', ("reject", "tuber_1", False)),
])
def test_adjudicate_parses_json_maps_names_to_seats(raw, expect):
    port = gm.LLMGM(FakeLLM([raw]), lambda c: "CTX", seat_names=NAMES, budgets=gm_cfg()["table"])
    v = port.adjudicate(dict(CONTRACT), [{"speaker": "tuber_1", "text": "hi", "kind": "reply"}], 1)
    assert (v.verdict, v.retake_seat, v.resolved) == expect


def test_adjudicate_unparsable_raises_so_the_arbiter_retries():
    port = gm.LLMGM(FakeLLM(["I think it went well."]), lambda c: "CTX", seat_names=NAMES,
                    budgets=gm_cfg()["table"])
    with pytest.raises(ValueError):
        port.adjudicate(dict(CONTRACT), [], 1)


def test_adjudicate_unknown_retake_seat_becomes_none():
    port = gm.LLMGM(FakeLLM([verdict(verdict="reject", retake_seat="Gandalf", resolved=False)]),
                    lambda c: "CTX", seat_names=NAMES, budgets=gm_cfg()["table"])
    assert port.adjudicate(dict(CONTRACT), [], 1).retake_seat is None


def test_overrule_returns_clean_line():
    port = gm.LLMGM(FakeLLM(['Leena: "We wait here."']), lambda c: "CTX", seat_names=NAMES,
                    budgets=gm_cfg()["table"])
    assert port.overrule(dict(CONTRACT), [], "tuber_2", "deadline") == "We wait here."


# ── check wiring ────────────────────────────────────────────────────────────

def test_check_uses_cast_names_and_per_seat_forbidden_phrases():
    check = gm.build_check(gm_cfg()["table"])
    assert check("tuber_1", "Leena: open it.").code == "speaks_for_other"
    assert check("tuber_2", "I remember four cribs.").code == "forbidden_leak"
    assert check("tuber_1", "I remember four cribs.").ok      # only tuber_2 is barred from it


# ── runtime end to end ──────────────────────────────────────────────────────

def _seat_replies(prod, rt, worker_lines):
    """Fake seats: answer every think_request / turn_assignment / retake addressed to them."""
    handled = 0
    while handled < len(prod.sent):
        m = prod.sent[handled]
        handled += 1
        p = m["payload"]
        if m["type"] == "think_request":
            for s in p["seats"]:
                rt.on_message(protocol.build("think_done", s, m["from"],
                              {"scene_id": p["scene_id"], "round": p["round"], "seat": s, "ok": True}))
        elif m["type"] in ("turn_assignment", "retake"):
            text = worker_lines[p["seat"]].pop(0)
            rt.on_message(protocol.build("character_reply", p["seat"], m["from"],
                          {"scene_id": p["scene_id"], "round": p["round"], "seat": p["seat"],
                           "text": text, "took": True, "reason": ""}))


def test_full_scene_runs_and_advances_to_next_contract():
    prod, clock = FakeProducer(), Clock()
    llm = FakeLLM(["The vault door groans open.", verdict(),          # scene s1
                   "A second scene begins."])                           # scene s2 direction
    cfg = gm_cfg()
    gm.table_gm_idle_tick("tuber_0", cfg, llm, prod, None, clock=clock)   # starts s1
    rt = gm.runtime_for("tuber_0")
    _seat_replies(prod, rt, {"tuber_1": ["Stand back."], "tuber_2": ["It hums."]})
    gm.table_gm_idle_tick("tuber_0", cfg, llm, prod, None, clock=clock)   # resolved -> start s2
    types = [m["type"] for m in prod.sent]
    assert types[:3] == ["scene_start", "scene_direction", "think_request"]
    assert "scene_resolve" in types
    resolve = next(m for m in prod.sent if m["type"] == "scene_resolve")
    assert protocol.parse(resolve)["state_delta"] == {"door": "open"}
    starts = [protocol.parse(m)["scene_id"] for m in prod.sent if m["type"] == "scene_start"]
    assert starts == ["s1", "s2"]
    for m in prod.sent:
        protocol.parse(m)
        assert m["from"] == "tuber_0"          # the arbiter speaks as the GM worker


def test_commit_check_failure_triggers_retake_through_runtime():
    prod, clock = FakeProducer(), Clock()
    llm = FakeLLM(["Direction.", verdict()])
    cfg = gm_cfg()
    gm.table_gm_idle_tick("tuber_0", cfg, llm, prod, None, clock=clock)
    rt = gm.runtime_for("tuber_0")
    _seat_replies(prod, rt, {"tuber_1": ["*draws sword* Back!", "Back, all of you."],
                             "tuber_2": ["It hums."]})
    retakes = [protocol.parse(m) for m in prod.sent if m["type"] == "retake"]
    assert retakes and retakes[0]["seat"] == "tuber_1"
    assert retakes[0]["reason"].startswith("stage_direction")


def test_handle_table_message_routes_only_on_gm_role():
    prod, clock = FakeProducer(), Clock()
    gm.table_gm_idle_tick("tuber_0", gm_cfg(), FakeLLM(["D."]), prod, None, clock=clock)
    msg = protocol.build("think_done", "tuber_1", "tuber_0",
                         {"scene_id": "s1", "round": 1, "seat": "tuber_1", "ok": True})
    gm.handle_table_message("tuber_3", {"role": "table_seat"}, None, prod, msg, None)  # ignored
    assert gm.runtime_for("tuber_3") is None
    gm.handle_table_message("tuber_0", gm_cfg(), None, prod, msg, None)
    assert "tuber_1" in gm.runtime_for("tuber_0").arbiter.state["think_done"]


def test_idle_tick_never_raises_and_ignores_other_roles():
    prod = FakeProducer()
    gm.table_gm_idle_tick("tuber_1", {"role": "table_seat"}, None, prod, None)
    assert prod.sent == []
    gm.table_gm_idle_tick("tuber_0", gm_cfg(), FakeLLM([RuntimeError("down")] * 5), prod, None,
                          clock=Clock())     # GM direction fails: logged, no exception


def test_no_contracts_means_idle(monkeypatch):
    prod = FakeProducer()
    monkeypatch.setattr(gm, "contracts_provider", lambda agent_config: [])
    gm.table_gm_idle_tick("tuber_0", gm_cfg(), FakeLLM([]), prod, None, clock=Clock())
    assert prod.sent == []
