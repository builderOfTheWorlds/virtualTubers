"""Tests for scripts/validate_office_profiles.py (OB-10c).

Builds a synthetic, fully valid 8-character pack in tmp_path, then mutates one
thing per parametrized case. The script is not a module, so it is loaded by path.
"""
import importlib.util
import json
import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "validate_office_profiles.py"


@pytest.fixture(scope="module")
def vop():
    spec = importlib.util.spec_from_file_location("_validate_office_profiles", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _words(n, word="ledger"):
    return " ".join([word] * n)


def _cast(vop, cid):
    others = [o for o in vop.IDS if o != cid][:3]
    prompt = (f"You are the {cid}. " + _words(200) + " " + vop.LOOP_SENTENCE)
    if cid == "party_member":
        prompt += " " + vop.OBSERVER_SENTENCE
    seat = f"tuber_{vop.IDS.index(cid)}"
    return {
        "name": f"Name {cid}",
        "archetype": "steady hand",
        "voice": vop.VOICE_FOR[cid],
        "avatar": None,
        "system_prompt": prompt,
        "seat": seat,
        "office_role": cid,
        "wants": ["a", "b"],
        "fears": ["c", "d"],
        "speech": "short sentences",
        "relationships": {o: "one line" for o in others},
        "knowledge": ["knows-the-office", "trusts-tech-lead"],
        "turn_order_pos": vop.IDS.index(cid),
    }


def _profile(vop, cid):
    nodes = [{"name": n, "statement": "I do."} for n in (
        "knows-the-office", "trusts-tech-lead", "lives-alone", "wants-quiet-days",
        "fears-audits", "likes-coffee", "has-a-cat", "remembers-first-day")]
    return {
        "id": cid,
        "retains_fragments": True,
        "is_main": True,
        "identity": {
            "full_name": f"Name {cid}", "age": 30, "pronouns": "they/them",
            "title": cid.title(), "seat": f"tuber_{vop.IDS.index(cid)}",
            "tenure_months": vop.TENURE_MONTHS[cid], "home": "flat",
            "commute": "bus", "hobbies": ["chess"],
        },
        "appearance": _words(100, "tall"),
        "personality": {
            "traits": ["calm", "dry", "exact", "loyal"], "speech_tics": ["well"],
            "work_style": "methodical", "stress_response": "goes quiet",
        },
        "objectives": {"wants": ["x"], "fears": ["y"], "secrets": ["z"]},
        "backstory": {"believed": "I " + _words(700, "worked"), "truth": "open"},
        "backstory_nodes": nodes,
        "behaviour_contract": [vop.LOOP_SENTENCE],
    }


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def pack(tmp_path, vop):
    root = tmp_path / "pack"
    for cid in vop.IDS:
        _write(root / "cast" / f"{cid}.yaml", _cast(vop, cid))
        _write(root / "profiles" / f"{cid}.yaml", _profile(vop, cid))
    return root


def _mutate(root, kind, cid, fn):
    path = root / kind / f"{cid}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    _write(path, data)


def test_valid_pack_passes_with_no_errors_or_warnings(vop, pack):
    report = vop.validate_pack(pack)
    assert report["ok"], vop.format_report(report)
    assert report["errors"] == 0
    assert report["warnings"] == 0
    assert set(report["characters"]) == set(vop.IDS)


def _del(key):
    return lambda d: d.pop(key)


def _set(dotted, value):
    def fn(d):
        parts = dotted.split(".")
        for p in parts[:-1]:
            d = d[p]
        d[parts[-1]] = value
    return fn


ERROR_CASES = [
    ("cast", "engineer", _del("speech"), "missing key 'speech'"),
    ("cast", "engineer", _set("turn_order_pos", "3"), "turn_order_pos"),
    ("cast", "engineer", _set("wants", "not a list"), "'wants' has wrong type"),
    ("cast", "engineer", _set("seat", "tuber_4"), "seat 'tuber_4'"),
    ("cast", "engineer", _set("turn_order_pos", 4), "turn_order_pos 4"),
    ("cast", "engineer", _set("office_role", "janitor"), "not a valid OfficeRole"),
    ("cast", "engineer", _set("office_role", "tester"), "does not match file id"),
    ("cast", "engineer", _set("voice", "nobody_voice"), "not in config/voices.yaml"),
    ("cast", "engineer", _set("voice", "bass_low"), "schema voice"),
    ("cast", "engineer", _set("knowledge", ["knows-nothing-here"]), "knowledge not in"),
    ("cast", "engineer", _set("relationships", {"engineer": "me", "ceo": "b", "tester": "c"}),
     "self key"),
    ("cast", "engineer", _set("relationships", {"janitor": "a", "ceo": "b", "tester": "c"}),
     "not a cast id"),
    ("cast", "engineer", _set("system_prompt", "You are the engineer. " + _words(200)),
     "time repeats"),
    ("cast", "party_member",
     _set("system_prompt", "You are him. " + _words(200) + " Never state or imply that time repeats."),
     "You never speak"),
    ("cast", "engineer", _set("system_prompt", "You are in a simulation. " + _words(200)
                              + " Never state or imply that time repeats."), "banned"),
    ("cast", "engineer", _set("system_prompt", "You mind the Stream. " + _words(200)
                              + " Never state or imply that time repeats."), "stream"),
    ("cast", "engineer", _set("system_prompt", "You hate the AI. " + _words(200)
                              + " Never state or imply that time repeats."), "'ai'"),
    ("profiles", "engineer", _set("backstory.believed", "I am an NPC. " + _words(700)), "npc"),
    ("profiles", "engineer", _set("backstory.believed", "A time loop. " + _words(700)),
     "time loop"),
    ("profiles", "engineer", _set("id", "tester"), "profile id"),
    ("profiles", "engineer", _set("identity.tenure_months", 12), "tenure_months"),
    ("profiles", "engineer", _set("identity.seat", "tuber_0"), "identity.seat"),
    ("profiles", "engineer", _set("identity.age", "thirty"), "identity.age"),
    ("profiles", "engineer", _del("identity"), "missing key 'identity'"),
    ("profiles", "engineer", _set("retains_fragments", "yes"), "retains_fragments"),
    ("profiles", "engineer", lambda d: d["backstory_nodes"].append(
        {"name": "Knows_Bad", "statement": "x"}), "fails regex"),
    ("profiles", "engineer", lambda d: d["backstory_nodes"].append(
        {"name": "singleword", "statement": "x"}), "fails regex"),
    ("profiles", "engineer", lambda d: d["backstory_nodes"].append(
        {"name": "runs-the-show", "statement": "x"}), "allowlisted verb"),
    ("profiles", "engineer", lambda d: d["backstory_nodes"].append(
        {"name": "likes-coffee", "statement": "again"}), "duplicate node name"),
]


@pytest.mark.parametrize("kind,cid,fn,needle", ERROR_CASES,
                         ids=[f"{k}-{c}-{n[:24]}" for k, c, _, n in ERROR_CASES])
def test_single_mutation_reports_error(vop, pack, kind, cid, fn, needle):
    _mutate(pack, kind, cid, fn)
    report = vop.validate_pack(pack)
    assert not report["ok"]
    errs = report["characters"][cid]["errors"]
    assert any(needle.lower() in e.lower() for e in errs), errs
    for other in vop.IDS:
        if other != cid:
            assert report["characters"][other]["errors"] == [], other


@pytest.mark.parametrize("kind", ["cast", "profiles"])
def test_missing_file_reports_error(vop, pack, kind):
    (pack / kind / "analyst.yaml").unlink()
    report = vop.validate_pack(pack)
    assert not report["ok"]
    assert any("file missing" in e for e in report["characters"]["analyst"]["errors"])


def test_unparseable_yaml_reports_error(vop, pack):
    (pack / "cast" / "ceo.yaml").write_text("name: [unclosed", encoding="utf-8")
    report = vop.validate_pack(pack)
    assert any("cannot parse" in e for e in report["characters"]["ceo"]["errors"])


def test_extra_non_office_file_is_pack_error(vop, pack):
    _write(pack / "cast" / "janitor.yaml", {"name": "J"})
    report = vop.validate_pack(pack)
    assert not report["ok"]
    assert any("janitor" in e for e in report["pack_errors"])


def test_schema_file_is_ignored(vop, pack):
    (pack / "profiles" / "_SCHEMA.md").write_text("# schema", encoding="utf-8")
    _write(pack / "profiles" / "_notes.yaml", {"x": 1})
    assert vop.validate_pack(pack)["ok"]


WARN_CASES = [
    ("profiles", _set("backstory.believed", "I " + _words(100)), "backstory.believed is"),
    ("profiles", _set("appearance", _words(20)), "appearance is"),
    ("cast", _set("system_prompt", "You. " + _words(20) + " Never state or imply that time repeats."),
     "system_prompt is"),
    ("cast", _set("wants", ["only one"]), "'wants' has 1"),
    ("profiles", _set("personality.traits", ["one"]), "personality.traits"),
    ("profiles", _set("behaviour_contract", ["be nice"]), "behaviour_contract lacks"),
]


@pytest.mark.parametrize("kind,fn,needle", WARN_CASES, ids=[n for _, _, n in WARN_CASES])
def test_soft_violation_is_warning_not_error(vop, pack, kind, fn, needle):
    _mutate(pack, kind, "marketing", fn)
    report = vop.validate_pack(pack)
    assert report["ok"], report["characters"]["marketing"]["errors"]
    warns = report["characters"]["marketing"]["warnings"]
    assert any(needle in w for w in warns), warns


@pytest.mark.parametrize("text,expected", [
    ("a looping tune", ["looping"]),
    ("the loophole", []),
    ("said Aiden", []),
    ("scripted", []),
    ("artificial intelligence here", ["artificial intelligence"]),
    ("npc and Simulated", ["npc", "simulated"]),
])
def test_banned_hits_whole_words_only(vop, text, expected):
    assert vop.banned_hits(text) == expected


def test_main_exit_codes_and_json(vop, pack, capsys):
    assert vop.main(["--pack", str(pack), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and len(data["characters"]) == 8

    _mutate(pack, "cast", "tester", _set("seat", "tuber_9"))
    assert vop.main(["--pack", str(pack)]) == 1
    out = capsys.readouterr().out
    assert "tester" in out and "FAIL" in out


def test_missing_voices_registry_is_pack_error(vop, pack, tmp_path):
    report = vop.validate_pack(pack, voices_path=tmp_path / "nope.yaml")
    assert not report["ok"]
    assert report["pack_errors"]


def test_constants_cover_every_office_role(vop):
    assert set(vop.VOICE_FOR) == set(vop.IDS) == set(vop.TENURE_MONTHS)
    assert set(vop.VOICE_FOR.values()) <= vop.load_voice_names(REPO / "config" / "voices.yaml")
