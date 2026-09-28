"""WP-19 tests for app/character/summaries.py: one character's day -> summary + nodes.

Frozen test list (playbook §4 WP-19, items 1, 2, 3 and 6; items 4, 5 and 7 are
about the daily-maintenance job and live in test_character_jobs_daily.py).
D-21: each night the `summary` profile turns a character's day of
experience_events into one daily_summaries row (a first-person paragraph) plus
up to 10 week_knowledge_nodes whose names pass node_names.check(); a bad name
is retried once, then dropped and logged (plan §10). A day with no events
gets a "quiet day" summary without an LLM call. The key is
(character_id, loop_day), so a re-run is a no-op.

The LLM is a FakeLLM `complete(system, user, shape)` and the name check is
injected, so these tests don't depend on WP-11's live client or word lists.

OB-41 adaptations (test corrections, cited: .claude/prompts/ashiorid_office_build_plan.md
OB-41): office characters (engineer, tester, party_member) instead of harry;
the Party Member's experience is only `visibility: present` events, so his
summary is built from present events alone (explicit OB-41 test below).
"""
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from fakes import seed_character
from fakes_runtime import FakeLLM, seed_event
from pending import require, skip_if_pending

summaries = require("character.summaries", "app/character/summaries.py", wp="WP-19")
skip_if_pending("app/character/prompts/summary_day.md", wp="WP-11")   # the by-hand template
from character.clock import LoopClock  # noqa: E402
from character.store import knowledge  # noqa: E402

pytestmark = pytest.mark.integration

CLOCK = LoopClock(date(2026, 9, 27), "America/New_York")
DAY = date(2026, 9, 29)                                   # a Tuesday in week 1
GOOD = re.compile(r"(knows|fears|trusts|wants|hopes)(-[a-z0-9]+){1,7}")


def check_name(name):
    return [] if GOOD.fullmatch(name or "") else ["first word must be an allowed verb"]


def _node(i, name=None):
    return {"name": name or f"knows-fact-number-{i}", "kind": "fact",
            "statement": f"I know fact number {i}."}


def _reply(nodes, text="I spent the day chasing a flaky fixture with the tester."):
    return {"summary": text, "nodes": nodes}


def _character(conn, slug):
    character_id = seed_character(conn, slug)
    conn.commit()
    return {"id": character_id, "slug": slug, "name": slug.replace("_", " ").title(),
            "title": slug.replace("_", " ").title(), "retains_fragments": True}


def _at(hour, minute=0):
    """A UTC instant on DAY at `hour`:`minute` New York time (EDT, UTC-4)."""
    return datetime(2026, 9, 29, 4, tzinfo=timezone.utc) + timedelta(hours=hour, minutes=minute)


def _seed(conn, character, text, hour, visibility="present", **kwargs):
    return seed_event(conn, character["id"], text=text, ts=_at(hour), loop_week=1, loop_day=DAY,
                      visibility=visibility, **kwargs)


def _summarise(conn, character, llm, **kwargs):
    return summaries.summarise_day(conn, CLOCK, character, DAY, complete=llm,
                                   check_name=check_name, **kwargs)


# T19.1
def test_day_with_events_gives_one_summary_and_up_to_10_valid_nodes(pg_conn):
    engineer = _character(pg_conn, "engineer")
    ids = [_seed(pg_conn, engineer, f"event {i}", 9 + i) for i in range(3)]
    pg_conn.commit()
    first = [_node(i) for i in range(11)] + [_node(99, "tech-lead-is-trusted")]
    second = [_node(i) for i in range(11)] + [_node(98, "Still_Bad")]
    llm = FakeLLM(_reply(first), _reply(second))
    result = _summarise(pg_conn, engineer, llm)
    pg_conn.commit()

    assert result["status"] == "written" and result["events"] == 3
    assert len(llm.calls) == 2                               # one retry for the bad name
    assert "tech-lead-is-trusted" in llm.calls[1]["user"]
    assert llm.calls[0]["shape"] == summaries.SUMMARY_SHAPE
    row = knowledge.get_daily_summary(pg_conn, engineer["id"], DAY)
    assert row["event_count"] == 3 and row["loop_week"] == 1
    assert row["summary"]["summary"].startswith("I spent the day")
    assert row["summary"]["partial"] is False and row["summary"]["quiet"] is False
    nodes = knowledge.current_nodes(pg_conn, engineer["id"], 1)
    assert len(nodes) == summaries.MAX_NODES == 10
    assert all(check_name(n["name"]) == [] for n in nodes)
    assert {n["loop_day"] for n in nodes} == {DAY}
    assert all(set(n["source_event_ids"]) <= set(ids) for n in nodes)
    assert "I" in nodes[0]["statement"].split()


# T19.1 (the summary prompt is the WP-11 template, filled with the day's events)
def test_prompt_is_rendered_from_the_summary_day_template(pg_conn):
    tester = _character(pg_conn, "tester")
    _seed(pg_conn, tester, "The suite went green at last.", 22, visibility="self",
          msg_type="agent_thinking", from_agent="tuber_4")
    pg_conn.commit()
    llm = FakeLLM(_reply([_node(1)]))
    _summarise(pg_conn, tester, llm)
    call = llm.calls[0]
    assert "${" not in call["system"] and "${" not in call["user"]
    assert "Tester" in call["system"] and "first person" in call["system"].lower()
    assert "Tuesday 29 September 2026" in call["user"]
    assert "22:00 [self]" in call["user"] and "The suite went green at last." in call["user"]


# T19.2
def test_day_with_no_events_says_quiet_day_without_an_llm_call(pg_conn):
    tester = _character(pg_conn, "tester")
    llm = FakeLLM()                                          # raises if called
    result = _summarise(pg_conn, tester, llm)
    pg_conn.commit()
    assert result["status"] == "quiet" and llm.calls == []
    row = knowledge.get_daily_summary(pg_conn, tester["id"], DAY)
    assert "quiet day" in row["summary"]["summary"].lower()
    assert row["summary"]["quiet"] is True and row["event_count"] == 0
    assert knowledge.current_nodes(pg_conn, tester["id"], 1) == []


# T19.3
def test_rerun_for_the_same_character_and_day_is_a_no_op(pg_conn):
    engineer = _character(pg_conn, "engineer")
    _seed(pg_conn, engineer, "Standup ran long.", 9)
    pg_conn.commit()
    llm = FakeLLM(_reply([_node(1), _node(2)]))
    assert _summarise(pg_conn, engineer, llm)["status"] == "written"
    pg_conn.commit()
    again = FakeLLM()
    assert _summarise(pg_conn, engineer, again)["status"] == "exists"
    assert again.calls == []
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM daily_summaries WHERE character_id = %s", (engineer["id"],))
        assert cur.fetchone()[0] == 1
    assert len(knowledge.current_nodes(pg_conn, engineer["id"], 1)) == 2


# T19.6
def test_only_own_self_events_plus_present_events_are_summarised(pg_conn):
    engineer = _character(pg_conn, "engineer")
    tester = _character(pg_conn, "tester")
    _seed(pg_conn, engineer, "ENGINEER-THOUGHT the fixture leaks", 10, visibility="self",
          msg_type="agent_thinking", from_agent="tuber_3")
    _seed(pg_conn, engineer, "SHARED-SAY build is red", 11, from_agent="tuber_4")
    _seed(pg_conn, tester, "TESTER-THOUGHT blame the engineer", 11, visibility="self",
          msg_type="agent_thinking", from_agent="tuber_4")
    pg_conn.commit()
    llm = FakeLLM(_reply([_node(1)]))
    result = _summarise(pg_conn, engineer, llm)
    user = llm.calls[0]["user"]
    assert "ENGINEER-THOUGHT" in user and "SHARED-SAY" in user
    assert "TESTER-THOUGHT" not in user
    assert result["events"] == 2


# OB-41: the Party Member's summary comes from `present` events only.
def test_party_member_summary_is_built_from_present_events_only(pg_conn):
    party = _character(pg_conn, "party_member")
    _seed(pg_conn, party, "The CEO calls standup.", 9, from_agent="tuber_0")
    _seed(pg_conn, party, "The Party Member files past the Glass Box.", 13,
          msg_type="scene_event", from_agent="office_clock")
    pg_conn.commit()
    llm = FakeLLM(_reply([_node(1, "knows-standup-is-at-nine")],
                         text="I watched them gather for standup and said nothing."))
    assert _summarise(pg_conn, party, llm)["events"] == 2
    lines = [line for line in llm.calls[0]["user"].splitlines() if re.match(r"^\d\d:\d\d \[", line)]
    assert len(lines) == 2 and all("[present]" in line for line in lines)
    assert not any("[self]" in line for line in lines)


# T19.1 (pure helpers the job and the fragment step share)
def test_day_label_and_event_rendering():
    assert summaries.day_label(DAY) == "Tuesday 29 September 2026"
    rendered = summaries.render_events([
        {"ts": _at(9, 5), "visibility": "present", "from_agent": "tuber_0",
         "payload": {"character": "ceo", "text": "Standup."}, "text": "Standup."},
        {"ts": _at(9, 7), "visibility": "self", "from_agent": "tuber_3",
         "payload": {"text": "Why is CI red?"}, "text": "Why is CI red?"},
    ], CLOCK.zone)
    assert rendered.splitlines() == ["09:05 [present] ceo: Standup.",
                                     "09:07 [self] (my own thought) Why is CI red?"]
