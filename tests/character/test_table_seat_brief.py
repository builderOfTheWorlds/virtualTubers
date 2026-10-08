"""P2.6 (reduced) seat brief for the live agent table: app/table/seat_brief.py.

A separate module from WP-22 app/character/brief.py (the full weekly-loop brief,
still pending, Phase 8), so the frozen WP-22 tests stay intact. Lives in
tests/character/ to reuse the pg_conn fixture.

Invariants: never contains truth (backstory truth layer, gm_blocks, other
players' truths), stays <= max_chars, sections in order, unlocks included,
knowledge trimmed before identity/behaviour.
"""
import pathlib

import pytest

from pending import require

seat_brief = require("table.seat_brief", "app/table/seat_brief.py", wp="P2.6")
from character.generator import pack_profiles  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
PACK = REPO / "campaigns" / "ashiorid"
LORE = {p.stem: p.read_text(encoding="utf-8") for p in (PACK / "lore").glob("*.md")}

pytestmark = pytest.mark.integration

#: truth-only markers from the drafts' validator (campaigns/ashiorid/profiles/validate_profiles.py)
TRUTH_MARKERS = ("Begene", "Grovley", "four cribs", "half-siblings", "Leto fathered")


@pytest.fixture
def loaded(pg_conn):
    pack_profiles.load_pack(pg_conn, PACK / "profiles", PACK / "cast", campaign="ashiorid")
    pg_conn.commit()
    return pg_conn


@pytest.mark.parametrize("slug", ["chadwick", "Leena", "Vigil", "sodacan_bob"])
def test_player_brief_has_sections_in_order_and_no_truth(loaded, slug):
    text = seat_brief.build_table_brief(loaded, slug, lore=LORE)
    heads = [h for h in seat_brief.SECTION_HEADINGS if h in text]
    assert heads == list(seat_brief.SECTION_HEADINGS)
    positions = [text.index(h) for h in heads]
    assert positions == sorted(positions)
    for marker in TRUTH_MARKERS:
        assert marker.lower() not in text.lower(), (slug, marker)
    assert len(text) <= 6000


def test_brief_has_believed_wants_and_known_lore_only(loaded):
    text = seat_brief.build_table_brief(loaded, "chadwick", lore=LORE)
    assert "oath" in text.lower()                      # believed / wants
    assert "the-event" in text                         # his start knowledge stem
    assert "## the-bahadur" not in text                # not known at start


def test_unlocks_add_lore_and_stay_under_cap(loaded):
    base = seat_brief.build_table_brief(loaded, "Vigil", lore=LORE)
    more = seat_brief.build_table_brief(loaded, "Vigil", lore=LORE, unlocked=["moonwells"])
    assert "## moonwells" in more and "## moonwells" not in base
    assert len(more) <= 6000


def test_cap_trims_knowledge_first_keeps_behaviour(loaded):
    text = seat_brief.build_table_brief(loaded, "Leena", lore=LORE,
                                        unlocked=sorted(LORE), max_chars=2500)
    assert len(text) <= 2500
    assert seat_brief.SECTION_HEADINGS[-1] in text     # behaviour contract survives
    assert "Never speak for another character" in text or "never for another character" in text


def test_behaviour_contract_has_spoken_words_only_rule(loaded):
    text = seat_brief.build_table_brief(loaded, "sodacan_bob", lore=LORE)
    assert "spoken words only" in text.lower()


def test_unknown_slug_raises_lookup_error(loaded):
    with pytest.raises(LookupError):
        seat_brief.build_table_brief(loaded, "nobody", lore=LORE)


def test_gm_brief_is_refused(loaded):
    # The GM's context is app/table/gm_context.py; a seat brief for the GM is a wiring bug.
    with pytest.raises(ValueError):
        seat_brief.build_table_brief(loaded, "gm", lore=LORE)


def test_sql_never_reads_truth_or_gm_blocks():
    sqls = {k: v for k, v in vars(seat_brief).items() if k.endswith("_SQL")}
    assert sqls, "seat_brief must keep its queries in *_SQL constants"
    for name, sql in sqls.items():
        low = sql.lower()
        assert "truth" not in low and "gm_blocks" not in low, name
        assert "*" not in low, f"{name}: list columns explicitly"
