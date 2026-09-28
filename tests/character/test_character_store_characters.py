"""WP-05 integration tests for app/character/store/characters.py.

Frozen test list (playbook §4 WP-05, items 1-3): characters, agents,
baselines, backstories and the active baseline version pointer. The
`pg_conn` fixture (tests/character/conftest.py) is a fresh database with
app/character/sql/*.sql applied. Store functions never commit; the tests
commit where they need to.
"""
import psycopg2
import pytest

from pending import require

characters = require("character.store.characters", "app/character/store/characters.py")

pytestmark = pytest.mark.integration

CEO = dict(slug="ceo", name="Graham Ellery", campaign="ashiorid_office",
           retains_fragments=True, is_main=True, aliases=("Graham", "Mr Ellery"),
           avatar_params={"build": 0.7, "accent_color": "BLUE"})


def _count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")  # table names are test constants
        return cur.fetchone()[0]


# T05.1
def test_upsert_character_is_idempotent(pg_conn):
    first = characters.upsert_character(pg_conn, **CEO)
    pg_conn.commit()
    version = characters.insert_baseline(pg_conn, first, profile={"identity": {"age": 47}},
                                         baseline_book=0, baseline_chapter=0)
    characters.set_active_baseline(pg_conn, "ceo", version)
    pg_conn.commit()

    second = characters.upsert_character(pg_conn, **CEO)
    pg_conn.commit()
    assert second == first
    assert _count(pg_conn, "characters") == 1
    row = characters.get_character(pg_conn, "ceo")
    assert row["id"] == first
    assert row["name"] == "Graham Ellery"
    assert row["campaign"] == "ashiorid_office"
    assert row["retains_fragments"] is True and row["is_main"] is True
    assert list(row["aliases"]) == ["Graham", "Mr Ellery"]
    assert row["avatar_params"] == {"build": 0.7, "accent_color": "BLUE"}
    # the pointer is not one of upsert's columns: re-running must not reset it
    assert row["active_baseline_version"] == version
    assert characters.get_character(pg_conn, "nobody") is None


# T05.2
def test_set_active_baseline_fails_for_missing_version(pg_conn):
    character_id = characters.upsert_character(pg_conn, **CEO)
    v1 = characters.insert_baseline(pg_conn, character_id, profile={"v": 1},
                                    baseline_book=0, baseline_chapter=0)
    v2 = characters.insert_baseline(pg_conn, character_id, profile={"v": 2},
                                    baseline_book=0, baseline_chapter=0,
                                    change_notes="second pass")
    assert (v1, v2) == (1, 2)
    characters.insert_backstory(pg_conn, character_id, v1, "believed", {"text": "I ran a depot."})
    characters.set_active_baseline(pg_conn, "ceo", v1)
    pg_conn.commit()

    with pytest.raises(LookupError):
        characters.set_active_baseline(pg_conn, "ceo", 7)
    pg_conn.rollback()
    with pytest.raises(LookupError):
        characters.set_active_baseline(pg_conn, "nobody", 1)
    pg_conn.rollback()

    assert characters.get_character(pg_conn, "ceo")["active_baseline_version"] == v1
    active = characters.active_baseline(pg_conn, "ceo")
    assert active["version"] == v1 and active["profile"] == {"v": 1}
    characters.set_active_baseline(pg_conn, "ceo", v2)
    assert characters.active_baseline(pg_conn, "ceo")["profile"] == {"v": 2}
    with pytest.raises(psycopg2.Error):
        characters.insert_backstory(pg_conn, character_id, v1, "believed", {"text": "dup"})
    pg_conn.rollback()


# T05.3
def test_agents_map_returns_agent_to_character_id(pg_conn):
    assert characters.agents_map(pg_conn) == {}
    ceo = characters.upsert_character(pg_conn, **CEO)
    tech_lead = characters.upsert_character(pg_conn, slug="tech_lead", name="Anselm Brody",
                                            campaign="ashiorid_office")
    characters.add_agent(pg_conn, "char:ceo", ceo)
    characters.add_agent(pg_conn, "char:tech_lead", tech_lead)
    characters.add_agent(pg_conn, "char:ceo", ceo)  # re-adding is harmless
    pg_conn.commit()
    assert characters.agents_map(pg_conn) == {"char:ceo": ceo, "char:tech_lead": tech_lead}
