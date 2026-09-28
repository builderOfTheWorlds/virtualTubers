"""WP-14 tests for app/character/generator/export.py: DB -> cast YAML export.

Frozen test list (playbook §4 WP-14, items 9-10), adapted for OB-41. Plan §1
stage 7 and §9: export writes `characters.avatar_params` inline into
`campaigns/<pack>/cast/<slug>.yaml` as `character_params`; the pack loader
ignores unknown keys (app/campaign/pack.py:29-46), so `app/campaign/pack.py`
must still load the pack, and every other key in the file is kept. It also
prints a roster snippet for config/workers/roundtable.yaml.

Test corrections (OB-41, .claude/prompts/ashiorid_office_build_plan.md OB-41:
the pilot cast is the office cast, loaded by the office profile loader):
- T14.9 checks BOTH packs: campaigns/ashiorid_office (the office cast, loaded
  with office_profiles.load_profiles, whose cast files already carry the OB-20
  character_params) and campaigns/hptest (as written).
- The office round trip (load, then export) must be a no-op: the cast files
  are the source of the OB-20 params, so export must never rewrite them with
  equal values.
Every test works on a tmp copy of the packs; the repo's campaigns/ is never
written. `pg_conn` = a fresh database with app/character/sql/*.sql applied.
"""
import pytest

from fakes_generator import (HPTEST_PACK, OFFICE_CAMPAIGN, OFFICE_SLUGS, copy_office_pack,
                             copy_pack, read_yaml, snapshot_files)
from pending import require

export = require("character.generator.export", "app/character/generator/export.py", wp="WP-14")
from campaign.pack import load_pack  # noqa: E402
from character.generator import office_profiles  # noqa: E402  (promoted in WP-10o)
from character.store import characters  # noqa: E402

pytestmark = pytest.mark.integration

NEW_CEO = {"head_width": 0.25, "head_taper": 0.75, "eye_size": 0.5, "eye_spacing": 0.5,
           "jaw_width": 0.33, "nose_length": 0.4, "ear_size": 0.5, "build": 0.9,
           "accent_color": "RED"}
HARRY = {"head_width": 0.45, "head_taper": 0.3, "eye_size": 0.7, "eye_spacing": 0.5,
         "jaw_width": 0.4, "nose_length": 0.4, "ear_size": 0.5, "build": 0.35,
         "accent_color": "GREEN"}


@pytest.fixture
def office(tmp_path, pg_conn):
    """A tmp office pack loaded into the database: (pack, cast_dir)."""
    pack, profiles, cast = copy_office_pack(tmp_path)
    office_profiles.load_profiles(pg_conn, profiles, cast, campaign=OFFICE_CAMPAIGN)
    pg_conn.commit()
    return pack, cast


def _set_avatar(conn, slug, params):
    from psycopg2.extras import Json

    with conn.cursor() as cur:
        cur.execute("UPDATE characters SET avatar_params = %s WHERE slug = %s", (Json(params), slug))
    conn.commit()


def _without_block(text):
    """The file's lines with the character_params block removed."""
    out, skipping = [], False
    for line in text.splitlines():
        if line.startswith("character_params:"):
            skipping = True
            continue
        if skipping and (line.startswith((" ", "\t")) or not line.strip()):
            continue
        skipping = False
        out.append(line)
    return out


# T14.9 (office pack): a fresh load then export is a no-op round trip
def test_office_round_trip_export_writes_nothing(pg_conn, office):
    pack, cast = office
    before = snapshot_files(cast)
    assert export.export_cast(pg_conn, cast, campaign=OFFICE_CAMPAIGN) == []
    assert snapshot_files(cast) == before
    load_pack(pack)


# T14.9 (office pack): changed params are written inline and the pack still loads
def test_export_writes_character_params_inline_and_office_pack_loads(pg_conn, office):
    pack, cast = office
    _set_avatar(pg_conn, "ceo", NEW_CEO)
    written = export.export_cast(pg_conn, cast, campaign=OFFICE_CAMPAIGN)
    assert [p.name for p in written] == ["ceo.yaml"]
    doc = read_yaml(cast / "ceo.yaml")
    assert doc["character_params"] == NEW_CEO
    text = (cast / "ceo.yaml").read_text(encoding="utf-8")
    assert "character_params:" in text and "  jaw_width: 0.33" in text
    loaded = load_pack(pack)
    assert loaded.cast["ceo"].name == "Graham Ellery"
    assert set(loaded.cast) == set(OFFICE_SLUGS)


# T14.9 (hptest pack, as written): pack.py still loads campaigns/hptest
def test_export_into_hptest_and_pack_still_loads(pg_conn, tmp_path):
    pack = copy_pack(HPTEST_PACK, tmp_path)
    gm_before = (pack / "cast" / "gm.yaml").read_bytes()
    characters.upsert_character(pg_conn, slug="harry", name="Harry Potter", campaign="hptest",
                                avatar_params=HARRY)
    characters.upsert_character(pg_conn, slug="ceo", name="Graham Ellery",
                                campaign=OFFICE_CAMPAIGN, avatar_params=NEW_CEO)
    pg_conn.commit()
    written = export.export_cast(pg_conn, pack / "cast", campaign="hptest")
    assert [p.name for p in written] == ["harry.yaml"]
    assert read_yaml(pack / "cast" / "harry.yaml")["character_params"] == HARRY
    assert (pack / "cast" / "gm.yaml").read_bytes() == gm_before    # no DB character: untouched
    assert not (pack / "cast" / "ceo.yaml").exists()                # other campaign: not created
    loaded = load_pack(pack)
    assert loaded.cast["harry"].name == "Harry Potter"


# T14.10: every other key and line of the cast file is kept
def test_export_keeps_every_other_key_and_line(pg_conn, office):
    _, cast = office
    path = cast / "ceo.yaml"
    before_text = path.read_text(encoding="utf-8")
    before = read_yaml(path)
    _set_avatar(pg_conn, "ceo", NEW_CEO)
    export.export_cast(pg_conn, cast, campaign=OFFICE_CAMPAIGN)
    after_text = path.read_text(encoding="utf-8")
    after = read_yaml(path)
    before.pop("character_params")
    after.pop("character_params")
    assert after == before                        # seat, office_role, wants, relationships, ...
    assert _without_block(after_text) == _without_block(before_text)
    assert after_text.count("character_params:") == 1
    # the block stays where it was (after `avatar:`), not appended at the end
    lines = after_text.splitlines()
    assert lines.index(next(l for l in lines if l.startswith("character_params:"))) == \
        lines.index("avatar: null") + 1


# T14.10: a file with no block gets one after `avatar:` (or at the end)
def test_write_character_params_inserts_after_avatar_or_appends(tmp_path):
    with_avatar = tmp_path / "a.yaml"
    with_avatar.write_text('name: "A"\n# keep me\navatar: null\nvoice: x\n', encoding="utf-8")
    assert export.write_character_params(with_avatar, HARRY) is True
    lines = with_avatar.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ['name: "A"', "# keep me", "avatar: null"]
    assert lines[3].startswith("character_params:") and lines[-1] == "voice: x"
    assert export.write_character_params(with_avatar, HARRY) is False   # equal: no write
    bare = tmp_path / "b.yaml"
    bare.write_text("name: B\nvoice: y", encoding="utf-8")
    assert export.write_character_params(bare, HARRY) is True
    assert read_yaml(bare) == {"name": "B", "voice": "y", "character_params": HARRY}


# T14.11-style dry run for export: nothing is written
def test_export_dry_run_reports_but_writes_nothing(pg_conn, office):
    _, cast = office
    before = snapshot_files(cast)
    _set_avatar(pg_conn, "tester", NEW_CEO)
    written = export.export_cast(pg_conn, cast, campaign=OFFICE_CAMPAIGN, dry_run=True)
    assert [p.name for p in written] == ["tester.yaml"]
    assert snapshot_files(cast) == before


# plan §9: the roster snippet for config/workers/roundtable.yaml
def test_roster_snippet_lists_each_seat_with_inline_params(pg_conn, office):
    _, cast = office
    snippet = export.roster_snippet(pg_conn, cast, campaign=OFFICE_CAMPAIGN)
    lines = snippet.splitlines()
    assert lines[0] == "roster:"
    assert len(lines) == 9
    ceo = next(line for line in lines if "# ceo" in line)
    assert ceo.startswith('  tuber_0: {name: "Graham Ellery", character_params: {')
    assert "accent_color: BLUE" in ceo and "head_width: 0.80" in ceo
