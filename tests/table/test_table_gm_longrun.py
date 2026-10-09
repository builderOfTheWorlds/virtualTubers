"""Long unattended auto runs (2026-10-09): pacing to the voice queue, a wall-time
budget, and skipping stuck scenes instead of stalling the stream.

Not part of the frozen P3.6 set; these cover TableRuntime options added for the
first hour-long D&D table run (table.auto_pace / auto_duration_s / auto_skip_stuck).
"""
import json

import pytest

from pending import require

gm = require("agent_handlers.table_gm", "app/agent_handlers/table_gm.py", wp="P3.6")
from table import protocol  # noqa: E402

SEATS = ["tuber_1"]
NAMES = {"gm": "GM", "tuber_0": "GM", "tuber_1": "Chadwick"}
CONTRACT = {"scene_id": "s1", "run_id": "r", "segment_id": "seg", "leaf_id": "leaf",
            "slot_id": "slot", "order": 0, "canon_goal": "Open the door.",
            "participants": ["tuber_1"], "lore": [], "continuity_in": "",
            "continuity_out": "", "must_resolve": [], "expected_beats": [],
            "max_rounds": 1, "max_retries_per_turn": 1}


class LLM:
    """Scripted GM: callables are invoked with the call kind, else returned."""

    def __init__(self, script):
        self.script = script

    def complete_stream(self, system_prompt, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, reasoning_budget=None):
        kind = "adjudicate" if "Adjudicate the round" in system_prompt else "direct"
        reply = self.script(kind)
        if isinstance(reply, Exception):
            raise reply
        return "", reply


class Prod:
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


def cfg(**over):
    table = {"seats": SEATS, "gm_seat": "tuber_0", "seat_names": NAMES, "start_mode": "auto",
             "think_s": 60, "speak_s": 45, "auto_advance": True,
             "position_file": "/nonexistent/dir/pos.json"}
    table.update(over)
    return {"role": "table_gm", "table": table}


@pytest.fixture(autouse=True)
def providers(monkeypatch, tmp_path):
    gm.reset_runtimes()
    monkeypatch.setattr(gm, "contracts_provider", lambda c: [
        dict(CONTRACT, scene_id=f"s{i}", order=i) for i in range(1, 7)])
    monkeypatch.setattr(gm, "context_provider", lambda c, k: "CTX")
    yield
    gm.reset_runtimes()


def good(kind):
    if kind == "direct":
        return "The door groans open slowly before the party of travellers."
    return json.dumps({"verdict": "pass", "retake_seat": None, "notes": "ok",
                       "resolved": True, "state_delta": {}})


def starts(prod):
    return [protocol.parse(m)["scene_id"] for m in prod.sent if m["type"] == "scene_start"]


def play_scene(rt, prod, line="Stand back, all of you."):
    """Fake seats: answer every new think_request / turn_assignment / retake."""
    seen = getattr(prod, "_seen", 0)
    while seen < len(prod.sent):
        m = prod.sent[seen]
        seen += 1
        p = m["payload"]
        if m["type"] == "think_request":
            for s in p["seats"]:
                rt.on_message(protocol.build("think_done", s, m["from"],
                              {"scene_id": p["scene_id"], "round": p["round"], "seat": s, "ok": True}))
        elif m["type"] in ("turn_assignment", "retake"):
            rt.on_message(protocol.build("character_reply", p["seat"], m["from"],
                          {"scene_id": p["scene_id"], "round": p["round"], "seat": p["seat"],
                           "text": line, "took": True, "reason": ""}))
    prod._seen = seen


def tick(c, llm, prod, clock):
    gm.table_gm_idle_tick("tuber_0", c, llm, prod, None, clock=clock)


def test_pacing_holds_the_next_scene_until_the_voice_queue_nearly_drains():
    prod, clock, llm = Prod(), Clock(), LLM(good)
    c = cfg(auto_pace={"words_per_s": 2.0, "per_line_s": 5.0, "lead_s": 10.0})
    tick(c, llm, prod, clock)                       # s1 starts (direction committed)
    rt = gm.runtime_for("tuber_0")
    play_scene(rt, prod)
    tick(c, llm, prod, clock)                       # adjudicate -> resolved
    assert starts(prod) == ["s1"]                   # held: queue still airing
    # direction 10 words -> 10 s, reply 5 words -> 7.5 s: queue ends t+17.5, lead 10
    clock.t += 7.4
    tick(c, llm, prod, clock)
    assert starts(prod) == ["s1"]
    clock.t += 0.2
    tick(c, llm, prod, clock)
    assert starts(prod) == ["s1", "s2"]


def test_without_pacing_the_next_scene_starts_at_once():
    prod, clock, llm = Prod(), Clock(), LLM(good)
    c = cfg()
    tick(c, llm, prod, clock)
    play_scene(gm.runtime_for("tuber_0"), prod)
    tick(c, llm, prod, clock)
    assert starts(prod) == ["s1", "s2"]


def test_duration_budget_stops_new_scenes_but_not_the_running_one():
    prod, clock, llm = Prod(), Clock(), LLM(good)
    c = cfg(auto_duration_s=100)
    tick(c, llm, prod, clock)
    rt = gm.runtime_for("tuber_0")
    clock.t += 150                                  # budget spent mid-scene
    play_scene(rt, prod)
    tick(c, llm, prod, clock)
    assert "scene_resolve" in [m["type"] for m in prod.sent]
    tick(c, llm, prod, clock)
    assert starts(prod) == ["s1"]


def test_stuck_adjudication_is_skipped_and_the_run_continues():
    prod, clock = Prod(), Clock()
    state = {"bad": 2}

    def script(kind):
        if kind == "adjudicate" and state["bad"]:
            state["bad"] -= 1
            return "not json at all"
        return good(kind)

    c = cfg()
    llm = LLM(script)
    tick(c, llm, prod, clock)
    rt = gm.runtime_for("tuber_0")
    play_scene(rt, prod)                            # adjudicate fails (1)
    tick(c, llm, prod, clock)                       # fails (2) -> stuck -> auto skip -> s2
    assert "scene_stuck" in [m["type"] for m in prod.sent]
    assert starts(prod) == ["s1", "s2"]


def test_halts_after_consecutive_stuck_scenes():
    prod, clock = Prod(), Clock()
    c = cfg(auto_max_consecutive_stuck=2)
    llm = LLM(lambda kind: RuntimeError("vllm down"))
    for _ in range(20):
        tick(c, llm, prod, clock)
    rt = gm.runtime_for("tuber_0")
    assert starts(prod) == ["s1", "s2", "s3"]       # two skips, third scene stays stuck
    assert rt._halted is True


def test_adjudicate_error_names_the_reply_tail():
    g = gm.LLMGM(LLM(lambda kind: '{"verdict": "pass", "notes": "cut off'), lambda c: "CTX",
                 seat_names=NAMES, budgets={})
    with pytest.raises(ValueError, match="tail="):
        g.adjudicate(dict(CONTRACT), [], 1)


def test_contracts_provider_drops_repeated_canon_goals(monkeypatch, tmp_path):
    from table import arc_source, contract as contract_mod

    class C:
        def __init__(self, sid, goal):
            self.p = dict(CONTRACT, scene_id=sid, canon_goal=goal)

        def to_payload(self):
            return dict(self.p)

    monkeypatch.undo()
    monkeypatch.setattr(arc_source.RunArtifacts, "from_json", classmethod(lambda cls, p: None))
    monkeypatch.setattr(contract_mod, "contracts_for_run", lambda *a, **k: [
        C("a", "Open the door."), C("b", "open the  door."), C("c", "Close it.")])
    base = {"contracts_fixture": "x.json", "seats": ["tuber_1"]}
    ids = [p["scene_id"] for p in gm.contracts_provider({"table": dict(base, dedupe_canon_goal=True)})]
    assert ids == ["a", "c"]
    ids = [p["scene_id"] for p in gm.contracts_provider({"table": base})]
    assert ids == ["a", "b", "c"]


# ── a scene whose goal IS the reveal may say the secret (user decision 2026-10-09) ──
SECRET_LINE = "Grovley speaks at last: you are children of the Begene program."


def _gm(reply):
    return gm.LLMGM(LLM(lambda kind: reply), lambda c: "CTX", seat_names=NAMES,
                    budgets={"gm_forbidden_phrases": ["Begene", "four cribs"]})


def test_forbidden_phrase_still_blocked_in_an_ordinary_scene():
    with pytest.raises(ValueError, match="GM-only"):
        _gm(SECRET_LINE).direct(dict(CONTRACT), [], 1)


@pytest.mark.parametrize("field,value", [
    ("canon_goal", "Grovley reveals the Begene breeding program."),
    ("expected_beats", ["the party learns of the begene program"]),
    ("must_resolve", ["Begene revealed"]),
])
def test_forbidden_phrase_allowed_when_the_scene_goal_is_that_reveal(field, value):
    d = _gm(SECRET_LINE).direct(dict(CONTRACT, **{field: value}), [], 1)
    assert "Begene" in d.text


def test_unlock_covers_only_the_revealed_phrase_and_not_lore_names():
    contract = dict(CONTRACT, canon_goal="Grovley reveals the Begene program.")
    with pytest.raises(ValueError, match="GM-only"):
        _gm("The Begene program kept you in four cribs.").direct(contract, [], 1)
    with pytest.raises(ValueError, match="GM-only"):    # lore alone is no reveal
        _gm(SECRET_LINE).direct(dict(CONTRACT, lore=["the-begene-program"]), [], 1)


def test_direction_prompt_names_only_the_still_locked_phrases():
    seen = {}

    class Rec(LLM):
        def complete_stream(self, system_prompt, messages, **kw):
            seen["system"] = system_prompt
            return "", "The cellar door opens onto cold stone stairs."

    g = gm.LLMGM(Rec(None), lambda c: "CTX", seat_names=NAMES,
                 budgets={"gm_forbidden_phrases": ["Begene", "four cribs"]})
    g.direct(dict(CONTRACT, canon_goal="Grovley reveals the Begene program."), [], 1)
    assert "never use them: four cribs." in seen["system"]
    assert "Begene," not in seen["system"].split("never use them:")[1]


def test_rejection_names_the_reason():
    with pytest.raises(ValueError, match=r"phrases=\['Begene'\]"):
        _gm(SECRET_LINE).direct(dict(CONTRACT), [], 1)
