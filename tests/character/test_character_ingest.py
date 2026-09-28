"""WP-16 tests for app/character/ingest.py: routing bus messages to experience rows.

Frozen test list (playbook §4 WP-16, items 1-11; item 12, the backfill job,
is in test_character_jobs_ingest.py). Plan §3.3:

- allowlist agent_thinking, character_say, scene_event; everything else is
  skipped before its payload is looked at.
- `route(msg, agents, slugs, clock) -> list[row]` is pure:
  agent_thinking -> character_agents[from] gives one row, visibility 'self'
  (unmapped senders are counted and skipped); character_say / scene_event ->
  one row per known slug in present ∪ {character} ∪ addressees, visibility
  'present'. Week/day come from the BODY timestamp via clock.py; pre-epoch
  timestamps are counted and skipped.
- KafkaConsumer group character-ingest, auto_offset_reset earliest,
  enable_auto_commit False; offsets are committed after the DB commit, in
  batches of up to 200 messages or 2 s. Redelivery is harmless (ON CONFLICT).
- A malformed message is logged at ERROR, counted and skipped.

OB-41 adaptations (test corrections, cited: .claude/prompts/ashiorid_office_build_plan.md
OB-41): office slugs instead of harry/ron/hermione; the non-character in
`present` is the office clock "office_clock" (app/office/protocol.py CLOCK_SENDER)
instead of "gm"; office seats publish from `tuber_N` (app/office/roles.py SEAT),
so the agents map holds both `char:<slug>` and `tuber_N`, and item 1 is run for
both sender forms. The Party Member is a full character who just has no
lines at the moment (user decision 2026-09-28 (item 8), tracker P3a-3):
character_agents holds `char:party_member` and `tuber_7` like every other
seat, so his own agent_thinking routes as `self`; there is no slug exception.
"""
import logging
from collections import Counter
from datetime import date, datetime, timezone

import pytest

from fakes import OFFICE_SLUGS, FakeClock, seed_character
from fakes_runtime import (OFFICE_SEATS, FakeConn, FakeConsumer, count, say, scene, seed_agents,
                           thinking)
from pending import require

ingest = require("character.ingest", "app/character/ingest.py", wp="WP-16")
from character import config  # noqa: E402  (promoted in WP-03)
from character.clock import LoopClock  # noqa: E402
from character.store import events  # noqa: E402  (promoted in WP-05)

CLOCK = LoopClock(date(2026, 9, 27), "America/New_York")   # office epoch (week 1 = Sep 27)
TUESDAY = "2026-09-29T14:00:00+00:00"                        # 10:00 NY, week 1
SLUGS = {slug: f"id-{slug}" for slug in OFFICE_SLUGS}
#: char:<slug> and the seat id for every office character, the Party Member included
#: (Phase 2 T10o.7 / T14.13 seeding; user decision 2026-09-28 (item 8)).
AGENTS = {**{f"char:{s}": SLUGS[s] for s in OFFICE_SLUGS},
          **{OFFICE_SEATS[s]: SLUGS[s] for s in OFFICE_SLUGS}}
EVENT_KEYS = {"message_id", "character_id", "msg_type", "from_agent", "scene_id",
              "visibility", "text", "payload", "ts", "loop_week", "loop_day"}


def _route(msg, counts=None, **kwargs):
    return ingest.route(msg, AGENTS, SLUGS, CLOCK, counts=counts, **kwargs)


class Explosive:
    """A payload that fails on any use: proves route never looked at it."""

    def __getattr__(self, name):
        raise AssertionError(f"payload touched: .{name}")

    def __getitem__(self, key):
        raise AssertionError(f"payload touched: [{key!r}]")

    def __iter__(self):
        raise AssertionError("payload iterated")


# T16.1
@pytest.mark.parametrize("sender", ["char:engineer", "tuber_3"])
def test_agent_thinking_from_mapped_sender_gives_one_self_row(sender):
    msg = thinking(sender, "The flaky test is the fixture, not the code.", TUESDAY)
    (row,) = _route(msg)
    assert set(row) == EVENT_KEYS
    assert row["character_id"] == "id-engineer"
    assert row["visibility"] == "self"
    assert row["message_id"] == msg["id"]
    assert row["msg_type"] == "agent_thinking" and row["from_agent"] == sender
    assert row["text"] == "The flaky test is the fixture, not the code."
    assert row["payload"] == msg["payload"]
    assert row["ts"] == datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
    assert (row["loop_week"], row["loop_day"]) == (1, date(2026, 9, 29))


# T16.2
@pytest.mark.parametrize("sender", ["coder", "tuber_9", "char-live:ashiorid_office"])
def test_unmapped_agent_thinking_gives_no_rows_and_bumps_skip_count(sender):
    counts = Counter()
    assert _route(thinking(sender, "a thought", TUESDAY), counts) == []
    assert counts["unmapped"] == 1


# T16.3
def test_character_say_rows_for_present_characters_only():
    msg = say("engineer", "Pushed the fix.", TUESDAY, present=["engineer", "tester", "office_clock"],
              from_="tuber_3")
    rows = _route(msg)
    assert sorted(r["character_id"] for r in rows) == ["id-engineer", "id-tester"]
    assert {r["visibility"] for r in rows} == {"present"}
    assert {r["text"] for r in rows} == {"Pushed the fix."}
    assert {r["scene_id"] for r in rows} == {"standup"}


# T16.4
def test_present_character_and_addressees_union_is_deduplicated():
    msg = say("ceo", "Where are we on Corvane?", TUESDAY,
              present=["engineer", "tester", "engineer", "office_clock"],
              addressees=["tech_lead", "tester"])
    ids = [r["character_id"] for r in _route(msg)]
    assert sorted(ids) == ["id-ceo", "id-engineer", "id-tech_lead", "id-tester"]
    assert len(ids) == len(set(ids))
    event = scene("The lights flicker.", TUESDAY, present=["analyst", "analyst", "marketing"])
    assert sorted(r["character_id"] for r in _route(event)) == ["id-analyst", "id-marketing"]


# T16.5
@pytest.mark.parametrize("type_", ["status_update", "directive", "character_refresh", "phase_change"])
def test_type_outside_allowlist_is_dropped_before_payload_is_parsed(type_):
    counts = Counter()
    msg = {"id": "m1", "from": "tuber_0", "to": "broadcast", "type": type_,
           "payload": Explosive(), "timestamp": TUESDAY}
    assert _route(msg, counts) == []
    assert counts["not_allowed"] == 1
    assert set(ingest.ALLOWED_TYPES) == {"agent_thinking", "character_say", "scene_event"}
    narrowed = say("engineer", "hi", TUESDAY, present=["engineer"])
    assert _route(narrowed, types=("agent_thinking",)) == []


# T16.6
def test_week_and_day_come_from_body_timestamp_and_pre_epoch_is_skipped():
    # 23:30 NY on Saturday Oct 3 is 03:30Z on Sunday Oct 4: still week 1, day Oct 3.
    late = thinking("tuber_4", "One more run.", "2026-10-03T23:30:00-04:00")
    (row,) = _route(late)
    assert (row["loop_week"], row["loop_day"]) == (1, date(2026, 10, 3))
    assert row["ts"] == datetime(2026, 10, 4, 3, 30, tzinfo=timezone.utc)
    sunday = thinking("tuber_4", "New week.", "2026-10-04T04:00:00Z")   # 00:00 NY
    assert _route(sunday)[0]["loop_week"] == 2
    counts = Counter()
    assert _route(thinking("tuber_4", "Before the loop.", "2026-09-26T12:00:00-04:00"), counts) == []
    assert counts["pre_epoch"] == 1


# T16.7 (route level: malformed messages raise MalformedMessage)
@pytest.mark.parametrize("msg", [
    "not a dict",
    None,
    {"type": "agent_thinking", "from": "tuber_3", "payload": {"text": "x"}, "timestamp": TUESDAY},
    {"id": "m", "type": "agent_thinking", "from": "tuber_3", "payload": "x", "timestamp": TUESDAY},
    {"id": "m", "type": "agent_thinking", "from": "tuber_3", "payload": {"text": "x"}},
    {"id": "m", "type": "agent_thinking", "from": "tuber_3", "payload": {"text": "x"},
     "timestamp": "yesterday"},
    {"id": "m", "type": "agent_thinking", "from": "tuber_3", "payload": {"text": "x"},
     "timestamp": "2026-09-29T14:00:00"},                                    # naive
    {"id": "m", "type": "character_say", "from": "tuber_3", "timestamp": TUESDAY,
     "payload": {"character": "engineer", "present": "engineer", "text": "x"}},
    {"id": "m", "type": "agent_thinking", "from": "tuber_3", "payload": {"text": 5},
     "timestamp": TUESDAY},
], ids=["str", "none", "no-id", "payload-str", "no-ts", "bad-ts", "naive-ts",
        "present-not-list", "text-not-str"])
def test_malformed_message_raises_malformed_message(msg):
    with pytest.raises(ingest.MalformedMessage):
        _route(msg)
    assert issubclass(ingest.MalformedMessage, ValueError)


def _loop(messages, journal=None, insert=None, clock=None, **kwargs):
    journal = journal if journal is not None else []
    inserted = []

    def fake_insert(conn, rows):
        rows = list(rows)
        inserted.append(rows)
        return len(rows)

    consumer = FakeConsumer(messages, journal=journal,
                            on_poll=(lambda: clock.advance(0.25)) if clock else None)
    loop = ingest.IngestLoop(consumer, FakeConn(journal), CLOCK, agents=AGENTS, slugs=SLUGS,
                             insert_many=insert or fake_insert,
                             update_status=lambda conn, group, **kw: journal.append(("status", kw)),
                             monotonic=clock or FakeClock(), **kwargs)
    return loop, consumer, inserted


def _drain(loop, steps=40):
    for _ in range(steps):
        loop.step()
    loop.flush()


# T16.7 (loop level)
def test_malformed_message_is_logged_at_error_and_skipped_loop_continues(caplog):
    caplog.set_level(logging.DEBUG)
    good = [thinking("tuber_3", "first", TUESDAY), thinking("tuber_4", "last", TUESDAY)]
    batch = [good[0], "garbage", None, {"type": "character_say", "payload": 3}, good[1]]
    loop, consumer, inserted = _loop(batch)
    _drain(loop, steps=2)
    rows = [row for rows in inserted for row in rows]
    assert sorted(r["text"] for r in rows) == ["first", "last"]
    assert loop.counts["malformed"] == 3
    assert consumer.commits >= 1
    assert any(r.levelno >= logging.ERROR and r.name.startswith("character") for r in caplog.records)


# T16.8
def test_offsets_are_committed_only_after_the_db_commit():
    journal = []
    loop, consumer, _ = _loop([thinking("tuber_3", "ok", TUESDAY)], journal=journal)
    _drain(loop, steps=1)
    commits = [e for e in journal if e in ("db_commit", "kafka_commit")]
    assert commits == ["db_commit", "kafka_commit"]
    status = [e for e in journal if isinstance(e, tuple)]
    assert status and status[0][1]["messages_seen"] == 1 and status[0][1]["rows_written"] == 1

    journal = []

    def broken_insert(conn, rows):
        raise RuntimeError("database went away")

    loop, consumer, _ = _loop([thinking("tuber_3", "lost?", TUESDAY)], journal=journal,
                              insert=broken_insert)
    loop.step()
    with pytest.raises(RuntimeError):
        loop.flush()
    assert "kafka_commit" not in journal and consumer.commits == 0
    assert "db_rollback" in journal
    assert len(loop.pending) == 1      # kept, so a retry (or a restart + redelivery) loses nothing


# T16.9
@pytest.mark.integration
def test_redelivery_of_the_same_batch_inserts_zero_rows(pg_conn):
    engineer = seed_character(pg_conn, "engineer")
    tester = seed_character(pg_conn, "tester")
    seed_agents(pg_conn, {"char:engineer": engineer, "tuber_3": engineer, "tuber_4": tester})
    pg_conn.commit()
    agents, slugs = ingest.load_maps(pg_conn)
    assert agents["tuber_3"] == engineer and slugs == {"engineer": engineer, "tester": tester}
    batch = [thinking("tuber_3", "thought", TUESDAY),
             say("engineer", "Pushed.", TUESDAY, present=["engineer", "tester", "office_clock"]),
             scene("The standup ends.", TUESDAY, present=["tester"])]

    def run(messages):
        loop = ingest.IngestLoop(FakeConsumer(messages), pg_conn, CLOCK, agents=agents, slugs=slugs)
        loop.step()                      # 3 messages: under batch_max and under 2 s, no flush yet
        assert len(loop.pending) == 3
        return loop.flush()

    first = run(list(batch))
    assert first == 4 == count(pg_conn, "SELECT count(*) FROM experience_events")
    assert run(list(batch)) == 0
    assert count(pg_conn, "SELECT count(*) FROM experience_events") == 4
    status = events.ingest_status(pg_conn, "character-ingest")
    assert status["messages_seen"] == 6 and status["rows_written"] == 4


# T16.10
def test_batching_flushes_at_200_messages_or_2_seconds():
    clock = FakeClock()
    sizes, times = [], []

    def recording_insert(conn, rows):
        rows = list(rows)
        sizes.append(len(rows))
        times.append(clock.now)
        return len(rows)

    messages = [thinking("tuber_3", f"beat {i}", TUESDAY) for i in range(450)]
    loop, consumer, _ = _loop(messages, insert=recording_insert, clock=clock,
                              batch_max=200, batch_wait_s=2.0)
    for _ in range(40):
        loop.step()
    assert sizes == [200, 200, 50]
    assert times[:2] == [1.0, 2.0]          # full batches flush at once (4 polls of 50 each)
    assert times[2] == 4.25                 # the last 50 arrive at 2.25 and wait exactly 2 s
    assert consumer.commits == 3


# T16.11
def test_consumer_is_built_earliest_no_autocommit_group_character_ingest():
    seen = {}

    def factory(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return "consumer"

    cfg = config.load(env={})
    assert ingest.build_consumer(cfg.ingest, "kafka:9092", factory=factory) == "consumer"
    assert seen["args"] == ("vtuber.messages",)
    kwargs = seen["kwargs"]
    assert kwargs["group_id"] == "character-ingest"
    assert kwargs["auto_offset_reset"] == "earliest"
    assert kwargs["enable_auto_commit"] is False
    assert kwargs["bootstrap_servers"] == "kafka:9092"
    # a deserializer that raised would stall the partition: undecodable bytes -> None
    assert kwargs["value_deserializer"](b'{"type": "agent_thinking"}') == {"type": "agent_thinking"}
    assert kwargs["value_deserializer"](b"\xff not json") is None


# OB-41 + user decision 2026-09-28 (item 8): the Party Member is a full character.
# His own thoughts route as `self` like everyone's; the fixture day has no line
# of his (he has no lines at the moment), which is a fact of this fixture, not a rule.
@pytest.mark.parametrize("sender", ["char:party_member", "tuber_7"])
def test_party_member_is_a_full_character_his_own_thoughts_are_self_rows(sender):
    day = [thinking(OFFICE_SEATS[s], f"{s} thinks", TUESDAY) for s in OFFICE_SLUGS if s != "party_member"]
    day += [thinking(sender, "Note the time the Glass Box went quiet.", TUESDAY),
            say("ceo", "Standup. Everyone in.", TUESDAY, from_="tuber_0",
                present=list(OFFICE_SLUGS) + ["office_clock"]),
            scene("The Party Member files past the Glass Box.", TUESDAY,
                  present=["party_member", "office_manager", "office_clock"]),
            say("engineer", "Green build.", TUESDAY, present=["engineer", "tester"])]
    counts = Counter()
    rows = [row for msg in day for row in ingest.route(msg, AGENTS, SLUGS, CLOCK, counts=counts)]
    party = [r for r in rows if r["character_id"] == "id-party_member"]
    assert sorted((r["visibility"], r["msg_type"]) for r in party) == [
        ("present", "character_say"), ("present", "scene_event"), ("self", "agent_thinking")]
    (own,) = [r for r in party if r["visibility"] == "self"]
    assert own["from_agent"] == sender and own["text"] == "Note the time the Glass Box went quiet."
    assert counts["unmapped"] == 0
    assert all(r["character_id"] != "id-office_clock" for r in rows)
    selfs = sorted(r["character_id"] for r in rows if r["visibility"] == "self")
    assert selfs == sorted(SLUGS[s] for s in OFFICE_SLUGS)
    # this fixture has no character_say spoken BY him (not a rule: see the next test)
    assert not any(r["msg_type"] == "character_say" and r["payload"].get("character") == "party_member"
                   for r in rows)


# user decision 2026-09-28 (item 8): no "never speaks" rule in ingest; a line of his routes normally.
def test_a_line_spoken_by_the_party_member_routes_like_anyone_elses():
    msg = say("party_member", "Noted.", TUESDAY, from_="tuber_7",
              present=["party_member", "ceo", "office_clock"])
    rows = _route(msg)
    assert sorted(r["character_id"] for r in rows) == ["id-ceo", "id-party_member"]
    assert {r["visibility"] for r in rows} == {"present"}
