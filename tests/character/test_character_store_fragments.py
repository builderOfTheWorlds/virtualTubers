"""WP-05 integration tests for app/character/store/fragments.py.

Frozen test list (playbook §4 WP-05, items 13-17): memory fragments, their
lead-up beats and links, unlocks and recalls. The fragment tables are
insert-only, enforced by triggers in 001_init.sql; test tools opt out per
transaction with `SET LOCAL character.allow_test_mutation = 'on'` (plan §6,
§5.2). A fragment is dormant until it has a fragment_unlocks row (D-08).
"""
import psycopg2
import pytest

from fakes import seed_character
from pending import require

fragments = require("character.store.fragments", "app/character/store/fragments.py")

pytestmark = pytest.mark.integration

LEAD_UP = [
    {"text": "The standup ran long again.", "event_id": "e1", "embedding": [1.0, 0.0, 0.0]},
    {"text": "Owen said the CI was red.", "event_id": "e2", "embedding": [0.0, 1.0, 0.0]},
    {"text": "Anselm went quiet.", "event_id": "e3"},
]


@pytest.fixture
def ceo(pg_conn):
    character_id = seed_character(pg_conn, "ceo")
    pg_conn.commit()
    return character_id


def _make(conn, character_id, gist, **kwargs):
    return fragments.create_fragment(conn, character_id=character_id, source_week=1,
                                     gist=gist, **kwargs)


def _count(pg, sql, params=()):
    other = psycopg2.connect(pg)
    try:
        with other.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]
    finally:
        other.close()


# T05.13
def test_create_fragment_with_lead_up_and_links_is_one_transaction(pg, pg_conn, ceo):
    earlier = _make(pg_conn, ceo, "The office smelled of burnt coffee and dread.")
    fragment_id = _make(pg_conn, ceo, "My chest tightened when the room went quiet.",
                        hooks={"entities": ["owen", "anselm"], "places": ["glass box"]},
                        lead_up=LEAD_UP, links=[(earlier, "evokes")],
                        gist_embedding=[0.5, 0.5, 0.0], embed_model="toy-3", embed_dim=3)
    # committed: visible from another session straight away
    assert _count(pg, "SELECT count(*) FROM memory_fragments WHERE id = %s", (fragment_id,)) == 1
    assert _count(pg, "SELECT count(*) FROM fragment_links WHERE from_fragment_id = %s "
                      "AND to_fragment_id = %s AND relation = 'evokes'", (fragment_id, earlier)) == 1
    beats = fragments.lead_up_for(pg_conn, fragment_id)
    assert [beat["position"] for beat in beats] == [0, 1, 2]
    assert [beat["text"] for beat in beats] == [beat["text"] for beat in LEAD_UP]
    assert [beat["event_id"] for beat in beats] == ["e1", "e2", "e3"]
    assert _count(pg, "SELECT count(*) FROM fragment_lead_up WHERE fragment_id = %s "
                      "AND embedding IS NOT NULL", (fragment_id,)) == 2

    with pytest.raises(psycopg2.Error):
        _make(pg_conn, ceo, "This one links to nothing real.", lead_up=LEAD_UP,
              links=[("no-such-fragment", "evokes")])
    assert _count(pg, "SELECT count(*) FROM memory_fragments WHERE gist = %s",
                  ("This one links to nothing real.",)) == 0
    assert _count(pg, "SELECT count(*) FROM fragment_lead_up") == 3
    with pg_conn.cursor() as cur:  # the connection is usable after the failure
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)


# T05.14
def test_update_or_delete_without_test_flag_raises(pg_conn, ceo):
    fragment_id = _make(pg_conn, ceo, "I was sure I had said this before.", lead_up=LEAD_UP)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with pg_conn.cursor() as cur:
            cur.execute("UPDATE memory_fragments SET gist = 'rewritten' WHERE id = %s",
                        (fragment_id,))
    pg_conn.rollback()
    with pytest.raises(psycopg2.Error, match="immutable"):
        fragments.test_delete(pg_conn, fragment_id)
    pg_conn.rollback()
    assert [f["id"] for f in fragments.dormant_for(pg_conn, ceo)] == [fragment_id]


# T05.15
def test_dormant_for_excludes_unlocked(pg_conn, ceo):
    tester = seed_character(pg_conn, "tester")
    pg_conn.commit()
    dormant = _make(pg_conn, ceo, "The whiteboard said something I had erased.")
    unlocked = _make(pg_conn, ceo, "The coffee machine clicked like a warning.")
    _make(pg_conn, tester, "Someone else's fragment.")
    fragments.unlock(pg_conn, unlocked, 2, recall_id="r-1")
    pg_conn.commit()
    got = fragments.dormant_for(pg_conn, ceo)
    assert [fragment["id"] for fragment in got] == [dormant]
    assert got[0]["gist"] == "The whiteboard said something I had erased."
    assert [fragment["id"] for fragment in fragments.unlocked_for(pg_conn, ceo)] == [unlocked]


# T05.16
def test_unlock_is_idempotent(pg_conn, ceo):
    fragment_id = _make(pg_conn, ceo, "I knew the demo would fail before it did.")
    assert fragments.unlock(pg_conn, fragment_id, 2, recall_id="r-first") is True
    pg_conn.commit()
    assert fragments.unlock(pg_conn, fragment_id, 3, recall_id="r-second") is False
    pg_conn.commit()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT character_id, unlocked_week, recall_id FROM fragment_unlocks "
                    "WHERE fragment_id = %s", (fragment_id,))
        assert cur.fetchall() == [(ceo, 2, "r-first")]


# T05.17
def test_test_delete_works_inside_allow_test_mutation(pg, pg_conn, ceo):
    doomed = _make(pg_conn, ceo, "A fragment the recall harness made by hand.", lead_up=LEAD_UP)
    survivor = _make(pg_conn, ceo, "A fragment that must stay.")
    fragments.unlock(pg_conn, doomed, 2)
    pg_conn.commit()
    with fragments.allow_test_mutation(pg_conn):
        assert fragments.test_delete(pg_conn, doomed) is True
        assert fragments.test_delete(pg_conn, "no-such-fragment") is False
    assert _count(pg, "SELECT count(*) FROM memory_fragments WHERE id = %s", (doomed,)) == 0
    assert _count(pg, "SELECT count(*) FROM fragment_lead_up WHERE fragment_id = %s", (doomed,)) == 0
    assert _count(pg, "SELECT count(*) FROM fragment_unlocks WHERE fragment_id = %s", (doomed,)) == 0
    # the opt-out ended with its transaction: the trigger guards again
    with pytest.raises(psycopg2.Error, match="immutable"):
        fragments.test_delete(pg_conn, survivor)
    pg_conn.rollback()
