"""WP-10o integration tests for office_profiles.load_profiles (the DB half).

OB-41 test list (see test_character_office_profiles.py for its origin): the
office loader replaces WP-07..WP-10 and the baseline stage for the office cast
(.claude/prompts/ashiorid_office_build_plan.md OB-41). It writes `characters`,
`character_backstories` (believed and truth as separate rows, never mixed),
`character_baselines` (version 1 on the first load, baseline book/chapter 0/0 by
tracker Q8 (a)) and `character_agents` (`char:<slug>` and the seat worker id
`tuber_N`: office workers publish from tuber_N, app/office/roles.py SEAT).
Baselines are versioned and never modified (plan §6): a changed profile adds
version N+1 for that character only. load_profiles never commits; the tests
commit. `pg_conn` = a fresh database with app/character/sql/*.sql applied.
"""
import json

import pytest

from fakes_generator import (OFFICE_CAMPAIGN, OFFICE_SLUGS, TRUTH_MARKER, copy_office_pack,
                             count_rows, edit_profile, read_yaml, write_yaml)
from pending import require

office_profiles = require("character.generator.office_profiles",
                          "app/character/generator/office_profiles.py", wp="WP-10o")
from character.store import characters  # noqa: E402  (promoted in WP-05)
from office.roles import SEAT  # noqa: E402

pytestmark = pytest.mark.integration

TABLES = ("characters", "character_baselines", "character_backstories", "character_agents")


@pytest.fixture
def office(tmp_path):
    _, profiles, cast = copy_office_pack(tmp_path)
    return profiles, cast


def _load(conn, office, **kwargs):
    profiles, cast = office
    report = office_profiles.load_profiles(conn, profiles, cast, campaign=OFFICE_CAMPAIGN,
                                           **kwargs)
    conn.commit()
    return report


def _counts(conn):
    return {table: count_rows(conn, table) for table in TABLES}


def _updated_at(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT slug, updated_at FROM characters")
        return dict(cur.fetchall())


def _versions(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT c.slug, count(b.version), max(b.version), c.active_baseline_version "
                    "FROM characters c JOIN character_baselines b ON b.character_id = c.id "
                    "GROUP BY c.slug, c.active_baseline_version")
        return {slug: (n, top, active) for slug, n, top, active in cur.fetchall()}


# T10o.1
def test_first_load_row_counts_match_the_eight_files(pg_conn, office):
    report = _load(pg_conn, office)
    assert report.actions == {slug: "created" for slug in OFFICE_SLUGS}
    assert report.versions == {slug: 1 for slug in OFFICE_SLUGS}
    assert _counts(pg_conn) == {"characters": 8, "character_baselines": 8,
                                "character_backstories": 16, "character_agents": 16}
    assert _versions(pg_conn) == {slug: (1, 1, 1) for slug in OFFICE_SLUGS}
    profiles, _ = office
    for slug in OFFICE_SLUGS:
        row = characters.get_character(pg_conn, slug)
        doc = read_yaml(profiles / f"{slug}.yaml")
        assert row["campaign"] == OFFICE_CAMPAIGN and row["status"] == "active"
        assert row["name"] == doc["identity"]["full_name"]
        baseline = characters.active_baseline(pg_conn, slug)
        assert (baseline["baseline_book"], baseline["baseline_chapter"]) == (0, 0)
        assert baseline["backstory_nodes"] == doc["backstory_nodes"]
        assert baseline["profile"]["identity"] == doc["identity"]
        assert baseline["created_by"] == office_profiles.CREATED_BY
        assert baseline["source_id"] == (office_profiles.SOURCE_PREFIX
                                         + office_profiles.profile_sha256(profiles / f"{slug}.yaml"))


# T10o.2
def test_reload_with_the_same_shas_does_nothing(pg_conn, office):
    _load(pg_conn, office)
    counts, stamps = _counts(pg_conn), _updated_at(pg_conn)
    report = _load(pg_conn, office)
    assert report.actions == {slug: "unchanged" for slug in OFFICE_SLUGS}
    assert report.changed == ()
    assert _counts(pg_conn) == counts
    assert _updated_at(pg_conn) == stamps


# T10o.3
def test_changed_profile_sha_replaces_only_that_character(pg_conn, office):
    _load(pg_conn, office)
    stamps = _updated_at(pg_conn)
    profiles, _ = office
    edit_profile(profiles / "engineer.yaml",
                 lambda d: d["objectives"]["wants"].append("Ship the rule engine on time"))
    report = _load(pg_conn, office)
    assert report.actions == {slug: ("updated" if slug == "engineer" else "unchanged")
                              for slug in OFFICE_SLUGS}
    assert report.changed == ("engineer",)
    versions = _versions(pg_conn)
    assert versions["engineer"] == (2, 2, 2)                 # v1 kept, v2 active
    assert all(versions[s] == (1, 1, 1) for s in OFFICE_SLUGS if s != "engineer")
    active = characters.active_baseline(pg_conn, "engineer")
    assert "Ship the rule engine on time" in active["profile"]["objectives"]["wants"]
    assert count_rows(pg_conn, "character_backstories") == 18
    after = _updated_at(pg_conn)
    assert all(after[s] == stamps[s] for s in OFFICE_SLUGS if s != "engineer")
    assert count_rows(pg_conn, "character_agents") == 16     # agents are not duplicated


# T10o.4
def test_truth_never_reaches_believed(pg_conn, office):
    profiles, _ = office
    edit_profile(profiles / "office_manager.yaml",
                 lambda d: d["backstory"].update(truth=d["backstory"]["truth"] + " " + TRUTH_MARKER))
    _load(pg_conn, office)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT b.layer, b.content FROM character_backstories b "
                    "JOIN characters c ON c.id = b.character_id WHERE c.slug = 'office_manager'")
        layers = dict(cur.fetchall())
    doc = read_yaml(profiles / "office_manager.yaml")
    assert set(layers) == {"believed", "truth"}
    assert layers["believed"] == {"text": doc["backstory"]["believed"]}
    assert layers["truth"] == {"text": doc["backstory"]["truth"]}
    assert TRUTH_MARKER in layers["truth"]["text"]
    assert TRUTH_MARKER not in json.dumps(layers["believed"])
    baseline = characters.active_baseline(pg_conn, "office_manager")
    assert TRUTH_MARKER not in json.dumps(baseline["profile"])
    assert TRUTH_MARKER not in json.dumps(baseline["backstory_nodes"])
    assert "backstory" not in baseline["profile"]


# T10o.5 (DB part): one bad file means nothing at all is written
def test_invalid_profile_writes_nothing(pg_conn, office):
    profiles, _ = office
    edit_profile(profiles / "marketing.yaml",
                 lambda d: d["backstory_nodes"][3].update(name="marketing-is-great"))
    with pytest.raises(office_profiles.ProfileError) as err:
        _load(pg_conn, office)
    pg_conn.rollback()
    assert any("marketing.yaml: $.backstory_nodes[3].name:" in e for e in err.value.errors)
    assert all(count == 0 for count in _counts(pg_conn).values())


# T10o.6
def test_retains_fragments_is_true_for_all_eight(pg_conn, office):
    _load(pg_conn, office)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT slug, retains_fragments, is_main, aliases FROM characters")
        rows = {slug: (retains, main, aliases) for slug, retains, main, aliases in cur.fetchall()}
    assert set(rows) == set(OFFICE_SLUGS)
    assert all(retains is True for retains, _, _ in rows.values())
    assert list(rows["ceo"][2]) == ["CEO", "Graham", "Ellery"]


# T10o.7, T14.13 (adapted)
def test_character_agents_seeded_with_char_slug_and_seat_worker_id(pg_conn, office):
    _load(pg_conn, office)
    agents = characters.agents_map(pg_conn)
    seats = {role.value: seat for role, seat in SEAT.items()}
    campaign_seats = read_yaml(office[0].parent / "campaign.yaml")["seats"]
    for slug in OFFICE_SLUGS:
        character_id = characters.get_character(pg_conn, slug)["id"]
        assert agents[f"char:{slug}"] == character_id
        assert agents[seats[slug]] == character_id
        assert campaign_seats[slug] == seats[slug]
    assert len(agents) == 16


# T10o.8
def test_dry_run_writes_nothing(pg_conn, office):
    report = _load(pg_conn, office, dry_run=True)
    assert report.dry_run is True
    assert report.actions == {slug: "created" for slug in OFFICE_SLUGS}
    assert all(count == 0 for count in _counts(pg_conn).values())
    _load(pg_conn, office)
    profiles, _ = office
    edit_profile(profiles / "ceo.yaml", lambda d: d["identity"].update(age=48))
    counts = _counts(pg_conn)
    assert _load(pg_conn, office, dry_run=True).actions["ceo"] == "updated"
    assert _counts(pg_conn) == counts
    assert characters.active_baseline(pg_conn, "ceo")["profile"]["identity"]["age"] == 47


# T14.7 (adapted): a new version moves the pointer only when activate is set
def test_new_version_moves_the_pointer_only_when_activate(pg_conn, office):
    _load(pg_conn, office)
    profiles, _ = office
    edit_profile(profiles / "analyst.yaml", lambda d: d["identity"].update(age=35))
    report = _load(pg_conn, office, activate=False)
    assert report.actions["analyst"] == "updated" and report.versions["analyst"] == 2
    assert _versions(pg_conn)["analyst"] == (2, 2, 1)       # written, pointer still on v1
    assert characters.active_baseline(pg_conn, "analyst")["profile"]["identity"]["age"] != 35
    assert _load(pg_conn, office, activate=False).actions["analyst"] == "unchanged"
    report = _load(pg_conn, office)                          # same sha, activate=True
    assert report.actions["analyst"] == "activated" and report.versions["analyst"] == 2
    assert _versions(pg_conn)["analyst"] == (2, 2, 2)
    assert count_rows(pg_conn, "character_baselines") == 9


# T10o.9: an avatar change in the cast file updates avatar_params only
def test_cast_avatar_change_updates_avatar_params_without_a_new_baseline(pg_conn, office):
    _load(pg_conn, office)
    _, cast = office
    doc = read_yaml(cast / "tester.yaml")
    doc["character_params"]["jaw_width"] = 0.11
    write_yaml(cast / "tester.yaml", doc)
    report = _load(pg_conn, office)
    assert report.actions["tester"] == "avatar"
    assert characters.get_character(pg_conn, "tester")["avatar_params"]["jaw_width"] == 0.11
    assert count_rows(pg_conn, "character_baselines") == 8
    assert _load(pg_conn, office).actions["tester"] == "unchanged"
