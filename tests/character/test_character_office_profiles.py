"""WP-10o unit tests for app/character/generator/office_profiles.py (no database).

OB-41 test list (written by the Phase 2 test author, not the planner): the
office build plan OB-41 replaces the book source stages WP-07..WP-10 with
`scripts/load_office_profiles.py`, which "loads profiles/*.yaml into characters /
character_backstories / character_baselines" (.claude/prompts/ashiorid_office_build_plan.md,
OB-41). The items are modelled on WP-10 (row counts, same-sha re-load does
nothing, a changed sha replaces only its own rows) and WP-14 (baseline shape
and node names, avatar, char:<slug> agents). This file holds the pure half:
reading, validating and splitting a profile. The DB half is in
test_character_office_profiles_db.py, the CLI in test_character_load_office_profiles.py.

Profile contract: campaigns/ashiorid_office/profiles/_SCHEMA.md. Node names:
character.node_names.check (plan §10, WP-11).
"""
import pytest

from fakes_generator import (OFFICE_CAST, OFFICE_PROFILES, OFFICE_SLUGS, SLIDER_KEYS,
                             TRUTH_MARKER, copy_office_pack, edit_profile, read_yaml)
from pending import require

office_profiles = require("character.generator.office_profiles",
                          "app/character/generator/office_profiles.py", wp="WP-10o")
from character import config  # noqa: E402  (promoted in WP-03)
from character_schema import PARAM_DEFAULTS, SLIDER_DEFAULTS  # noqa: E402
from office.roles import SEAT  # noqa: E402


@pytest.fixture
def office(tmp_path):
    """(profiles_dir, cast_dir) of a tmp copy of campaigns/ashiorid_office."""
    _, profiles, cast = copy_office_pack(tmp_path)
    return profiles, cast


# T10o.1 (unit part): exactly the 8 profile files are read; _* files are skipped
def test_read_profiles_reads_the_eight_files_and_skips_underscore_files():
    names = [p.name for p in office_profiles.profile_files(OFFICE_PROFILES)]
    assert names == sorted(f"{slug}.yaml" for slug in OFFICE_SLUGS)
    records = office_profiles.read_profiles(OFFICE_PROFILES, OFFICE_CAST)
    assert [r.slug for r in records] == sorted(OFFICE_SLUGS)


# T10o.5 + T10o.6 (unit part), T14.6 (adapted): the 8 repo profiles are valid
def test_all_eight_repo_profiles_validate_clean():
    for path in office_profiles.profile_files(OFFICE_PROFILES):
        doc = read_yaml(path)
        assert office_profiles.validate_profile(doc, expected_id=path.stem) == [], path.name
    for record in office_profiles.read_profiles(OFFICE_PROFILES, OFFICE_CAST):
        assert record.retains_fragments is True          # office plan U6: all 8
        assert record.is_main is True                    # as authored (tracker Q5)
        assert record.name == read_yaml(OFFICE_CAST / f"{record.slug}.yaml")["name"]


# T10o.5, T14.6 (adapted): a bad node name is rejected with its path and reason
def test_bad_node_name_is_rejected_with_its_path_and_reason():
    doc = read_yaml(OFFICE_PROFILES / "tester.yaml")
    doc["backstory_nodes"][2]["name"] = "Green-Build-Wanted"
    doc["backstory_nodes"][5]["name"] = "remembers-the-last-loop"
    errors = office_profiles.validate_profile(doc, expected_id="tester")
    assert any(e.startswith("$.backstory_nodes[2].name:") for e in errors), errors
    assert any(e.startswith("$.backstory_nodes[5].name:") and "banned" in e for e in errors)
    assert len(errors) == 2


# T14.6 (adapted): the profile is validated against its shape, id and seat
@pytest.mark.parametrize("mutate,path", [
    (lambda d: d.pop("backstory"), "$.backstory"),
    (lambda d: d["backstory"].pop("truth"), "$.backstory.truth"),
    (lambda d: d["backstory"].update(believed="   "), "$.backstory.believed"),
    (lambda d: d.update(retains_fragments="yes"), "$.retains_fragments"),
    (lambda d: d["identity"].update(age="forty"), "$.identity.age"),
    (lambda d: d["identity"].update(seat="seat_4"), "$.identity.seat"),
    (lambda d: d.update(id="qa"), "$.id"),
    (lambda d: d["backstory_nodes"].append(dict(d["backstory_nodes"][0])),
     "$.backstory_nodes[17].name"),                              # duplicate name
    (lambda d: d["backstory_nodes"][0].pop("statement"), "$.backstory_nodes[0].statement"),
])
def test_validate_profile_reports_shape_id_and_seat_errors(mutate, path):
    doc = read_yaml(OFFICE_PROFILES / "tester.yaml")
    assert len(doc["backstory_nodes"]) == 17
    mutate(doc)
    errors = office_profiles.validate_profile(doc, expected_id="tester")
    assert errors and any(e.startswith(path + ":") for e in errors), errors


# T10o.5: errors from every file are collected, then ProfileError is raised
def test_read_profiles_collects_errors_from_every_file(office):
    profiles, cast = office
    edit_profile(profiles / "ceo.yaml",
                 lambda d: d["backstory_nodes"][0].update(name="ceo-is-third"))
    edit_profile(profiles / "tester.yaml", lambda d: d.update(id="qa"))
    (profiles / "broken.yaml").write_text("id: [unclosed\n", encoding="utf-8")
    with pytest.raises(office_profiles.ProfileError) as err:
        office_profiles.read_profiles(profiles, cast)
    errors = err.value.errors
    assert any(e.startswith("ceo.yaml: $.backstory_nodes[0].name:") for e in errors), errors
    assert any(e.startswith("tester.yaml: $.id:") for e in errors), errors
    assert any(e.startswith("broken.yaml:") for e in errors), errors
    assert "ceo.yaml" in str(err.value)


# T10o.1: an empty profiles directory is an error, not an empty load
def test_read_profiles_of_an_empty_directory_raises(tmp_path):
    with pytest.raises(office_profiles.ProfileError):
        office_profiles.read_profiles(tmp_path)


# T10o.4 (unit part): believed and truth are split; the profile carries neither
def test_record_splits_believed_and_truth_and_profile_has_no_backstory(office):
    profiles, cast = office
    edit_profile(profiles / "analyst.yaml",
                 lambda d: d["backstory"].update(truth=d["backstory"]["truth"] + TRUTH_MARKER))
    record = office_profiles.read_profile(profiles / "analyst.yaml", cast)
    doc = read_yaml(profiles / "analyst.yaml")
    assert record.believed == doc["backstory"]["believed"]
    assert record.truth == doc["backstory"]["truth"] and TRUTH_MARKER in record.truth
    assert TRUTH_MARKER not in record.believed
    assert "backstory" not in record.profile and "backstory_nodes" not in record.profile
    assert TRUTH_MARKER not in repr(record.profile)
    assert record.profile["identity"] == doc["identity"]
    assert record.profile["behaviour_contract"] == doc["behaviour_contract"]
    assert list(record.backstory_nodes) == doc["backstory_nodes"]


# T10o.3 (unit part): the sha256 follows the file content, not its line endings
def test_profile_sha256_follows_content_and_ignores_crlf(office):
    profiles, _ = office
    path = profiles / "engineer.yaml"
    before = office_profiles.profile_sha256(path)
    assert len(before) == 64
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert office_profiles.profile_sha256(path) == before
    edit_profile(path, lambda d: d["identity"].update(age=32))
    assert office_profiles.profile_sha256(path) != before
    record = office_profiles.read_profile(path)
    assert record.sha256 == office_profiles.profile_sha256(path)
    assert record.source_id == office_profiles.SOURCE_PREFIX + record.sha256


# T14.8 (adapted, OB-20 params): avatar params come from cast character_params
def test_avatar_params_come_from_the_cast_character_params():
    cfg = config.load(env={})
    for slug in OFFICE_SLUGS:
        cast_doc = read_yaml(OFFICE_CAST / f"{slug}.yaml")
        params = office_profiles.avatar_params(cast_doc, accent_color="YELLOW")
        assert set(params) == set(SLIDER_DEFAULTS) | {"accent_color"}
        assert params == {k: cast_doc["character_params"][k] for k in params}
        assert params["accent_color"] == cfg.characters[slug].accent_color  # config mirrors cast
    assert set(SLIDER_KEYS) == set(SLIDER_DEFAULTS)


# T14.8 (plan §9 v1 default): no character_params -> resolve_params(None) + config accent
def test_avatar_params_default_to_resolve_params_none_plus_configured_accent():
    params = office_profiles.avatar_params({"name": "Nobody"}, accent_color="CYAN")
    assert params == {**PARAM_DEFAULTS, "accent_color": "CYAN"}
    assert office_profiles.avatar_params(None) == PARAM_DEFAULTS
    assert all(k in SLIDER_DEFAULTS or k == "accent_color" for k in params)


# T10o.7 (unit part): agent ids are char:<slug> and the office seat worker id
def test_agent_ids_are_char_slug_and_the_seat_worker_id():
    for record in office_profiles.read_profiles(OFFICE_PROFILES, OFFICE_CAST):
        seat = {role.value: seat for role, seat in SEAT.items()}[record.slug]
        assert record.seat == seat
        assert record.agent_ids == (f"char:{record.slug}", seat)


# T10o.1 (unit part): name and aliases (title first, then the name's words)
def test_name_and_aliases_come_from_identity():
    records = {r.slug: r for r in office_profiles.read_profiles(OFFICE_PROFILES, OFFICE_CAST)}
    assert records["ceo"].name == "Graham Ellery"
    assert records["ceo"].aliases == ("CEO", "Graham", "Ellery")
    assert records["party_member"].aliases == ("Party Member", "Penhale")  # "A." is dropped
