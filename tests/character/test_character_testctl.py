"""WP-21 tests for app/character/testctl.py: the test manipulation controls.

Frozen test list (playbook §4 WP-21, items 1-8; item 9, `revert`, and the
main.py registration are in test_character_testctl_cli.py). Plan §5.2, D-10.
Every command supports --dry-run, writes a character_jobs row
(job='testctl:<cmd>'), needs `--confirm <database name>` when it deletes
anything, and uses SET LOCAL character.allow_test_mutation = 'on' (the only
way past the fragment immutability triggers). `testctl.enabled: false` turns
the whole group off. Commands are dispatched by
`testctl.main(argv, cfg=, connect=, producer=)`, which runs each one through
the WP-06 framework (`jobs.run_job`); a two-word command ("fragment add",
"week wipe") has the job name "testctl:fragment-add" / "testctl:week-wipe".

Local choice (tracker phase 3b): T21.1 "run the reset" is done by
`_simulate_reset` below, which makes the writes plan §5 lists for
weekly-reset (fragment with lead-up and a link, archive, open W+1, the
reset_steps, week closed), so WP-21 does not depend on WP-20's job. The e2e
test (WP-25 item 8) undoes a real weekly-reset.
"""
import psycopg2
import psycopg2.extras
import pytest
from psycopg2.extensions import parse_dsn

from fakes import seed_character, write_config
from fakes_e2e import FakeProducer, count, snapshot
from pending import require

testctl = require("character.testctl", "app/character/testctl.py", wp="WP-21")
from character import config, db  # noqa: E402
from character.clock import LoopClock  # noqa: E402

pytestmark = pytest.mark.integration

AT_W2 = "2026-10-07T12:00:00-04:00"       # Wednesday of office week 2
STEPS = ("close_saturday", "select_fragments", "archive", "open_next", "refresh")


@pytest.fixture
def cfg():
    return config.load(env={})


@pytest.fixture
def dsn(pg):
    conn = psycopg2.connect(pg)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    return pg


@pytest.fixture
def connect(dsn):
    def _connect(cfg, role="main"):
        return psycopg2.connect(dsn)
    return _connect


@pytest.fixture
def dbname(dsn):
    return parse_dsn(dsn)["dbname"]


@pytest.fixture
def producer():
    return FakeProducer()


@pytest.fixture
def run(cfg, connect, producer):
    def _run(*argv, config_=None):
        return testctl.main(list(argv), cfg=config_ or cfg, connect=connect, producer=producer)
    return _run


def _exec(dsn, sql, params=()):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _week(conn, week):
    start, end = LoopClock(config.load(env={}).loop.epoch, "America/New_York").week_bounds(week)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO loop_weeks (campaign, week, starts_at, ends_at) VALUES "
                    "('ashiorid_office', %s, %s, %s) ON CONFLICT DO NOTHING", (week, start, end))


def _event(cur, mid, cid, week, day, text="Something happened.", visibility="present"):
    cur.execute("INSERT INTO experience_events (message_id, character_id, msg_type, from_agent, "
                "visibility, text, payload, ts, loop_week, loop_day) VALUES (%s, %s, "
                "'character_say', 'char:tech_lead', %s, %s, '{}', %s::date + time '12:00' "
                "AT TIME ZONE 'America/New_York', %s, %s)", (mid, cid, visibility, text, day, week, day))


def _knowledge(cur, cid, week, tag, archived=None):
    for n in ("a", "b"):
        cur.execute("INSERT INTO week_knowledge_nodes (id, character_id, loop_week, name, kind, "
                    "statement, archived_at_week) VALUES (%s, %s, %s, %s, 'fact', %s, %s)",
                    (f"n-{tag}-{n}", cid, week, f"knows-{tag}-{n}", f"I know {tag} {n}.", archived))
    cur.execute("INSERT INTO week_knowledge_edges (id, character_id, loop_week, src_node_id, "
                "dst_node_id, relation, archived_at_week) VALUES (%s, %s, %s, %s, %s, 'supports', %s)",
                (f"e-{tag}", cid, week, f"n-{tag}-a", f"n-{tag}-b", archived))
    cur.execute("INSERT INTO daily_summaries (character_id, loop_day, loop_week, summary, event_count) "
                "VALUES (%s, %s::date, %s, %s, 1)",
                (cid, "2026-09-30" if week == 1 else "2026-10-07", week,
                 psycopg2.extras.Json({"summary": f"I had a {tag} day."})))


def _fragment(cur, fid, cid, week, gist, beats=("I sat down.", "The screen went red.")):
    cur.execute("INSERT INTO memory_fragments (id, character_id, source_week, gist) VALUES "
                "(%s, %s, %s, %s)", (fid, cid, week, gist))
    for position, text in enumerate(beats):
        cur.execute("INSERT INTO fragment_lead_up (fragment_id, position, text) VALUES (%s, %s, %s)",
                    (fid, position, text))


@pytest.fixture
def world(dsn):
    """Office week 1 already reset (f-w1, archived nodes); week 2 open with memory."""
    conn = psycopg2.connect(dsn)
    try:
        ids = {slug: seed_character(conn, slug) for slug in ("engineer", "tester")}
        _week(conn, 1)
        _week(conn, 2)
        with conn.cursor() as cur:
            cur.execute("UPDATE loop_weeks SET status = 'closed', reset_steps = %s WHERE week = 1",
                        (psycopg2.extras.Json({s: {"completed_at": "2026-10-04T04:02:00+00:00"}
                                               for s in STEPS}),))
            for slug, cid in ids.items():
                _event(cur, f"w1-{slug}", cid, 1, "2026-09-30")
                _event(cur, f"w2-{slug}", cid, 2, "2026-10-07")
                _knowledge(cur, cid, 1, f"{slug}-w1", archived=1)
                _knowledge(cur, cid, 2, f"{slug}-w2")
            _fragment(cur, "f-w1", ids["engineer"], 1, "My stomach dropped at the red graph.")
            _fragment(cur, "f-old", ids["tester"], 1, "The suite flickered in the dark.")
            cur.execute("INSERT INTO fragment_unlocks (fragment_id, character_id, unlocked_week) "
                        "VALUES ('f-old', %s, 2)", (ids["tester"],))
        conn.commit()
    finally:
        conn.close()
    return ids


def _simulate_reset(dsn, ids, week):
    """The writes weekly-reset makes for `week` (plan §5), done by hand."""
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            _fragment(cur, f"f-w{week}", ids["engineer"], week, "The deploy bar froze and I laughed.")
            cur.execute("INSERT INTO fragment_links (from_fragment_id, to_fragment_id, relation) "
                        "VALUES (%s, 'f-w1', 'evokes')", (f"f-w{week}",))
            cur.execute("INSERT INTO fragment_recalls (id, fragment_id, character_id, loop_week, ts, "
                        "level, activation) VALUES ('r1', %s, %s, %s, now(), 'unease', 0.4)",
                        (f"f-w{week}", ids["engineer"], week + 1))
            for table in ("week_knowledge_nodes", "week_knowledge_edges"):
                cur.execute(f"UPDATE {table} SET archived_at_week = %s WHERE loop_week <= %s "
                            "AND archived_at_week IS NULL", (week, week))
            cur.execute("UPDATE loop_weeks SET status = 'closed', reset_steps = %s WHERE week = %s",
                        (psycopg2.extras.Json({s: {"completed_at": "x"} for s in STEPS}), week))
        _week(conn, week + 1)
        conn.commit()
    finally:
        conn.close()


def _job_rows(dsn, job=None):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT job, status, error FROM character_jobs "
                        + ("WHERE job = %s " if job else "") + "ORDER BY created_at", (job,) if job else ())
            return cur.fetchall()
    finally:
        conn.close()


# T21.1
def test_reset_undo_restores_the_state_before_the_reset(run, dsn, dbname, world):
    before = snapshot(dsn)
    _simulate_reset(dsn, world, 2)
    assert snapshot(dsn) != before
    assert run("reset-undo", "--week", "2", "--confirm", dbname) == 0
    assert snapshot(dsn) == before
    assert _job_rows(dsn, "testctl:reset-undo") == [("testctl:reset-undo", "completed", None)]
    # a next week that already has events is kept
    _simulate_reset(dsn, world, 2)
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        _event(cur, "w3-engineer", world["engineer"], 3, "2026-10-12")
    conn.commit()
    conn.close()
    assert run("reset-undo", "--week", "2", "--confirm", dbname) == 0
    assert count(dsn, "SELECT count(*) FROM loop_weeks WHERE week = 3") == 1
    assert count(dsn, "SELECT status FROM loop_weeks WHERE week = 2") == "open"
    assert count(dsn, "SELECT count(*) FROM memory_fragments WHERE source_week = 2") == 0


# T21.2
@pytest.mark.parametrize("argv", [["reset-undo", "--week", "1"], ["week", "wipe", "--week", "1"],
                                  ["fragment", "delete", "--id", "f-w1"],
                                  ["fragment", "lock", "--id", "f-old"]])
def test_delete_without_confirm_dbname_exits_1(run, dsn, dbname, world, argv):
    before = snapshot(dsn)
    assert run(*argv) == 1
    assert run(*argv, "--confirm", "character_profile_prod") == 1
    assert snapshot(dsn) == before
    rows = _job_rows(dsn)
    assert [row[1] for row in rows] == ["failed", "failed"]
    assert all("--confirm" in row[2] for row in rows)
    assert run(*argv, "--dry-run") == 0                  # a rehearsal deletes nothing
    assert snapshot(dsn) == before
    assert run(*argv, "--confirm", dbname) == 0
    assert snapshot(dsn) != before


# T21.3
def test_fragment_delete_works_only_through_the_bypass_and_trigger_still_blocks(run, dsn, dbname, world):
    with pytest.raises(psycopg2.Error, match="immutable"):
        _exec(dsn, "DELETE FROM memory_fragments WHERE id = 'f-w1'")
    assert run("fragment", "delete", "--id", "f-w1", "--confirm", dbname) == 0
    assert count(dsn, "SELECT count(*) FROM memory_fragments WHERE id = 'f-w1'") == 0
    assert count(dsn, "SELECT count(*) FROM fragment_lead_up WHERE fragment_id = 'f-w1'") == 0
    assert count(dsn, "SELECT count(*) FROM memory_fragments WHERE id = 'f-old'") == 1
    with pytest.raises(psycopg2.Error, match="immutable"):
        _exec(dsn, "DELETE FROM memory_fragments WHERE id = 'f-old'")
    with pytest.raises(psycopg2.Error, match="immutable"):
        _exec(dsn, "UPDATE memory_fragments SET gist = 'edited' WHERE id = 'f-old'")
    assert run("fragment", "delete", "--id", "f-w1", "--confirm", dbname) == 1   # no such fragment


# T21.4
def test_unlock_and_lock_toggle_the_unlock_row(run, dsn, dbname, world):
    unlocks = "SELECT count(*) FROM fragment_unlocks WHERE fragment_id = 'f-w1'"
    assert run("fragment", "unlock", "--id", "f-w1", "--at", AT_W2) == 0
    assert count(dsn, unlocks) == 1
    assert count(dsn, "SELECT unlocked_week FROM fragment_unlocks WHERE fragment_id = 'f-w1'") == 2
    assert run("fragment", "unlock", "--id", "f-w1", "--at", AT_W2) == 2          # already unlocked
    assert run("fragment", "lock", "--id", "f-w1", "--confirm", dbname) == 0
    assert count(dsn, unlocks) == 0
    assert run("fragment", "lock", "--id", "f-w1", "--confirm", dbname) == 2      # already dormant
    assert run("fragment", "unlock", "--id", "f-w1", "--at", AT_W2) == 0
    assert count(dsn, unlocks) == 1
    assert run("fragment", "unlock", "--id", "no-such-fragment") == 1
    with pytest.raises(psycopg2.Error, match="immutable"):
        _exec(dsn, "DELETE FROM fragment_unlocks WHERE fragment_id = 'f-w1'")


# T21.5
def test_seed_inserts_synthetic_events_with_week_and_day_from_the_clock(run, dsn, world, tmp_path):
    seed = tmp_path / "seed.yaml"
    seed.write_text(
        "events:\n"
        "  - {character: engineer, ts: '2026-10-03T23:30:00-04:00', text: 'Saturday night deploy.'}\n"
        "  - {character: tester, ts: '2026-10-04T00:10:00-04:00', text: 'Sunday, just after midnight.'}\n"
        "  - {character: engineer, ts: '2026-10-04T04:30:00+00:00', text: 'I should sleep.',\n"
        "     type: agent_thinking, from: 'char:engineer', visibility: self, message_id: think-1}\n",
        encoding="utf-8")
    assert run("seed", "--file", str(seed)) == 0
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("SELECT c.slug, e.loop_week, e.loop_day::text, e.msg_type, e.visibility, "
                    "e.from_agent, e.message_id, e.payload->>'text' FROM experience_events e "
                    "JOIN characters c ON c.id = e.character_id WHERE e.message_id NOT LIKE 'w_-%' "
                    "ORDER BY e.ts")
        rows = cur.fetchall()
    conn.close()
    assert [row[:3] for row in rows] == [("engineer", 1, "2026-10-03"), ("tester", 2, "2026-10-04"),
                                         ("engineer", 2, "2026-10-04")]
    assert rows[0][3:6] == ("character_say", "present", "testctl:seed")
    assert rows[0][7] == "Saturday night deploy."
    assert rows[2][3:7] == ("agent_thinking", "self", "char:engineer", "think-1")
    assert run("seed", "--file", str(seed)) == 0                                  # idempotent ids
    assert count(dsn, "SELECT count(*) FROM experience_events") == 7
    bad = tmp_path / "bad.yaml"
    bad.write_text("events:\n  - {character: intern, ts: '2026-10-05T09:00:00-04:00', text: x}\n"
                   "  - {character: engineer, ts: '2026-10-05T09:00:00-04:00', text: y}\n",
                   encoding="utf-8")
    assert run("seed", "--file", str(bad)) == 1
    naive = tmp_path / "naive.yaml"
    naive.write_text("events:\n  - {character: engineer, ts: '2026-10-05T09:00:00', text: z}\n",
                     encoding="utf-8")
    assert run("seed", "--file", str(naive)) == 1
    assert count(dsn, "SELECT count(*) FROM experience_events") == 7


# T21.6
def test_week_wipe_removes_that_weeks_rows_only(run, dsn, dbname, world):
    def counts(week, slug):
        cid = world[slug]
        return tuple(count(dsn, f"SELECT count(*) FROM {table} WHERE character_id = %s AND loop_week = %s",
                           (cid, week)) for table in ("experience_events", "daily_summaries",
                                                      "week_knowledge_nodes", "week_knowledge_edges"))

    assert counts(1, "engineer") == counts(1, "tester") == counts(2, "engineer") == (1, 1, 2, 1)
    assert run("week", "wipe", "--week", "1", "--character", "engineer", "--confirm", dbname) == 0
    assert counts(1, "engineer") == (0, 0, 0, 0)
    assert counts(1, "tester") == (1, 1, 2, 1)
    assert counts(2, "engineer") == counts(2, "tester") == (1, 1, 2, 1)
    assert run("week", "wipe", "--week", "1", "--confirm", dbname) == 0
    assert counts(1, "tester") == (0, 0, 0, 0)
    assert counts(2, "engineer") == counts(2, "tester") == (1, 1, 2, 1)
    assert count(dsn, "SELECT count(*) FROM memory_fragments") == 2               # fragments stay
    assert count(dsn, "SELECT count(*) FROM loop_weeks") == 2


def _all_commands(dbname, seed_file):
    return [
        ("reset-undo", ["reset-undo", "--week", "1", "--confirm", dbname]),
        ("fragment-add", ["fragment", "add", "--character", "engineer",
                          "--gist", "I remember burnt toast in the kitchen.", "--at", AT_W2]),
        ("fragment-delete", ["fragment", "delete", "--id", "f-w1", "--confirm", dbname]),
        ("fragment-unlock", ["fragment", "unlock", "--id", "f-w1", "--at", AT_W2]),
        ("fragment-lock", ["fragment", "lock", "--id", "f-old", "--confirm", dbname]),
        ("week-wipe", ["week", "wipe", "--week", "1", "--confirm", dbname]),
        ("seed", ["seed", "--file", str(seed_file)]),
        ("clock", ["clock", "--show", "--at", AT_W2]),
        ("refresh", ["refresh", "--character", "engineer", "--at", AT_W2]),
    ]


@pytest.fixture
def seed_file(tmp_path):
    path = tmp_path / "seed.yaml"
    path.write_text("events:\n  - {character: tester, ts: '2026-10-06T09:00:00-04:00', text: Hi.}\n",
                    encoding="utf-8")
    return path


# T21.7
@pytest.mark.parametrize("index", range(9))
def test_testctl_disabled_makes_every_subcommand_exit_1(run, dsn, dbname, world, seed_file, tmp_path,
                                                         producer, index):
    off = config.load(write_config(tmp_path, lambda d: d["character"]["testctl"].update(enabled=False)),
                      env={})
    name, argv = _all_commands(dbname, seed_file)[index]
    before = snapshot(dsn)
    assert run(*argv, config_=off) == 1
    assert snapshot(dsn) == before and producer.sent == []
    assert [row[:2] for row in _job_rows(dsn)] == [(f"testctl:{name}", "failed")]
    assert "disabled" in _job_rows(dsn)[0][2]


# T21.8
@pytest.mark.parametrize("index", range(9))
def test_every_subcommand_writes_a_testctl_jobs_row_except_dry_run(run, dsn, dbname, world, seed_file,
                                                                   producer, index):
    name, argv = _all_commands(dbname, seed_file)[index]
    before = snapshot(dsn)
    assert run(*argv, "--dry-run") == 0
    assert snapshot(dsn) == before
    assert _job_rows(dsn) == [] and producer.sent == []
    assert run(*argv) == 0
    assert [row[:2] for row in _job_rows(dsn)] == [(f"testctl:{name}", "completed")]
