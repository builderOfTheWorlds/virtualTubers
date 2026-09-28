"""WP-05 integration tests for app/character/store/knowledge.py.

Frozen test list (playbook §4 WP-05, items 10-12): daily summaries, week
knowledge nodes and edges, and the weekly archive (plan §2: archived
knowledge is kept, never deleted, and unreachable by the character).
"""
from datetime import date

import pytest

from fakes import seed_character
from pending import require

knowledge = require("character.store.knowledge", "app/character/store/knowledge.py")

pytestmark = pytest.mark.integration


def _archive_state(conn):
    """{(table, name-or-relation): archived_at_week} for every node and edge."""
    with conn.cursor() as cur:
        cur.execute("SELECT name, archived_at_week FROM week_knowledge_nodes")
        state = {("node", name): week for name, week in cur.fetchall()}
        cur.execute("SELECT relation, archived_at_week FROM week_knowledge_edges")
        state.update({("edge", relation): week for relation, week in cur.fetchall()})
    return state


@pytest.fixture
def graph(pg_conn):
    """Two characters with nodes in weeks 1-3 and one edge per week (ceo).

    ceo: knows-standup-is-nine (w1), fears-the-audit (w2),
         wants-the-pilot-live (w3); an old node already archived at week 1.
    tester: suspects-flaky-ci (w2).
    """
    ceo = seed_character(pg_conn, "ceo")
    tester = seed_character(pg_conn, "tester")
    ids = {}
    for week, name in ((1, "knows-standup-is-nine"), (2, "fears-the-audit"),
                       (3, "wants-the-pilot-live"), (1, "remembers-old-launch")):
        ids[name] = knowledge.insert_node(pg_conn, character_id=ceo, loop_week=week, name=name,
                                          kind="fact", statement=f"I {name.replace('-', ' ')}")
    ids["suspects-flaky-ci"] = knowledge.insert_node(
        pg_conn, character_id=tester, loop_week=2, name="suspects-flaky-ci", kind="fact",
        statement="I suspect CI is flaky", loop_day=date(2026, 10, 7))
    knowledge.insert_edge(pg_conn, character_id=ceo, loop_week=1, relation="w1-edge",
                          src_node_id=ids["knows-standup-is-nine"],
                          dst_node_id=ids["remembers-old-launch"])
    knowledge.insert_edge(pg_conn, character_id=ceo, loop_week=2, relation="w2-edge",
                          src_node_id=ids["fears-the-audit"],
                          dst_node_id=ids["knows-standup-is-nine"])
    knowledge.insert_edge(pg_conn, character_id=ceo, loop_week=3, relation="w3-edge",
                          src_node_id=ids["wants-the-pilot-live"],
                          dst_node_id=ids["fears-the-audit"])
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE week_knowledge_nodes SET archived_at_week = 1 WHERE name = %s",
                    ("remembers-old-launch",))
    pg_conn.commit()
    return {"ceo": ceo, "tester": tester, "ids": ids}


# T05.10
def test_archive_week_marks_unarchived_nodes_and_edges_up_to_week(pg_conn, graph):
    counts = knowledge.archive_week(pg_conn, 2)
    pg_conn.commit()
    assert counts == {"nodes": 3, "edges": 2}
    assert _archive_state(pg_conn) == {
        ("node", "knows-standup-is-nine"): 2,
        ("node", "fears-the-audit"): 2,
        ("node", "suspects-flaky-ci"): 2,       # every character
        ("node", "wants-the-pilot-live"): None,  # week 3 > W
        ("node", "remembers-old-launch"): 1,     # already archived: untouched
        ("edge", "w1-edge"): 2,
        ("edge", "w2-edge"): 2,
        ("edge", "w3-edge"): None,
    }
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM week_knowledge_nodes")
        assert cur.fetchone()[0] == 5  # nothing deleted
    assert knowledge.archive_week(pg_conn, 2) == {"nodes": 0, "edges": 0}


# T05.11
def test_current_nodes_excludes_archived(pg_conn, graph):
    ceo = graph["ceo"]
    names = [node["name"] for node in knowledge.current_nodes(pg_conn, ceo, 3)]
    assert sorted(names) == ["fears-the-audit", "knows-standup-is-nine", "wants-the-pilot-live"]
    knowledge.archive_week(pg_conn, 2)
    pg_conn.commit()
    nodes = knowledge.current_nodes(pg_conn, ceo, 3)
    assert [node["name"] for node in nodes] == ["wants-the-pilot-live"]
    assert nodes[0]["statement"] == "I wants the pilot live"
    assert knowledge.current_nodes(pg_conn, graph["tester"], 3) == []
    assert [n["name"] for n in knowledge.current_nodes(pg_conn, ceo, 2)] == []


# T05.12
def test_unarchive_week_reverses_archive_week(pg_conn, graph):
    before = _archive_state(pg_conn)
    knowledge.archive_week(pg_conn, 2)
    pg_conn.commit()
    counts = knowledge.unarchive_week(pg_conn, 2)
    pg_conn.commit()
    assert counts == {"nodes": 3, "edges": 2}
    assert _archive_state(pg_conn) == before
    assert _archive_state(pg_conn)[("node", "remembers-old-launch")] == 1

