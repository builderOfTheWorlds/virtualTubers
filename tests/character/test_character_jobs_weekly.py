"""WP-20 tests for app/character/jobs_weekly.py: the `weekly-reset` job.

Frozen test list (playbook §4 WP-20, items 1, 2 and 6-10; items 3-5 are in
test_character_fragments.py). Plan §5, weekly-reset at Sunday 00:00 NY, keyed
by loop_weeks(campaign, week).reset_steps, for the week W that just ended:
  1. close_saturday   make sure Saturday's summaries exist
  2. select_fragments fragments for retains_fragments characters
  3. archive          archived_at_week = W on week-W nodes and edges
  4. open_next        insert week W+1, status open
  5. refresh          publish character_refresh (reset.refresh_mode push|none)
Each step runs under SELECT ... FOR UPDATE on the week row (store.weeks.run_step)
and a re-run skips completed steps. Before Sunday 00:00 NY it is "not due"
(exit 2) unless --force. --dry-run writes and publishes nothing.

Target week (tracker local choice P3a-L4): W = the clock week of --at minus 1;
with --force, W = the clock week of --at itself.

OB-41 adaptations (test corrections, cited: .claude/prompts/ashiorid_office_build_plan.md
OB-41): the office cast (engineer, tester) and epoch 2026-09-27 (week 1 =
Sep 27..Oct 3, so the reset for week 1 runs at Sunday 2026-10-04 00:00 NY);
the synthetic `visitor` fixture row has retains_fragments false.
"""
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest

from fakes import seed_character, write_config
from fakes_runtime import (FakeEmbed, FakeLLM, FakeProducer, connect_to, count,  # noqa: F401
                           migrated_dsn, seed_event)
from pending import require, skip_if_pending

jobs_weekly = require("character.jobs_weekly", "app/character/jobs_weekly.py", wp="WP-20")
skip_if_pending("app/character/prompts/fragment.md", wp="WP-11")
from character import config, jobs  # noqa: E402
from character.clock import LoopClock  # noqa: E402
from character.store import knowledge, weeks  # noqa: E402

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
RESET_AT = "2026-10-04T00:02:00-04:00"          # Sunday 00:02 NY: week 2 starts, W = 1
SATURDAY_LATE = "2026-10-03T23:00:00-04:00"     # still week 1: not due
STEPS = ["close_saturday", "select_fragments", "archive", "open_next", "refresh"]
CAMPAIGN = "ashiorid_office"


@pytest.fixture
def cfg():
    return config.load(env={})


@pytest.fixture
def clock(cfg):
    return LoopClock(cfg.loop.epoch, cfg.timezone)


def anchor_reply(user):
    line = next(l for l in user.splitlines() if "ANCHOR" in l)
    return {"anchor_event_id": line.split(":", 1)[0].strip(),
            "gist": "The red graph, and I could not breathe.",
            "hooks": {"entities": ["tech-lead"], "places": ["Glass Box"], "objects": ["graph"],
                      "tone": "dread"}}


SUMMARY = {"summary": "I spent Saturday alone with the pager.",
           "nodes": [{"name": "knows-pager-was-quiet", "kind": "fact",
                      "statement": "I know the pager stayed quiet."}]}


@pytest.fixture
def office(migrated_dsn, clock):
    """engineer + tester (retain fragments) and visitor (does not); week-1 events and nodes."""
    conn = psycopg2.connect(migrated_dsn)
    ids = {}
    try:
        for slug, retains in (("engineer", True), ("tester", True), ("visitor", False)):
            ids[slug] = seed_character(conn, slug, retains_fragments=retains)
            base = datetime(2026, 9, 30, 14, tzinfo=timezone.utc)
            for i in range(10):
                ts = base + timedelta(hours=i)
                seed_event(conn, ids[slug], text=("ANCHOR " if i == 9 else "") + f"{slug} beat {i}",
                           ts=ts, loop_week=1, loop_day=clock.position(ts)[1])
            knowledge.insert_node(conn, character_id=ids[slug], loop_week=1, name="knows-week-one-fact",
                                  kind="fact", statement="I know a week-one fact.")
        saturday = datetime(2026, 10, 3, 16, tzinfo=timezone.utc)
        seed_event(conn, ids["engineer"], text="Saturday on call", ts=saturday, loop_week=1,
                   loop_day=clock.position(saturday)[1])
        conn.commit()
    finally:
        conn.close()
    return ids


def _job(producer=None, summary=None, fragment=None):
    return jobs_weekly.WeeklyResetJob(complete_summary=summary or FakeLLM(SUMMARY),
                                      complete_fragment=fragment or FakeLLM(anchor_reply),
                                      embed=FakeEmbed(), producer=producer or FakeProducer(),
                                      check_name=lambda name: [])


def _run(job, cfg, dsn, *extra, at=RESET_AT):
    return jobs.run_job(job, ["--at", at, *extra], cfg=cfg, connect=connect_to(dsn))


def _steps(dsn, week=1):
    conn = psycopg2.connect(dsn)
    try:
        row = weeks.get_week(conn, CAMPAIGN, week)
        return None if row is None else sorted(row["reset_steps"])
    finally:
        conn.close()


def _fragments(dsn):
    return count(dsn, "SELECT count(*) FROM memory_fragments")


# T20.1
def test_steps_run_in_order(cfg, migrated_dsn, office, monkeypatch):
    order = []
    real = weeks.run_step

    def spy(conn, campaign, week, step, fn):
        order.append((campaign, week, step))
        return real(conn, campaign, week, step, fn)

    monkeypatch.setattr(jobs_weekly.weeks, "run_step", spy)
    summary = FakeLLM(SUMMARY)
    job = _job(summary=summary)
    assert job.name == "weekly-reset" and list(jobs_weekly.STEPS) == STEPS
    assert _run(job, cfg, migrated_dsn) == jobs.EXIT_OK
    assert order == [(CAMPAIGN, 1, step) for step in STEPS]
    assert _steps(migrated_dsn) == sorted(STEPS)
    # close_saturday: every character now has a Saturday summary; only the engineer had events
    assert count(migrated_dsn, "SELECT count(*) FROM daily_summaries WHERE loop_day = '2026-10-03'") == 3
    assert len(summary.calls) == 1


# T20.2
def test_crash_in_archive_then_rerun_skips_steps_1_2_and_finishes(cfg, migrated_dsn, office,
                                                                  monkeypatch):
    real_archive = knowledge.archive_week

    def crash(conn, week):
        raise RuntimeError("injected crash in archive")

    monkeypatch.setattr(jobs_weekly.knowledge, "archive_week", crash)
    fragment_llm = FakeLLM(anchor_reply)
    assert _run(_job(fragment=fragment_llm), cfg, migrated_dsn) == jobs.EXIT_FAILED
    assert _steps(migrated_dsn) == ["close_saturday", "select_fragments"]
    assert _fragments(migrated_dsn) == 2 and len(fragment_llm.calls) == 2

    monkeypatch.setattr(jobs_weekly.knowledge, "archive_week", real_archive)
    again = FakeLLM(anchor_reply)
    summary = FakeLLM(SUMMARY)
    assert _run(_job(fragment=again, summary=summary), cfg, migrated_dsn) == jobs.EXIT_OK
    assert again.calls == [] and summary.calls == []       # steps 1-2 were skipped
    assert _steps(migrated_dsn) == sorted(STEPS)
    assert _fragments(migrated_dsn) == 2
    # and a third run has nothing to do (plan §5 exit 2)
    assert _run(_job(), cfg, migrated_dsn) == jobs.EXIT_NOTHING


# T20.6
def test_archive_hides_week_w_nodes_from_the_next_week(cfg, migrated_dsn, office):
    assert _run(_job(), cfg, migrated_dsn) == 0
    conn = psycopg2.connect(migrated_dsn)
    try:
        for character_id in office.values():
            assert knowledge.current_nodes(conn, character_id, 2) == []
    finally:
        conn.close()
    assert count(migrated_dsn, "SELECT count(*) FROM week_knowledge_nodes "
                               "WHERE loop_week = 1 AND archived_at_week = 1") >= 3
    assert count(migrated_dsn, "SELECT count(*) FROM week_knowledge_nodes "
                               "WHERE archived_at_week IS NULL") == 0


# T20.7
def test_week_w_plus_1_is_open_with_bounds_from_the_clock(cfg, migrated_dsn, office, clock):
    assert _run(_job(), cfg, migrated_dsn) == 0
    conn = psycopg2.connect(migrated_dsn)
    try:
        nxt = weeks.get_week(conn, CAMPAIGN, 2)
        closed = weeks.get_week(conn, CAMPAIGN, 1)
    finally:
        conn.close()
    assert nxt["status"] == "open"
    assert (nxt["starts_at"], nxt["ends_at"]) == clock.week_bounds(2)
    assert closed["status"] == "closed"


# T20.8
def test_refresh_publishes_one_character_refresh(migrated_dsn, office):
    cfg = config.load(env={})
    producer = FakeProducer()
    assert _run(_job(producer=producer), cfg, migrated_dsn) == 0
    (msg,) = producer.sent
    assert msg["type"] == "character_refresh" and msg["from"] == "character-updater"
    assert msg["to"] == "broadcast"
    assert msg["payload"] == {"campaign": CAMPAIGN, "week": 2, "characters": ["*"],
                              "reason": "weekly_reset"}


# T20.8
def test_refresh_mode_none_publishes_nothing(migrated_dsn, office, tmp_path):
    path = write_config(tmp_path, lambda doc: doc["character"]["reset"].update(refresh_mode="none"))
    cfg = config.load(path, env={})
    producer = FakeProducer()
    assert _run(_job(producer=producer), cfg, migrated_dsn) == 0
    assert producer.sent == []
    assert _steps(migrated_dsn) == sorted(STEPS)            # the step still completes


# T20.9
def test_before_sunday_midnight_is_not_due_unless_force(cfg, migrated_dsn, office):
    producer = FakeProducer()
    assert _run(_job(producer=producer), cfg, migrated_dsn, at=SATURDAY_LATE) == jobs.EXIT_NOTHING
    assert _steps(migrated_dsn) is None and _fragments(migrated_dsn) == 0
    assert producer.sent == []
    assert _run(_job(producer=producer), cfg, migrated_dsn, "--force", at=SATURDAY_LATE) == 0
    assert _steps(migrated_dsn) == sorted(STEPS)            # --force resets the current week (1)
    assert len(producer.sent) == 1


# T20.10
def test_dry_run_writes_nothing_and_publishes_nothing(cfg, migrated_dsn, office, capsys):
    producer, summary, fragment = FakeProducer(), FakeLLM(), FakeLLM()
    before = {t: count(migrated_dsn, f"SELECT count(*) FROM {t}")
              for t in ("loop_weeks", "daily_summaries", "memory_fragments", "character_jobs",
                        "week_knowledge_nodes")}
    code = _run(_job(producer=producer, summary=summary, fragment=fragment), cfg, migrated_dsn,
                "--dry-run")
    assert code == jobs.EXIT_OK
    after = {t: count(migrated_dsn, f"SELECT count(*) FROM {t}") for t in before}
    assert after == before
    assert count(migrated_dsn, "SELECT count(*) FROM week_knowledge_nodes "
                               "WHERE archived_at_week IS NOT NULL") == 0
    assert producer.sent == [] and summary.calls == [] and fragment.calls == []
    out = capsys.readouterr().out
    assert all(step in out for step in STEPS)


# registration (playbook WP-06: "main.py registers every job")
def test_main_registers_weekly_reset():
    main = REPO_ROOT / "services" / "character-updater" / "main.py"
    result = subprocess.run([sys.executable, str(main), "weekly-reset", "--help"],
                            capture_output=True, text=True, timeout=120, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert "--force" in result.stdout and "--dry-run" in result.stdout
