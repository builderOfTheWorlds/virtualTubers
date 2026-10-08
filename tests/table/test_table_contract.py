"""P3.2 tests for app/table/contract.py (scene contracts from generator leaf slots)."""
import json
import pathlib

import pytest

from pending import require

arc_source = require("table.arc_source", "app/table/arc_source.py", wp="P3.2")
contract = require("table.contract", "app/table/contract.py", wp="P3.2")

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
DND_RUN = "ashiorid_1_20260913_180158_ce8d"
OFFICE_RUN = "ashiorid_office_20260929_135927_9c1b"

DND_SEATS = {"gm": "tuber_0", "chadwick": "tuber_1", "Leena": "tuber_2",
             "Vigil": "tuber_3", "sodacan_bob": "tuber_4"}
DND_CAST = ("gm", "chadwick", "Leena", "Vigil", "sodacan_bob")
# docker-compose.office.yml header comment
OFFICE_SEATS = {"ceo": "tuber_0", "tech_lead": "tuber_1", "analyst": "tuber_2",
                "engineer": "tuber_3", "tester": "tuber_4", "marketing": "tuber_5",
                "office_manager": "tuber_6", "party_member": "tuber_7"}
OFFICE_CAST = tuple(OFFICE_SEATS)


def _build(run_id, seats, cast):
    run = arc_source.RunArtifacts.from_json(FIX / f"{run_id}.json")
    return run, contract.contracts_for_run(run, seats, pack_cast=cast)


@pytest.fixture(scope="module")
def dnd():
    return _build(DND_RUN, DND_SEATS, DND_CAST)


@pytest.fixture(scope="module")
def office():
    return _build(OFFICE_RUN, OFFICE_SEATS, OFFICE_CAST)


def _n_slots(run):
    return sum(len(n.get("slots") or []) for t in run.trees.values() for n in t.values())


@pytest.mark.parametrize("which", ["dnd", "office"])
def test_every_slot_becomes_a_contract(which, request):
    run, cs = request.getfixturevalue(which)
    assert len(cs) > 0
    assert len(cs) == _n_slots(run)
    assert [c.order for c in cs] == list(range(len(cs)))
    assert len({c.scene_id for c in cs}) == len(cs)


@pytest.mark.parametrize("which", ["dnd", "office"])
def test_order_deterministic_and_follows_segments(which, request):
    run, cs = request.getfixturevalue(which)
    seats, cast = (DND_SEATS, DND_CAST) if which == "dnd" else (OFFICE_SEATS, OFFICE_CAST)
    again = contract.contracts_for_run(run, seats, pack_cast=cast)
    assert [c.scene_id for c in again] == [c.scene_id for c in cs]
    seg_rank = {s["id"]: i for i, s in enumerate(run.segments())}
    ranks = [seg_rank[c.segment_id] for c in cs]
    assert ranks == sorted(ranks)


def test_depth_first_within_segment():
    tree = {
        "s": {"kind": "branch", "order": 0, "parent_id": None, "children": ["s-n1", "s-n0"], "slots": []},
        "s-n0": {"kind": "branch", "order": 0, "parent_id": "s", "children": ["s-n0-n0"], "slots": []},
        "s-n0-n0": {"kind": "leaf", "order": 0, "parent_id": "s-n0", "children": [],
                    "slots": [{"slot_id": "a", "prompt": "A", "participants": ["gm"]},
                              {"slot_id": "b", "prompt": "B", "participants": ["gm"]}]},
        "s-n1": {"kind": "leaf", "order": 1, "parent_id": "s", "children": [],
                 "slots": [{"slot_id": "c", "prompt": "C", "participants": ["gm"]}]},
    }
    run = arc_source.RunArtifacts.from_rows("r", [
        ("arc_plan", None, {"segments": [{"id": "s", "order": 0, "continuity_out": "Seg end."}]}),
        ("tree", "s", tree)])
    cs = contract.contracts_for_run(run, {"gm": "tuber_0"}, pack_cast=["gm"])
    assert [c.canon_goal for c in cs] == ["A", "B", "C"]
    assert cs[0].scene_id == "s.s-n0-n0.a"
    assert cs[2].must_resolve == ("Seg end",)  # leaf has none -> segment fallback


def test_dnd_participants_all_seated(dnd):
    _, cs = dnd
    allowed = set(DND_SEATS.values())
    assert not [w for w in cs.warnings if "participant" in w], cs.warnings
    for c in cs:
        assert c.participants and set(c.participants) <= allowed
    seen = {s for c in cs for s in c.participants}
    # all four players + GM appear somewhere in the run
    assert seen == allowed


def test_office_participants_all_seated(office):
    _, cs = office
    allowed = set(OFFICE_SEATS.values())
    assert not [w for w in cs.warnings if "participant" in w], cs.warnings
    for c in cs:
        assert c.participants and set(c.participants) <= allowed


def test_participant_case_normalised():
    run = arc_source.RunArtifacts.from_rows("r", [
        ("arc_plan", None, {"segments": [{"id": "s", "order": 0}]}),
        ("tree", "s", {"s": {"kind": "leaf", "order": 0, "parent_id": None, "children": [],
                             "continuity_out": "x",
                             "slots": [{"slot_id": "a", "prompt": "A",
                                        "participants": ["LEENA", "vigil", "Chadwick", "Leena"]}]}})])
    cs = contract.contracts_for_run(run, DND_SEATS, pack_cast=DND_CAST)
    assert cs[0].participants == ("tuber_2", "tuber_3", "tuber_1")


def test_unmapped_participants_warned_not_silent():
    run = arc_source.RunArtifacts.from_rows("r", [
        ("arc_plan", None, {"segments": [{"id": "s", "order": 0}]}),
        ("tree", "s", {"s": {"kind": "leaf", "order": 0, "parent_id": None, "children": [],
                             "slots": [{"slot_id": "a", "prompt": "A",
                                        "participants": ["Alcinoe", "Leena", "sodacan_bob"]}]}})])
    seats = {k: v for k, v in DND_SEATS.items() if k != "sodacan_bob"}
    cs = contract.contracts_for_run(run, seats, pack_cast=DND_CAST)
    assert cs[0].participants == ("tuber_2",)
    assert any("Alcinoe" in w and "not in the pack cast" in w for w in cs.warnings)
    assert any("sodacan_bob" in w and "no seat" in w for w in cs.warnings)


def test_field_mapping_from_fixture(dnd):
    run, cs = dnd
    c = cs[0]
    leaf = run.trees[c.segment_id][c.leaf_id]
    slot = next(s for s in leaf["slots"] if s["slot_id"] == c.slot_id)
    assert c.canon_goal == slot["prompt"].strip()
    assert c.lore == tuple(slot["lore"])
    assert c.continuity_in == leaf["continuity_in"]
    assert c.continuity_out == leaf["continuity_out"]
    assert c.must_resolve == contract.split_items(leaf["continuity_out"])
    assert c.must_resolve
    assert c.expected_beats[-1] == f"kind:{slot['kind']}"
    assert (c.max_rounds, c.max_retries_per_turn) == (4, 2)
    assert c.segment_id == run.segments()[0]["id"]


def test_spine_slot_falls_back_to_leaf_summary(dnd):
    run, cs = dnd
    spine = [c for c in cs if c.scene_ref]
    assert spine, "D&D run has spine slots with scene_ref"
    for c in spine:
        leaf = run.trees[c.segment_id][c.leaf_id]
        assert c.canon_goal == leaf["summary"].strip()
        assert any(c.slot_id in w and "no prompt" in w for w in cs.warnings)
    assert all(c.canon_goal for c in cs)


def test_segments_without_slots_warned(dnd):
    _, cs = dnd
    produced = {c.segment_id for c in cs}
    for seg in ("grovley-revelation-arc", "malvakar-riddle-arc2"):
        assert seg not in produced
        assert f"{seg}: segment produced no contracts" in cs.warnings


def test_split_items_rule():
    assert contract.split_items("A b. C d! E? F; G\nH.") == ("A b", "C d!", "E?", "F", "G", "H")
    assert contract.split_items("") == ()
    assert contract.split_items(None) == ()
    assert contract.split_items("Dr.Who stays.") == ("Dr.Who stays",)


def test_safe_scene_id():
    assert contract.safe_id("seg 1.leaf/2.slot:3") == "seg_1.leaf_2.slot_3"


@pytest.mark.parametrize("which", ["dnd", "office"])
def test_payload_round_trip(which, request):
    _, cs = request.getfixturevalue(which)
    for c in cs:
        payload = c.to_payload()
        assert set(payload) == set(contract.CONTRACT_FIELDS)
        wire = json.loads(json.dumps(payload))
        assert contract.SceneContract.from_payload(wire) == c


def test_payload_is_a_valid_scene_start_contract(dnd):
    protocol = require("table.protocol", "app/table/protocol.py", wp="P3.1")
    _, cs = dnd
    c = cs[0]
    protocol.validate("scene_start", {"scene_id": c.scene_id, "contract": c.to_payload(),
                                      "round": 1, "max_rounds": c.max_rounds,
                                      "seats": list(c.participants)})


def test_from_payload_rejects_bad():
    with pytest.raises(contract.ContractError):
        contract.SceneContract.from_payload({"scene_id": "x"})
    _, cs = _build(OFFICE_RUN, OFFICE_SEATS, OFFICE_CAST)
    bad = cs[0].to_payload()
    bad["participants"] = "tuber_0"
    with pytest.raises(contract.ContractError):
        contract.SceneContract.from_payload(bad)


def test_contract_is_frozen(office):
    _, cs = office
    with pytest.raises(Exception):
        cs[0].canon_goal = "x"


def test_next_contract(office):
    _, cs = office
    assert contract.next_contract(cs, None) == cs[0]
    assert contract.next_contract(cs, cs[0].scene_id) == cs[1]
    assert contract.next_contract(cs, cs[-1].scene_id) is None
    assert contract.next_contract([], None) is None
    with pytest.raises(KeyError):
        contract.next_contract(cs, "nope")
