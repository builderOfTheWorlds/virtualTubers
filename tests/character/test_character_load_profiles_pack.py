"""P2.4 pack-agnostic profile loader: the D&D pack (campaigns/ashiorid/profiles).

Uses the real draft profiles (P2.5) so a schema drift between the drafts and the
loader fails here. Players: gm_blocks NULL, truth only in the truth backstory
layer. GM: every top-level key outside the player schema lands in gm_blocks
(+ truth), and is stripped from the stored profile.
"""
import pathlib
import subprocess
import sys

import pytest

from pending import require

pack_profiles = require("character.generator.pack_profiles",
                        "app/character/generator/pack_profiles.py", wp="P2.4")
from character.store import characters  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
DND = REPO / "campaigns" / "ashiorid" / "profiles"
DND_CAST = REPO / "campaigns" / "ashiorid" / "cast"
SLUGS = {"gm", "chadwick", "Leena", "Vigil", "sodacan_bob"}


def test_read_dnd_profiles_validate():
    records = pack_profiles.read_pack_profiles(DND, DND_CAST)
    assert {r.slug for r in records} == SLUGS
    gm = next(r for r in records if r.slug == "gm")
    assert gm.seat == "tuber_0"
    for key in ("secrets", "unlocks", "table_rules", "style"):
        assert key not in gm.profile, key           # GM-only blocks never in profile JSON


def test_gm_block_keys_are_data_not_code():
    doc = {"id": "gm", "table_role": "gm", "wants": [], "identity": {}, "secrets": [1],
           "custom_block": {"x": 1}, "backstory": {"believed": "b", "truth": "t"}}
    blocks = pack_profiles.gm_blocks_of(doc)
    assert blocks == {"truth": "t", "secrets": [1], "custom_block": {"x": 1}}
    assert pack_profiles.gm_blocks_of(dict(doc, table_role="player")) is None


@pytest.mark.integration
def test_load_dnd_pack_into_db_idempotent(pg_conn):
    report = pack_profiles.load_pack(pg_conn, DND, DND_CAST, campaign="ashiorid")
    pg_conn.commit()
    assert set(report.actions) == SLUGS
    assert set(report.actions.values()) == {"created"}

    blocks = characters.gm_blocks(pg_conn, "gm")
    assert blocks and {"truth", "secrets", "unlocks", "table_rules", "style"} <= set(blocks)
    for slug in SLUGS - {"gm"}:
        assert characters.gm_blocks(pg_conn, slug) is None
        row = characters.active_baseline(pg_conn, slug)
        assert "truth" not in row["profile"].get("backstory", {})

    again = pack_profiles.load_pack(pg_conn, DND, DND_CAST, campaign="ashiorid")
    pg_conn.commit()
    assert set(again.actions.values()) == {"unchanged"}
    assert again.versions == report.versions
    assert characters.gm_blocks(pg_conn, "gm") == blocks


def test_cli_help_and_dry_run_without_db_flags():
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "load_profiles.py"), "--help"],
                         capture_output=True, text=True, cwd="/")
    assert out.returncode == 0 and "--pack" in out.stdout


def test_cli_invalid_pack_exits_1_before_connecting(tmp_path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "bad.yaml").write_text("id: other\n")
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "load_profiles.py"),
                          "--pack-dir", str(tmp_path)],
                         capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "CHARACTER_DB_HOST": "203.0.113.1"})
    assert out.returncode == 1
    assert "bad.yaml" in (out.stdout + out.stderr)
