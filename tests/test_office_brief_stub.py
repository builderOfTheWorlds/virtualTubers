"""Tests for app/office/brief_stub.py (E6 stub persona prompt). Character
files are built in tmp_path — never the real campaigns/ashiorid_office pack."""
import pytest
import yaml

from office import brief_stub
from office.brief_stub import (
    BACKSTORY_HEADING,
    DIRECTIVE_HEADING,
    NO_DIRECTIVE_TEXT,
    BriefError,
    build_persona_prompt,
    character_id,
    load_character,
    resolve_pack_dir,
)
from office.roles import OfficeRole


def write_character(pack, cid, system_prompt="You are the Analyst.", believed="I grew up by the sea.",
                    profile=True):
    (pack / "cast").mkdir(parents=True, exist_ok=True)
    (pack / "profiles").mkdir(parents=True, exist_ok=True)
    cast = {"name": "Test Person", "system_prompt": system_prompt, "office_role": cid}
    (pack / "cast" / f"{cid}.yaml").write_text(yaml.safe_dump(cast), encoding="utf-8")
    if profile:
        data = {"id": cid, "backstory": {"believed": believed, "truth": "GM ONLY SECRET"}}
        (pack / "profiles" / f"{cid}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def pack(tmp_path):
    root = tmp_path / "pack"
    write_character(root, "analyst")
    return root


@pytest.mark.parametrize("value,expected", [
    (OfficeRole.TECH_LEAD, "tech_lead"), ("office_manager", "office_manager"), ("tuber_7", "party_member"),
])
def test_character_id_accepts_roles_values_and_seats(value, expected):
    assert character_id(value) == expected


def test_character_id_unknown_role_raises():
    with pytest.raises(ValueError):
        character_id("janitor")


def test_resolve_pack_dir_argument_beats_env_beats_default(monkeypatch, tmp_path):
    monkeypatch.delenv(brief_stub.PACK_DIR_ENV, raising=False)
    assert resolve_pack_dir() == brief_stub.DEFAULT_PACK_DIR
    monkeypatch.setenv(brief_stub.PACK_DIR_ENV, str(tmp_path / "env"))
    assert resolve_pack_dir() == tmp_path / "env"
    assert resolve_pack_dir(tmp_path / "arg") == tmp_path / "arg"


def test_build_persona_prompt_has_all_three_parts_in_order(pack):
    prompt = build_persona_prompt("analyst", "Ship the velocity rule.", pack_dir=pack)
    assert prompt.startswith("You are the Analyst.")
    assert prompt.index(BACKSTORY_HEADING) < prompt.index(DIRECTIVE_HEADING)
    assert "I grew up by the sea." in prompt
    assert prompt.rstrip().endswith("Ship the velocity rule.")


def test_build_persona_prompt_never_leaks_truth_layer(pack):
    assert "GM ONLY SECRET" not in build_persona_prompt("analyst", pack_dir=pack)


def test_build_persona_prompt_without_directive_says_none_yet(pack):
    assert NO_DIRECTIVE_TEXT in build_persona_prompt("analyst", None, pack_dir=pack)


def test_build_persona_prompt_directive_dict_includes_title_and_issue(pack):
    prompt = build_persona_prompt(
        "analyst", {"text": "Add a country rule.", "title": "Country rule", "issue": 7}, pack_dir=pack)
    assert "Country rule (issue #7)\nAdd a country rule." in prompt


def test_build_persona_prompt_missing_profile_omits_backstory(tmp_path):
    write_character(tmp_path, "tester", system_prompt="You are the Tester.", profile=False)
    prompt = build_persona_prompt(OfficeRole.TESTER, "Run it.", pack_dir=tmp_path)
    assert BACKSTORY_HEADING not in prompt
    assert prompt.startswith("You are the Tester.")


def test_build_persona_prompt_truncates_backstory_on_word_boundary(tmp_path):
    write_character(tmp_path, "analyst", believed="alpha beta gamma delta epsilon")
    prompt = build_persona_prompt("analyst", pack_dir=tmp_path, max_backstory_chars=12)
    assert "alpha beta ..." in prompt and "gamma" not in prompt


def test_build_persona_prompt_missing_cast_raises(tmp_path):
    with pytest.raises(BriefError, match="not found"):
        build_persona_prompt("ceo", pack_dir=tmp_path)


def test_build_persona_prompt_empty_system_prompt_raises(tmp_path):
    write_character(tmp_path, "ceo", system_prompt="   ")
    with pytest.raises(BriefError, match="no system_prompt"):
        build_persona_prompt("ceo", pack_dir=tmp_path)


def test_load_character_malformed_cast_raises(tmp_path):
    (tmp_path / "cast").mkdir()
    (tmp_path / "cast" / "ceo.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(BriefError, match="not a mapping"):
        load_character("ceo", pack_dir=tmp_path)


def test_load_character_unparseable_profile_degrades_to_empty(tmp_path):
    write_character(tmp_path, "ceo", profile=False)
    (tmp_path / "profiles" / "ceo.yaml").write_text("backstory: [unclosed", encoding="utf-8")
    assert load_character("ceo", pack_dir=tmp_path)["profile"] == {}


def test_build_persona_prompt_uses_env_pack_dir(monkeypatch, pack):
    monkeypatch.setenv(brief_stub.PACK_DIR_ENV, str(pack))
    assert build_persona_prompt("analyst").startswith("You are the Analyst.")
