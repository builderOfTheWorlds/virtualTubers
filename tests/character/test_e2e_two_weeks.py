"""WP-25: the end-to-end simulated fortnight (playbook §4 WP-25; plan §13 phase 3 gate).

pgserver, canned LLM replies, a fake embedder whose vectors make a replay
align, and `--at` driving time from the office epoch Sunday 2026-09-27.
The scenario is tests/character/fixtures/e2e_office_fortnight.yaml. This test
is the integration proof; it has no module of its own.

OB-41 test corrections (tracker .claude/prompts/character_v4_tracker_phase3b.md):
- "initialize for the trio, with canned baselines" -> `initialize` for the
  office cast; the canned baselines are campaigns/ashiorid_office/profiles
  (a tmp copy of the pack). The fortnight follows engineer, tester, analyst
  and the Party Member. He is a full character who just has no lines at the
  moment (user decision 2026-09-28 (item 8)): the fixture has no line or
  thought of his, so his rows are all `present` HERE, as a fact of the
  fixture, not a rule (a think:party_member beat would give him a self row).
- "one harry fragment" -> one dormant fragment per retaining character (all 8
  retain, OB-41); the worked example is the engineer's.
- Item 9 "a re-run of any completed job exits 2": initialize and weekly-reset
  exit 2. daily-maintenance's compaction step always runs (plan §5: "naturally
  idempotent"), and Phase 3a's frozen T19.5 re-run exits 0, so its re-run is
  held to "no LLM call and no change" with exit 0 or 2.

CROSS-PHASE ASSUMPTIONS (reconcile with Phase 2 / 3a before freezing; listed
in the tracker file too):
A1 jobs run as jobs.run_job(job, argv, cfg=, connect=) (WP-06).
A2 character.jobs_initialize.InitializeJob() takes `--pack DIR` (Phase 2
   test_character_initialize.py) and loads all 8 profiles, seeds char:<slug>
   and tuber_N agents for all 8, the Party Member included (Phase 2 T14.13;
   user decision 2026-09-28 (item 8)) and opens week 1; a second run exits 2;
   no LLM call.
A3 character.jobs_daily.DailyMaintenanceJob(complete=, messages_connect=,
   monotonic=, sleep=, poll_s=) with `complete(system, user, shape)`; compaction
   is replaced through jobs_daily.compaction.run / .ingest_lag (Phase 3a
   test_character_jobs_daily.py); ingest counts as caught up when
   ingest_status.last_message_ts is past midnight.
A4 character.jobs_weekly.WeeklyResetJob(complete_summary=, complete_fragment=,
   embed=, producer=); at Sunday 00:02 of week N+1 it resets week N; the
   fragment prompt lists moments as "<event_id>: <text>" and the reply's
   anchor_event_id picks the moment; the lead-up is the lead_up_beats events
   before it.
A5 character.ingest.route(msg, agents, slugs, clock) -> rows for
   store.events.insert_many, with slugs = {slug: character_id} (WP-16).
A6 node names "remembers-<token>-note" pass the default node_names.check.
"""
import shutil
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg2
import pytest
from psycopg2.extensions import parse_dsn

from fakes import FakeClock, write_config
from fakes_e2e import (FORBIDDEN_BRIEF_RE, LOOP_TABLES, OFFICE_PACK, CannedCharacterLLM,
                       FakeJudge, FakeProducer, NullConn, TopicEmbed, bus_message, count,
                       load_yaml_fixture, snapshot)
from pending import require, skip_if_pending

for _target in ("app/character/jobs_initialize.py", "app/character/generator/office_profiles.py",
                "app/character/ingest.py", "app/character/summaries.py", "app/character/jobs_daily.py",
                "app/character/fragments.py", "app/character/jobs_weekly.py", "app/character/brief.py",
                "app/character/recall.py", "app/character/recall_harness.py", "app/character/live.py",
                "app/character/testctl.py", "app/character/node_names.py",
                "app/character/embeddings.py", "app/character/bus_contracts.py"):
    skip_if_pending(_target, wp="WP-25")
jobs = require("character.jobs", "app/character/jobs.py", wp="WP-25")
from character import (brief, config, db, ingest, jobs_daily, jobs_initialize,  # noqa: E402
                       jobs_weekly, live, recall, recall_harness, testctl)
from character.clock import LoopClock  # noqa: E402
from character.store import characters, events  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.slow]

NY = ZoneInfo("America/New_York")
FIX = load_yaml_fixture("e2e_office_fortnight.yaml")
WEEK1_DAYS = [date(2026, 9, 27) + timedelta(days=i) for i in range(7)]
WEEK2_DAYS = [date(2026, 10, 4) + timedelta(days=i) for i in range(7)]
TOKENS = {day: spec["token"] for day, spec in FIX["days"].items()}
LEAD_UP = [beat["t"] for beat in FIX["days"]["2026-09-30"]["beats"][:8]]
TABLES = LOOP_TABLES + ("characters", "character_baselines", "character_backstories",
                        "character_agents")


def _at(day, hh=0, mm=0, ss=30):
    return datetime.combine(day, time(hh, mm, ss), tzinfo=NY).isoformat()


def _embed():
    embed = TopicEmbed()
    for i, text in enumerate(LEAD_UP):
        embed.assign(text, f"lead-{i}")
    embed.assign(FIX["gist"], "gist")
    replay = FIX["days"]["2026-10-07"]["beats"]
    for beat, lead in zip(replay, FIX["paraphrase_of"]["2026-10-07"]):
        if lead is not None:
            embed.assign(beat["t"], f"lead-{lead}")
    return embed


def _messages(day_key):
    out = []
    for n, beat in enumerate(FIX["days"][day_key]["beats"]):
        day = date.fromisoformat(day_key)
        ts = datetime.combine(day, time(9, 0), tzinfo=NY) + timedelta(minutes=10 * n)
        mid = beat.get("id") or f"e2e-{day_key}-{n}"
        speaker = beat["s"]
        if speaker == "clock":
            payload = {"campaign": "ashiorid_office", "scene_id": f"day-{day_key}", "kind": "narration",
                       "character": None, "present": FIX["present"], "text": beat["t"]}
            out.append(bus_message("scene_event", "office_clock", payload, ts, mid))
        elif speaker.startswith("think:"):
            out.append(bus_message("agent_thinking", f"char:{speaker[6:]}", {"text": beat["t"]}, ts, mid))
        else:
            payload = {"campaign": "ashiorid_office", "scene_id": f"day-{day_key}", "character": speaker,
                       "addressees": [], "present": FIX["present"], "text": beat["t"]}
            out.append(bus_message("character_say", f"char:{speaker}", payload, ts, mid))
    return out


class Fortnight:
    def __init__(self, dsn, cfg, pack):
        self.dsn, self.cfg, self.pack = dsn, cfg, pack
        self.clock = LoopClock(cfg.loop.epoch, cfg.timezone)
        self.embed = _embed()
        self.llm = CannedCharacterLLM(FIX["gist"], FIX["hooks"], anchor_ids=[FIX["moment_id"]],
                                      tokens=list(TOKENS.values()))
        self.producer = FakeProducer()
        self.engine = None
        self.recalls = []

    def connect(self, cfg=None, role="main"):
        return psycopg2.connect(self.dsn)

    def run(self, job, *argv):
        return jobs.run_job(job, list(argv), cfg=self.cfg, connect=self.connect)

    def initialize(self, at):
        return self.run(jobs_initialize.InitializeJob(), "--pack", str(self.pack), "--at", at)

    def daily(self, day):
        wait = FakeClock()
        job = jobs_daily.DailyMaintenanceJob(complete=self.llm.summary,
                                             messages_connect=lambda cfg: NullConn(),
                                             monotonic=wait, sleep=wait.sleep, poll_s=15)
        return self.run(job, "--at", _at(day + timedelta(days=1)))

    def weekly(self, sunday):
        job = jobs_weekly.WeeklyResetJob(complete_summary=self.llm.summary,
                                         complete_fragment=self.llm.fragment,
                                         embed=self.embed, producer=self.producer)
        return self.run(job, "--at", _at(sunday, 0, 2, 0))

    def feed(self, day):
        """Route the day's messages like ingest does; feed the engineer's beats to recall."""
        key = day.isoformat()
        conn = self.connect()
        try:
            agents = characters.agents_map(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT slug, id FROM characters")
                slugs = dict(cur.fetchall())
            for msg in _messages(key) if key in FIX["days"] else []:
                rows = ingest.route(msg, agents, slugs, self.clock)
                events.insert_many(conn, rows)
                conn.commit()
                if self.engine is not None and any(r["character_id"] == slugs["engineer"] for r in rows):
                    for event in self.engine.observe(msg["payload"]["text"], beat_id=msg["id"]):
                        live.record_recall_event(conn, character_id=slugs["engineer"],
                                                 week=self.clock.position(day_start(day))[0],
                                                 event=event, ts=datetime.fromisoformat(msg["timestamp"]))
                        conn.commit()
                        self.recalls.append(event)
            events.update_ingest_status(conn, self.cfg.ingest.consumer_group,
                                        last_message_ts=day_start(day + timedelta(days=1)) + timedelta(seconds=1))
            conn.commit()
        finally:
            conn.close()

    def brief_text(self, slug, week):
        conn = self.connect()
        try:
            return brief.render(brief.load_parts(conn, slug, week, pack_dir=self.pack), max_chars=100_000)
        finally:
            conn.close()

    def id_of(self, slug):
        return count(self.dsn, "SELECT id FROM characters WHERE slug = %s", (slug,))


def day_start(day):
    return datetime.combine(day, time(0, 0), tzinfo=NY)


@pytest.fixture
def fortnight(pg, tmp_path, monkeypatch):
    conn = psycopg2.connect(pg)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    pack = tmp_path / "pack"
    shutil.copytree(OFFICE_PACK, pack)
    cfg = config.load(write_config(tmp_path, lambda d: d["character"].update(pack=str(pack))), env={})
    monkeypatch.setattr(jobs_daily.compaction, "run", lambda conn, ccfg, **kw: {
        "status": "done", "reason": "", "dry_run": kw.get("dry_run", False), "noisy_deleted": 0,
        "rows_moved": 0, "partitions_moved": [], "default_rows_moved": 0})
    monkeypatch.setattr(jobs_daily.compaction, "ingest_lag", lambda *a, **kw: 0)
    return Fortnight(pg, cfg, pack)


def _tokens_in(text, days):
    return [TOKENS[d.isoformat()] for d in days if d.isoformat() in TOKENS and TOKENS[d.isoformat()] in text]


def test_simulated_fortnight(fortnight):
    f = fortnight
    # 1. initialize the office cast
    assert f.initialize(_at(WEEK1_DAYS[0], 0, 5, 0)) == 0
    assert count(f.dsn, "SELECT count(*) FROM characters") == 8
    assert count(f.dsn, "SELECT status FROM loop_weeks WHERE week = 1") == "open"
    eng = f.id_of("engineer")

    # 2. week 1: events through route(), daily-maintenance at each midnight
    for day in WEEK1_DAYS:
        f.feed(day)
        assert f.daily(day) == 0
    assert set(_tokens_in(f.brief_text("engineer", 1), WEEK1_DAYS)) == \
        {"kestrel", "heron", "osprey", "plover", "curlew"}
    pm = f.id_of("party_member")
    assert count(f.dsn, "SELECT count(*) FROM experience_events WHERE character_id = %s", (pm,)) > 0
    # his self rows == his thoughts in the fixture (none today): a fixture fact, not a rule (item 8)
    own_thoughts = sum(beat["s"] == "think:party_member"
                       for spec in FIX["days"].values() for beat in spec["beats"])
    assert count(f.dsn, "SELECT count(*) FROM experience_events WHERE character_id = %s "
                        "AND visibility = 'self'", (pm,)) == own_thoughts
    assert count(f.dsn, "SELECT count(*) FROM character_agents WHERE character_id = %s", (pm,)) >= 1

    # 3. weekly-reset at Sunday 00:00 NY: dormant fragments, week 1 archived
    assert f.weekly(WEEK2_DAYS[0]) == 0
    assert count(f.dsn, "SELECT count(*) FROM memory_fragments WHERE character_id = %s "
                        "AND source_week = 1", (eng,)) == 1
    fid = count(f.dsn, "SELECT id FROM memory_fragments WHERE character_id = %s", (eng,))
    assert count(f.dsn, "SELECT count(*) FROM fragment_unlocks") == 0
    assert count(f.dsn, "SELECT string_agg(text, '|' ORDER BY position) FROM fragment_lead_up "
                        "WHERE fragment_id = %s", (fid,)) == "|".join(LEAD_UP)
    for slug in ("tester", "analyst", "party_member"):
        assert count(f.dsn, "SELECT count(*) FROM memory_fragments WHERE character_id = %s",
                     (f.id_of(slug),)) == 1
    assert count(f.dsn, "SELECT count(*) FROM week_knowledge_nodes WHERE loop_week = 1 "
                        "AND archived_at_week IS DISTINCT FROM 1") == 0
    assert count(f.dsn, "SELECT status FROM loop_weeks WHERE week = 2") == "open"
    assert [m["payload"]["reason"] for m in f.producer.sent] == ["weekly_reset"]

    # 4. the week-2 brief has no week-1 knowledge and no fragment
    week2 = f.brief_text("engineer", 2)
    assert _tokens_in(week2, WEEK1_DAYS) == [] and FIX["gist"] not in week2
    assert FORBIDDEN_BRIEF_RE.search(week2) is None

    # 5. week 2 replays the engineer's lead-up; recall surfaces it; the judge says yes
    conn = f.connect()
    try:
        candidates = recall_harness.load_candidates(conn, eng)
        aliases = recall_harness.load_aliases(conn, f.cfg.campaign)
    finally:
        conn.close()
    assert [c.fragment_id for c in candidates] == [fid]
    judge = FakeJudge(verdicts=[True])
    f.engine = recall.RecallEngine(candidates, f.embed, recall.RecallParams.from_config(f.cfg.recall),
                                   judge=judge, aliases=aliases)
    for day in WEEK2_DAYS:
        f.feed(day)
        assert f.daily(day) == 0
    assert [e.level for e in f.recalls if e.level == "surface"] == ["surface"]
    assert len(judge.calls) == 1
    assert count(f.dsn, "SELECT unlocked_week FROM fragment_unlocks WHERE fragment_id = %s", (fid,)) == 2
    assert count(f.dsn, "SELECT count(*) FROM fragment_recalls WHERE fragment_id = %s "
                        "AND level = 'surface' AND judge_verdict", (fid,)) == 1

    # 6. the brief now carries it as a feeling
    feelings = f.brief_text("engineer", 2).split("## Feelings you carry", 1)[1]
    assert FIX["gist"] in feelings.split("\n## ", 1)[0]

    # 7. weekly-reset week 2; the week-3 brief still carries the feeling
    after_week2 = snapshot(f.dsn, TABLES)
    assert f.weekly(date(2026, 10, 11)) == 0
    week3 = f.brief_text("engineer", 3)
    assert FIX["gist"] in week3
    assert _tokens_in(week3, WEEK1_DAYS + WEEK2_DAYS) == []
    assert FORBIDDEN_BRIEF_RE.search(week3) is None
    silent = f.brief_text("party_member", 3)
    assert "You never speak. You only observe." in silent and FORBIDDEN_BRIEF_RE.search(silent) is None

    # 8. testctl reset-undo --week 2 restores the state after week 2 exactly
    dbname = parse_dsn(f.dsn)["dbname"]
    assert testctl.main(["reset-undo", "--week", "2", "--confirm", dbname], cfg=f.cfg,
                        connect=f.connect, producer=FakeProducer()) == 0
    assert snapshot(f.dsn, TABLES) == after_week2

    # 9. a re-run of a completed job changes nothing
    before, llm_calls = snapshot(f.dsn, TABLES), len(f.llm.calls)
    assert f.initialize(_at(WEEK1_DAYS[0], 0, 5, 0)) == 2
    assert f.weekly(WEEK2_DAYS[0]) == 2
    assert f.daily(WEEK1_DAYS[3]) in (0, 2)
    assert snapshot(f.dsn, TABLES) == before
    assert len(f.llm.calls) == llm_calls
