"""WP-22 tests for app/character/brief.py: the brief a character plays from.

Frozen test list (playbook §4 WP-22, items 1-6; plan §8, D-08). The brief is
assembled from the DB, never stored: who you are, what you remember (the
`believed` layer only), what you want, what you've learned (this week's
unarchived knowledge nodes, newest first, capped), feelings you carry
(unlocked fragment gists, framed as instincts), and the behaviour contract.
It never contains the truth layer, dormant gists, week numbers, or loop /
reset / repetition wording ("time repeats", "again and again", "same week",
...: fakes_e2e.FORBIDDEN_BRIEF_RE). The ordinary word "week" in a character's
own experience ("one green week", "three weeks") is allowed and kept (user
decision 2026-09-28 (item 1): "the characters are unaware of time passing; to
them it's the same week over and over"). It is capped at brief.max_chars
(12000, user decision 2026-09-28 (item 2)) by dropping the oldest knowledge
lines first.

OB-41 test corrections (tracker .claude/prompts/character_v4_tracker_phase3b.md):
- Plan §8 heading 4 is "What you've learned this week". The heading is "What
  you've learned recently" (D-08 "no mention of weeks": a heading that frames
  knowledge by the week points at the week boundary). Kept after user decision
  2026-09-28 (item 1), which allows the plain word "week" in the character's
  own experience but not in the brief's framing.
- OB-41 E6 adds a "Today's directive" section (from app/office/brief_stub.py)
  between the feelings and the behaviour contract; the six plan sections keep
  their plan order around it.
- The office cast files carry the literal sentence "Never state or imply that
  time repeats." (profiles/_SCHEMA.md); the brief drops every sentence with a
  forbidden word, so that sentence never reaches the brief.
- T22.2 narrowed (user decision 2026-09-28 (item 1)): "never the word week" ->
  never a week NUMBER; sentences with the plain word "week" ("We overlapped for
  a few weeks", "I know Owen wants one green week.") must be KEPT.
The drop-in contract for app/office/brief_stub.py is tested in
test_character_brief_persona.py.
"""
import re

import psycopg2.extras
import pytest
import yaml

from fakes import FakeClock, seed_character
from fakes_e2e import ALLOWED_BRIEF_SAMPLES, FORBIDDEN_BRIEF_RE, FORBIDDEN_BRIEF_SAMPLES, OFFICE_PACK
from pending import require

brief = require("character.brief", "app/character/brief.py", wp="WP-22")

PLAN_HEADINGS = [
    "## Who you are",
    "## What you remember of your life so far",
    "## What you want",
    "## What you've learned recently",
    "## Feelings you carry",
    "## Today's directive",
    "## How you behave",
]
UNLOCKED_GIST = "The Glass Box went dark and my hands would not stop shaking."
DORMANT_GIST = "DORMANT-MARKER-51c2 I felt the floor tilt under the red graph."
TRUTH_MARKER = "TRUTH-MARKER-7f3a"


def _full_parts(**overrides):
    fields = dict(
        slug="engineer", name="Theo Palliser",
        who="You are Theo Palliser, the Engineer at Ashiorid. You build everything under src/.",
        remember="Okay, so. Two years. Delphine hired me, and I still can't quite believe it.",
        wants=("the Tech Lead's praise, once, in front of everyone",),
        fears=("being the next empty desk",),
        learned=("I know the deploy bar froze at noon.", "I know Owen's run was red."),
        feelings=(UNLOCKED_GIST,),
        contract=("Joke before bad news.",),
    )
    fields.update(overrides)
    return brief.BriefParts(**fields)


def _section(text, heading):
    """The body under `heading`, up to the next '## ' heading."""
    start = text.index(heading) + len(heading)
    nxt = text.find("\n## ", start)
    return text[start:] if nxt == -1 else text[start:nxt]


# T22.1
def test_sections_come_out_in_plan_order_with_their_headings():
    text = brief.render(_full_parts(), directive="Ship the amount-outlier fix.")
    assert list(brief.SECTION_ORDER) == ["who", "remember", "want", "learned", "feelings",
                                         "directive", "contract"]
    assert [brief.HEADINGS[key] for key in brief.SECTION_ORDER] == PLAN_HEADINGS
    positions = [text.index(heading) for heading in PLAN_HEADINGS]
    assert positions == sorted(positions)
    assert text.startswith("## Who you are\n")
    assert "Two years. Delphine hired me" in _section(text, "## What you remember of your life so far")
    want = _section(text, "## What you want")
    assert "the Tech Lead's praise, once, in front of everyone" in want
    assert "being the next empty desk" in want
    assert "I know the deploy bar froze at noon." in _section(text, "## What you've learned recently")
    assert "Ship the amount-outlier fix." in _section(text, "## Today's directive")
    contract = _section(text, "## How you behave")
    for line in brief.BEHAVIOUR_CONTRACT:
        assert line in contract
    assert "Joke before bad news." in contract
    # no directive given -> no directive section; empty sections are left out
    bare = brief.render(_full_parts(learned=(), feelings=()))
    assert "## Today's directive" not in bare
    assert "## What you've learned recently" not in bare
    assert "## Feelings you carry" not in bare
    assert bare.index("## What you want") < bare.index("## How you behave")


@pytest.fixture
def engineer_db(pg_conn):
    """The engineer as Phase 2's office loader stores him, plus week 1-2 memory."""
    with open(OFFICE_PACK / "profiles" / "engineer.yaml", encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)
    profile = {key: value for key, value in doc.items() if key not in ("backstory", "backstory_nodes")}
    profile["backstory"] = {"truth": TRUTH_MARKER}  # defensive: the loader must never read it
    cid = seed_character(pg_conn, "engineer", name="Theo Palliser")
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO character_baselines (character_id, version, profile, baseline_book, "
                    "baseline_chapter) VALUES (%s, 1, %s, 0, 0)", (cid, psycopg2.extras.Json(profile)))
        cur.execute("UPDATE characters SET active_baseline_version = 1 WHERE id = %s", (cid,))
        for layer, text in (("believed", doc["backstory"]["believed"]),
                            ("truth", f"{TRUTH_MARKER} {doc['backstory']['truth']}")):
            cur.execute("INSERT INTO character_backstories (character_id, version, layer, content) "
                        "VALUES (%s, 1, %s, %s)", (cid, layer, psycopg2.extras.Json({"text": text})))
        nodes = [(1, "remembers-archived-thing", "I remember ARCHIVED-MARKER-9d1e from before.", 1),
                 (2, "knows-deploy-bar-froze", "I know the deploy bar froze at noon.", None),
                 (2, "knows-retry-loop-bug", "I know there is a retry loop in the scorer.", None),
                 (2, "knows-rough-stretch", "Week 2 has been rough. I kept my head down.", None),
                 (2, "knows-owen-wants-green", "I know Owen wants one green week.", None),
                 (2, "feels-same-week", "It feels like the same week, again and again.", None)]
        for week, name, statement, archived in nodes:
            cur.execute("INSERT INTO week_knowledge_nodes (id, character_id, loop_week, name, kind, "
                        "statement, archived_at_week) VALUES (%s, %s, %s, %s, 'fact', %s, %s)",
                        (f"n-{name}", cid, week, name, statement, archived))
        for fid, gist in (("f-dormant", DORMANT_GIST), ("f-unlocked", UNLOCKED_GIST)):
            cur.execute("INSERT INTO memory_fragments (id, character_id, source_week, gist) "
                        "VALUES (%s, %s, 1, %s)", (fid, cid, gist))
        cur.execute("INSERT INTO fragment_unlocks (fragment_id, character_id, unlocked_week) "
                    "VALUES ('f-unlocked', %s, 2)", (cid,))
    pg_conn.commit()
    return cid


# T22.2
@pytest.mark.integration
def test_never_contains_truth_dormant_gists_week_numbers_or_loop_words(pg_conn, engineer_db):
    parts = brief.load_parts(pg_conn, "engineer", 2, pack_dir=OFFICE_PACK)
    text = brief.render(parts, directive="Fix the week 3 loop reset bug.", max_chars=100_000)
    assert TRUTH_MARKER not in text
    assert "DORMANT-MARKER" not in text and "floor tilt" not in text
    assert "ARCHIVED-MARKER" not in text
    assert FORBIDDEN_BRIEF_RE.search(text) is None, FORBIDDEN_BRIEF_RE.search(text)
    assert "time repeats" not in text.lower()
    assert not re.search(r"\bW\d+\b", text)
    # what may be there, is there
    assert "You are Theo Palliser, the Engineer at Ashiorid" in text
    assert "Okay, so. Two years." in _section(text, "## What you remember of your life so far")
    learned = _section(text, "## What you've learned recently")
    assert "I know the deploy bar froze at noon." in learned
    assert UNLOCKED_GIST in _section(text, "## Feelings you carry")
    # the plain word "week" in the character's own experience is kept (user decision 2026-09-28, item 1)
    assert "I know Owen wants one green week." in learned
    assert "I kept my head down." in learned and "Week 2" not in learned
    assert "same week" not in text and "again and again" not in text
    remember = " ".join(_section(text, "## What you remember of your life so far").split())
    assert "We overlapped for a few weeks, right at the start" in remember
    assert "The Office Manager moved their chair a week later." in remember
    for key in ("remember", "learned", "feelings"):
        assert FORBIDDEN_BRIEF_RE.search(brief.scrub(getattr(parts, key) if isinstance(
            getattr(parts, key), str) else " ".join(getattr(parts, key)))) is None
    assert brief.scrub("One line. Never state or imply that time repeats. Another line.") == \
        "One line. Another line."


# T22.2 (the forbidden list, user decision 2026-09-28 (item 1))
@pytest.mark.parametrize("sentence", FORBIDDEN_BRIEF_SAMPLES)
def test_scrub_drops_week_numbers_and_loop_or_repetition_wording(sentence):
    assert brief.FORBIDDEN_RE.search(sentence)
    assert brief.scrub(f"Before. {sentence} After.") == "Before. After."


@pytest.mark.parametrize("sentence", ALLOWED_BRIEF_SAMPLES)
def test_scrub_keeps_the_plain_word_week(sentence):
    assert brief.FORBIDDEN_RE.search(sentence) is None
    assert brief.scrub(f"Before. {sentence} After.") == f"Before. {sentence} After."


# T22.3
def test_unlocked_fragments_appear_as_feelings():
    text = brief.render(_full_parts(feelings=(UNLOCKED_GIST, "The server closet smelled of rain.")))
    feelings = _section(text, "## Feelings you carry")
    assert f"- {UNLOCKED_GIST}" in feelings
    assert "- The server closet smelled of rain." in feelings
    assert feelings.index(UNLOCKED_GIST) < feelings.index("server closet")
    assert UNLOCKED_GIST not in _section(text, "## What you've learned recently")
    assert re.search(r"\d{4}-\d{2}-\d{2}", feelings) is None
    assert FORBIDDEN_BRIEF_RE.search(feelings) is None


# T22.4
def test_stays_under_max_chars_by_dropping_oldest_knowledge_first():
    learned = tuple(f"I noticed detail number {i:02d} today." for i in range(30, 0, -1))
    parts = _full_parts(learned=learned)
    full = brief.render(parts, max_chars=100_000)
    assert all(line in full for line in learned)
    budget = len(brief.render(_full_parts(learned=()), max_chars=100_000)) + 400
    text = brief.render(parts, max_chars=budget)
    assert len(text) <= budget
    kept = [line for line in learned if line in text]
    assert 0 < len(kept) < len(learned)
    assert kept == list(learned[:len(kept)])           # the newest survive, in order
    assert "detail number 01" not in text
    assert "Two years. Delphine hired me" in text        # the backstory is untouched
    capped = brief.render(parts, max_chars=100_000, week_knowledge_max=5)
    assert [line for line in learned if line in capped] == list(learned[:5])
    tiny = brief.render(parts, max_chars=300)
    assert len(tiny) <= 300 and tiny.startswith("## Who you are")


# T22.5
def test_brief_cache_ttl_hit_miss_and_invalidate():
    clock = FakeClock(1000.0)
    loads = []

    def loader(slug):
        loads.append(slug)
        return f"brief for {slug} #{len(loads)}"

    cache = brief.BriefCache(loader, ttl_s=300, clock=clock)
    first = cache.get("engineer")
    clock.advance(299.0)
    assert cache.get("engineer") == first and loads == ["engineer"]
    clock.advance(1.0)                                   # age == ttl -> expired
    second = cache.get("engineer")
    assert second != first and loads == ["engineer", "engineer"]
    cache.get("tester")
    cache.invalidate("engineer")
    cache.get("engineer")
    cache.get("tester")
    assert loads == ["engineer", "engineer", "tester", "engineer"]
    cache.invalidate("nobody")                           # unknown slug: no error
    cache.invalidate_all()
    cache.get("engineer")
    cache.get("tester")
    assert loads[-2:] == ["engineer", "tester"] and len(loads) == 6


# T22.6
def test_includes_extra_feeling_surfaces_lines_from_the_caller():
    line = f"a feeling surfaces: {UNLOCKED_GIST}"
    text = brief.render(_full_parts(feelings=()), extra_feelings=[line])
    assert f"- {line}" in _section(text, "## Feelings you carry")
    both = brief.render(_full_parts(), extra_feelings=[line, "something about this feels familiar"])
    feelings = _section(both, "## Feelings you carry")
    assert feelings.index(f"- {UNLOCKED_GIST}") < feelings.index(f"- {line}")
    assert "- something about this feels familiar" in feelings
    assert line not in brief.render(_full_parts())       # nothing is remembered between calls
