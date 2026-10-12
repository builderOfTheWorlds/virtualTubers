"""Office loop fix (2026-10-10): the runtime's story memory and cross-scene repeat guards.

Plan: .claude/prompts/ashiorid_office_loop_fix_plan.md P1/P3.
"""
import json

import pytest

from pending import require

gm = require("agent_handlers.table_gm", "app/agent_handlers/table_gm.py", wp="P3.6")
from table import protocol  # noqa: E402
from test_table_gm_longrun import CONTRACT, LLM, Clock, Prod, cfg, good, play_scene, starts, tick  # noqa: E402


@pytest.fixture(autouse=True)
def providers(monkeypatch):
    gm.reset_runtimes()
    seen = {}
    monkeypatch.setattr(gm, "contracts_provider", lambda c: [
        dict(CONTRACT, scene_id=f"s{i}", order=i, canon_goal=f"Goal {i}.") for i in range(1, 7)])

    def ctx(c, k, history="", segment=None):
        seen[k["scene_id"]] = history
        return "CTX"
    monkeypatch.setattr(gm, "context_provider", ctx)
    monkeypatch.setattr(gm, "segments_provider", lambda c: {})
    yield seen
    gm.reset_runtimes()


def _run(c, llm, prod, clock, n, line=lambda i: f"Fresh line number {i} about the door."):
    for i in range(n):
        tick(c, llm, prod, clock)
        play_scene(gm.runtime_for("tuber_0"), prod, line=line(i))
        tick(c, llm, prod, clock)


def test_finished_scenes_reach_gm_history_and_seat_payloads(tmp_path, providers):
    prod, clock, llm = Prod(), Clock(), LLM(good)
    c = cfg(position_file=str(tmp_path / "pos.json"))
    _run(c, llm, prod, clock, 2)
    assert starts(prod)[:3] == ["s1", "s2", "s3"]
    assert providers["s1"] == ""
    assert "[s1] Goal 1." in providers["s2"] and "[s2] Goal 2." in providers["s3"]
    for type_, scene in (("think_request", "s3"), ("turn_assignment", "s2")):
        got = [protocol.parse(m) for m in prod.sent
               if m["type"] == type_ and m["payload"]["scene_id"] == scene]
        assert got and f"[s{int(scene[1]) - 1}]" in got[0]["story"]


def test_story_persists_in_position_file_and_restores(tmp_path):
    pos = tmp_path / "pos.json"
    prod, clock, llm = Prod(), Clock(), LLM(good)
    c = cfg(position_file=str(pos))
    _run(c, llm, prod, clock, 2)
    saved = json.loads(pos.read_text())
    assert [e["scene_id"] for e in saved["story"]] == ["s1", "s2"]
    gm.reset_runtimes()
    rt = gm.TableRuntime("tuber_0", c, llm, Prod(), clock=clock)
    assert [e.scene_id for e in rt.story.entries] == ["s1", "s2"]


def test_run_start_from_beginning_clears_story(tmp_path):
    prod, clock, llm = Prod(), Clock(), LLM(good)
    c = cfg(position_file=str(tmp_path / "pos.json"))
    _run(c, llm, prod, clock, 2)
    rt = gm.runtime_for("tuber_0")
    rt._end_running("test")
    tick(c, llm, prod, clock)
    from message_bus import build_message
    from table import control
    assert len(rt.story) >= 2
    rt.on_message(build_message("operator", "tuber_0", control.RUN_START, {"from": "beginning"}))
    assert len(rt.story) == 0


def test_story_log_off_keeps_history_empty(tmp_path, providers):
    prod, clock, llm = Prod(), Clock(), LLM(good)
    c = cfg(position_file=str(tmp_path / "pos.json"), story_log=False)
    _run(c, llm, prod, clock, 2)
    assert providers["s3"] == ""


def test_cross_scene_catchphrase_is_retaken(tmp_path):
    prod, clock, llm = Prod(), Clock(), LLM(good)
    c = cfg(position_file=str(tmp_path / "pos.json"),
            cross_scene_repeat={"scenes": 2, "ngram": 6})
    phrase = "Three things, then I'll let you go."
    _run(c, llm, prod, clock, 2, line=lambda i: f"{phrase} Item {i} is the milk order today.")
    retakes = [protocol.parse(m) for m in prod.sent if m["type"] == "retake"]
    assert retakes and retakes[0]["scene_id"] == "s2"
    assert "earlier scene" in retakes[0]["reason"]


def test_build_check_cross_scene_off_without_config():
    check = gm.build_check({"seats": ["tuber_1"], "seat_names": {"tuber_1": "Nora"}},
                           recent_fn=lambda: ("Three things, then I'll let you go.",))
    assert check("tuber_1", "Right. Three things, then I'll let you go: coffee first.").ok
    check_on = gm.build_check({"seats": ["tuber_1"], "seat_names": {"tuber_1": "Nora"},
                               "cross_scene_repeat": {"ngram": 6}},
                              recent_fn=lambda: ("Three things, then I'll let you go.",))
    res = check_on("tuber_1", "Right. Three things, then I'll let you go: coffee first.")
    assert not res.ok and res.code == "repeats"
    assert check_on("tuber_1", "Coffee is on and the post tray is sorted.").ok


def test_shared_ngram():
    assert gm.shared_ngram("a b c d e f g", ["x a b c d e f y"], n=6) is not None
    assert gm.shared_ngram("a b c d e", ["a b c d e"], n=6) is None
    assert gm.shared_ngram("a b c d e f", [], n=6) is None


class _SeqLLM:
    def __init__(self, replies):
        self.replies, self.systems = list(replies), []

    def complete_stream(self, system_prompt, messages, **kw):
        self.systems.append(system_prompt)
        return "", self.replies.pop(0)


def _gm(llm, recent):
    return gm.LLMGM(llm, lambda c: "CTX", seat_names={}, budgets={},
                    recent_fn=lambda: recent, repeat_threshold=0.5)


OPENING = "The emergency strip hums as Nora steps into the dim glow of the front entrance."


def test_gm_direction_repeating_earlier_scene_is_asked_again():
    llm = _SeqLLM([OPENING, "Nora sets out the oat milk while the coffee machine warms."])
    d = _gm(llm, (OPENING,)).direct(dict(CONTRACT), [], 1)
    assert d.text.startswith("Nora sets out")
    assert len(llm.systems) == 2 and "already happened" in llm.systems[1]


def test_gm_direction_second_repeat_is_committed_not_blocked():
    llm = _SeqLLM([OPENING, OPENING])
    d = _gm(llm, (OPENING,)).direct(dict(CONTRACT), [], 1)
    assert d.text == OPENING and len(llm.systems) == 2


def test_gm_silent_you_line_is_asked_again_then_rejected():
    bad = 'You close the notebook. "Fair?" you ask, looking up.'
    good_text = "You close the notebook and look up without a word."
    llm = _SeqLLM([bad, good_text])
    g = gm.LLMGM(llm, lambda c: "CTX", seat_names={}, budgets={"gm_silent_you": True})
    assert g.direct(dict(CONTRACT), [], 1).text == good_text
    llm2 = _SeqLLM([bad, bad])
    g2 = gm.LLMGM(llm2, lambda c: "CTX", seat_names={}, budgets={"gm_silent_you": True})
    with pytest.raises(ValueError):
        g2.direct(dict(CONTRACT), [], 1)
    llm3 = _SeqLLM([bad])        # rule off: committed as before
    assert gm.LLMGM(llm3, lambda c: "CTX", seat_names={}, budgets={}).direct(
        dict(CONTRACT), [], 1).text == bad


def test_gm_direction_without_recent_is_one_call():
    llm = _SeqLLM([OPENING])
    gm.LLMGM(llm, lambda c: "CTX", seat_names={}, budgets={}).direct(dict(CONTRACT), [], 1)
    assert len(llm.systems) == 1
