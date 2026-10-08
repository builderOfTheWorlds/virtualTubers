"""P2.7 gm_blocks store helpers (migration 002_gm_blocks.sql; build plan U7)."""
import pytest

from pending import require

characters = require("character.store.characters", "app/character/store/characters.py")

pytestmark = pytest.mark.integration


def _baseline(conn, slug):
    cid = characters.upsert_character(conn, slug=slug, name=slug.title(), campaign="ashiorid")
    version = characters.insert_baseline(conn, cid, profile={"slug": slug},
                                         baseline_book=1, baseline_chapter=1)
    characters.set_active_baseline(conn, slug, version)
    conn.commit()
    return cid, version


def test_gm_blocks_round_trip_and_players_stay_null(pg_conn):
    gm_id, gm_v = _baseline(pg_conn, "gm")
    _baseline(pg_conn, "chadwick")
    blocks = {"truth": "Leto fathered all four.", "custom_block": {"x": 1}}
    characters.set_gm_blocks(pg_conn, gm_id, gm_v, blocks)
    pg_conn.commit()
    assert characters.gm_blocks(pg_conn, "gm") == blocks
    assert characters.gm_blocks(pg_conn, "chadwick") is None
    assert characters.gm_blocks(pg_conn, "nobody") is None


def test_set_gm_blocks_unknown_baseline_raises(pg_conn):
    gm_id, gm_v = _baseline(pg_conn, "gm")
    with pytest.raises(LookupError):
        characters.set_gm_blocks(pg_conn, gm_id, gm_v + 5, {"truth": "x"})


def test_set_gm_blocks_rejects_non_mapping(pg_conn):
    gm_id, gm_v = _baseline(pg_conn, "gm")
    with pytest.raises(TypeError):
        characters.set_gm_blocks(pg_conn, gm_id, gm_v, ["truth"])
