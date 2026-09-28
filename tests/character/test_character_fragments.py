"""WP-20 tests for app/character/fragments.py: selecting and creating a week's fragments.

Frozen test list (playbook §4 WP-20, items 3, 4 and 5; the weekly-reset job's
items are in test_character_jobs_weekly.py). Plan §2 / D-08 / D-09: for
characters with `retains_fragments`, the weekly reset creates
`fragments_per_week` lossy, emotional, FIRST-PERSON fragments, each stored with
the `lead_up_beats` beats that came before its moment (embedded, D-20) and its
hooks {entities, places, objects, tone, weekday, hour} (v3 §7). The model picks
the moment (the anchor event); the lead-up is taken from the events BEFORE it,
in order, never chosen by the model (app/character/prompts/fragment.md).

OB-41 adaptations (test corrections, cited: .claude/prompts/ashiorid_office_build_plan.md
OB-41 "retains_fragments: true for all 8"): item 4's "a harry fragment" is an
engineer fragment. Every office character retains fragments, so item 3 uses a
synthetic fixture character `visitor` with retains_fragments false (a raw
`characters` row, not part of the cast).
"""
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from fakes import seed_character
from fakes_runtime import FakeEmbed, FakeLLM, seed_event
from pending import require, skip_if_pending

fragments = require("character.fragments", "app/character/fragments.py", wp="WP-20")
skip_if_pending("app/character/prompts/fragment.md", wp="WP-11")   # the by-hand template
from character.clock import LoopClock  # noqa: E402
from character.store import fragments as fragment_store  # noqa: E402

pytestmark = pytest.mark.integration

CLOCK = LoopClock(date(2026, 9, 27), "America/New_York")
START = datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc)      # Tue 09:00 NY, week 1
HOOKS = {"entities": ["tech-lead", "Corvane"], "places": ["Glass Box"],
         "objects": ["latency graph"], "tone": "dread"}


def _character(conn, slug, retains=True):
    character_id = seed_character(conn, slug, retains_fragments=retains)
    conn.commit()
    return {"id": character_id, "slug": slug, "name": slug.title(), "title": slug.title(),
            "retains_fragments": retains}


def _week(conn, character, n=12, anchor_index=10):
    """n events an hour apart; the anchor's text says ANCHOR. Returns the events in order."""
    seeded = []
    for i in range(n):
        ts = START + timedelta(hours=i)
        text = f"ANCHOR the latency graph went red ({i})" if i == anchor_index else f"beat {i}"
        message_id = seed_event(conn, character["id"], text=text, ts=ts, loop_week=1,
                                loop_day=CLOCK.position(ts)[1], message_id=f"m{i:02d}-{character['slug']}")
        seeded.append({"message_id": message_id, "text": text, "ts": ts})
    # a week-2 event and nothing else: must never be a candidate or a beat
    seed_event(conn, character["id"], text="next week", ts=START + timedelta(days=6), loop_week=2,
               loop_day=date(2026, 10, 5))
    conn.commit()
    return seeded


def pick_anchor(gist):
    """A FakeLLM reply builder: choose the moment whose text says ANCHOR."""
    def reply(user):
        line = next(l for l in user.splitlines() if "ANCHOR" in l)
        return {"anchor_event_id": line.split(":", 1)[0].strip(), "gist": gist, "hooks": HOOKS,
                "name": "fears-the-red-latency-graph", "rank_rationale": "It frightened me."}
    return reply


def _select(conn, character, llm, embed=None, **kwargs):
    return fragments.select_fragments(conn, CLOCK, character, 1, complete=llm,
                                      embed=embed or FakeEmbed(), n=1, lead_up_beats=8,
                                      embed_model="fake-embed", **kwargs)


# T20.3
def test_character_without_retains_fragments_gets_no_fragments(pg_conn):
    visitor = _character(pg_conn, "visitor", retains=False)
    _week(pg_conn, visitor)
    llm, embed = FakeLLM(), FakeEmbed()
    assert _select(pg_conn, visitor, llm, embed) == []
    assert llm.calls == [] and embed.calls == []
    assert fragment_store.dormant_for(pg_conn, visitor["id"]) == []


# T20.4
def test_fragment_has_first_person_gist_embedded_lead_up_and_hooks(pg_conn):
    engineer = _character(pg_conn, "engineer")
    _week(pg_conn, engineer)
    llm = FakeLLM(pick_anchor("He watched the graph go red."),              # third person: rejected
                  pick_anchor("The graph went red and I felt the floor tilt under me."))
    embed = FakeEmbed(dim=4)
    (fragment_id,) = _select(pg_conn, engineer, llm, embed)
    assert len(llm.calls) == 2 and "first person" in llm.calls[1]["user"].lower()
    assert llm.calls[0]["shape"] == fragments.FRAGMENT_SHAPE
    (row,) = fragment_store.dormant_for(pg_conn, engineer["id"])
    assert row["id"] == fragment_id and row["source_week"] == 1
    assert row["gist"] == "The graph went red and I felt the floor tilt under me."
    assert fragments.is_first_person(row["gist"]) and not fragments.is_first_person("He sat.")
    hooks = row["hooks"]
    assert {k: hooks[k] for k in HOOKS} == HOOKS
    assert (hooks["weekday"], hooks["hour"]) == ("tuesday", 19)   # the anchor: Tue 19:00 NY
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*), count(embedding) FROM fragment_lead_up WHERE fragment_id = %s",
                    (fragment_id,))
        assert cur.fetchone() == (8, 8)
        cur.execute("SELECT anchor_event_id, gist_embedding IS NOT NULL, embed_model, embed_dim "
                    "FROM memory_fragments WHERE id = %s", (fragment_id,))
        assert cur.fetchone() == ("m10-engineer", True, "fake-embed", 4)


# T20.5
def test_lead_up_comes_from_events_before_the_moment_in_order(pg_conn):
    engineer = _character(pg_conn, "engineer")
    seeded = _week(pg_conn, engineer)
    llm = FakeLLM(pick_anchor("I remember the red graph and my stomach dropping."))
    (fragment_id,) = _select(pg_conn, engineer, llm)
    beats = fragment_store.lead_up_for(pg_conn, fragment_id)
    assert [b["position"] for b in beats] == list(range(8))
    assert [b["text"] for b in beats] == [e["text"] for e in seeded[2:10]]
    assert [b["event_id"] for b in beats] == [e["message_id"] for e in seeded[2:10]]
    assert "next week" not in llm.calls[0]["user"]      # week 2 is not a candidate
    # pure helper: an early anchor gives a shorter lead-up; never the anchor or later
    events = [{"message_id": f"e{i}", "text": f"t{i}"} for i in range(6)]
    assert [e["message_id"] for e in fragments.lead_up_before(events, "e3", 8)] == ["e0", "e1", "e2"]
    assert fragments.lead_up_before(events, "e0", 8) == []
    with pytest.raises(ValueError):
        fragments.lead_up_before(events, "missing", 8)


# T20.4 (an anchor that is not one of the week's events is rejected, then skipped)
def test_unknown_anchor_is_retried_once_then_skipped(pg_conn):
    engineer = _character(pg_conn, "engineer")
    _week(pg_conn, engineer)
    bad = {"anchor_event_id": "not-an-event", "gist": "I felt it.", "hooks": HOOKS}
    llm = FakeLLM(bad, bad)
    assert _select(pg_conn, engineer, llm) == []
    assert len(llm.calls) == 2
    assert fragment_store.dormant_for(pg_conn, engineer["id"]) == []


# T20.2 support: fragment creation commits (store.fragments.create_fragment owns its
# transaction), so the step must not double a week's fragments when it is re-run.
def test_select_skips_a_character_that_already_has_its_fragments_for_the_week(pg_conn):
    engineer = _character(pg_conn, "engineer")
    _week(pg_conn, engineer)
    assert len(_select(pg_conn, engineer, FakeLLM(pick_anchor("I froze.")))) == 1
    again = FakeLLM()
    assert _select(pg_conn, engineer, again) == []
    assert again.calls == []
    assert fragments.existing_count(pg_conn, engineer["id"], 1) == 1


# the fragment prompt is the WP-11 template, filled with the week's summaries and moments
def test_prompt_lists_moments_as_id_colon_text(pg_conn):
    engineer = _character(pg_conn, "engineer")
    _week(pg_conn, engineer, n=3, anchor_index=2)
    llm = FakeLLM(pick_anchor("I still hear the alarm."))
    _select(pg_conn, engineer, llm)
    user = llm.calls[0]["user"]
    assert "${" not in user and "${" not in llm.calls[0]["system"]
    assert re.search(r"^m00-engineer: beat 0$", user, re.M)
    assert re.search(r"^m02-engineer: ANCHOR", user, re.M)
