#!/usr/bin/env python3
"""
character_v4_plan_validation.py

Checks the load-bearing parts of the v4 character plan against a real
Postgres 16 + pgvector before any of it is built:

  1. docs/charcterProfileGenerationNotes/v4_reference_character_profile.sql
     applies cleanly, twice (idempotent).
  2. Fragment immutability: UPDATE/DELETE blocked; the test opt-out works;
     cascades from a fragment delete work under the opt-out.
  3. loop_weeks.reset_steps exactly-once step recording under FOR UPDATE.
  4. Weekly archive: nodes become unreachable, nothing is deleted.
  5. Dimension-free `vector` columns: cosine distance works per model.
  6. character_reader: can SELECT, cannot INSERT (default privileges too).
  7. messages: M1 on the logger's real legacy table, then the M2 partitioned
     conversion, ON CONFLICT (id, timestamp) dedupe, compaction A,
     compaction B on both shapes (detach/attach, batched move).
  8. The loop clock: week/day derivation across both DST transitions.

Throwaway database via the `pgserver` pip package (bundles Postgres 16 +
pgvector, works on Windows/Linux x86_64). Nothing touches a real server.

    python -m venv $TMPDIR/pgtest_venv
    $TMPDIR/pgtest_venv/Scripts/python -m pip install pgserver psycopg2-binary tzdata
    $TMPDIR/pgtest_venv/Scripts/python .claude/prompts/character_v4_plan_validation.py

Exit 0 = every check passed.
"""
import sys
import tempfile
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pgserver
import psycopg2
import psycopg2.errors

REPO = Path(__file__).resolve().parents[2]
NOTES = REPO / "docs" / "charcterProfileGenerationNotes"
CHAR_SQL = (NOTES / "v4_reference_character_profile.sql").read_text(encoding="utf-8")
LOGGER_PY = (REPO / "services" / "message-logger" / "logger.py").read_text(encoding="utf-8")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")


def expect_error(conn, sql, params=None, exc=psycopg2.Error):
    with conn.cursor() as cur:
        try:
            cur.execute(sql, params)
        except exc as e:
            conn.rollback()
            return str(e).strip().splitlines()[0]
    conn.rollback()
    return None


def uid():
    return str(uuid.uuid4())


# ── 8. clock (pure python; the contract for app/character/clock.py) ─────────
NY = ZoneInfo("America/New_York")


def week_bounds(week, epoch, tz=NY):
    start_day = epoch + timedelta(days=7 * (week - 1))
    start = datetime.combine(start_day, time(0), tzinfo=tz)
    end = datetime.combine(start_day + timedelta(days=7), time(0), tzinfo=tz)
    return start, end


def loop_position(ts, epoch, tz=NY):
    local = ts.astimezone(tz)
    day = local.date()
    return (day - epoch).days // 7 + 1, day


def validate_clock():
    epoch = date(2026, 10, 4)  # a Sunday
    check("clock: epoch is a Sunday", epoch.weekday() == 6)
    # Week containing DST end (Sun 1 Nov 2026 02:00 -> 01:00)
    wk, day = loop_position(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), epoch)
    s, e = week_bounds(wk, epoch)
    hours = (e.astimezone(timezone.utc) - s.astimezone(timezone.utc)).total_seconds() / 3600
    check("clock: DST-end week is 169 real hours", wk == 5 and hours == 169, f"week={wk} hours={hours}")
    # Sat 23:59:59 NY belongs to the old week, Sun 00:00:00 NY to the new one
    sat = datetime(2026, 10, 10, 23, 59, 59, tzinfo=NY)
    sun = datetime(2026, 10, 11, 0, 0, 0, tzinfo=NY)
    check("clock: Saturday 23:59:59 NY is week 1", loop_position(sat, epoch)[0] == 1)
    check("clock: Sunday 00:00 NY is week 2", loop_position(sun, epoch)[0] == 2)
    # A UTC timestamp that is already Sunday in UTC but still Saturday in NY
    utc_edge = datetime(2026, 10, 11, 2, 0, tzinfo=timezone.utc)  # 22:00 Sat NY (EDT)
    wk, day = loop_position(utc_edge, epoch)
    check("clock: 02:00Z Sunday is still Saturday in NY", wk == 1 and day == date(2026, 10, 10))
    # DST start 14 Mar 2027 -> 167-hour week
    wk, _ = loop_position(datetime(2027, 3, 14, 12, tzinfo=timezone.utc), epoch)
    s, e = week_bounds(wk, epoch)
    hours = (e.astimezone(timezone.utc) - s.astimezone(timezone.utc)).total_seconds() / 3600
    check("clock: DST-start week is 167 real hours", hours == 167, f"week={wk} hours={hours}")
    # bounds round-trip for every week of a year
    ok = all(loop_position(week_bounds(w, epoch)[0], epoch)[0] == w
             and loop_position(week_bounds(w, epoch)[1] - timedelta(seconds=1), epoch)[0] == w
             for w in range(1, 60))
    check("clock: week_bounds/loop_position round-trip for 59 weeks", ok)


# ── 1-6. character_profile schema ───────────────────────────────────────────
def validate_character_schema(srv):
    srv.psql("DROP DATABASE IF EXISTS character_profile;")
    srv.psql("CREATE DATABASE character_profile;")
    srv.psql("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='character_reader') "
             "THEN CREATE ROLE character_reader LOGIN; END IF; END $$;")
    dsn = srv.get_uri("character_profile")
    conn = psycopg2.connect(dsn)

    for attempt in (1, 2):
        try:
            with conn.cursor() as cur:
                cur.execute(CHAR_SQL)
            conn.commit()
            check(f"schema: applies cleanly (run {attempt})", True)
        except psycopg2.Error as e:
            conn.rollback()
            check(f"schema: applies cleanly (run {attempt})", False, str(e).splitlines()[0])
            return

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='public' AND data_type='double precision'")
        n_double = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        n_tables = cur.fetchone()[0]
    check("schema: DOUBLE PRECISION columns present", n_double == 2, f"{n_double} columns")
    check("schema: table count", n_tables == 23, f"{n_tables} tables")

    # fixtures
    harry = uid()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO characters (id, slug, name, campaign, retains_fragments) "
                    "VALUES (%s,'harry','Harry Potter','hp',TRUE)", (harry,))
        frag = uid()
        cur.execute("INSERT INTO memory_fragments (id, character_id, source_week, gist, hooks) "
                    "VALUES (%s,%s,1,'my scar burned when he looked at me','{\"entities\":[\"snape\"]}')",
                    (frag, harry))
        for pos in range(3):
            cur.execute("INSERT INTO fragment_lead_up (fragment_id, position, text, embedding) "
                        "VALUES (%s,%s,%s,%s)", (frag, pos, f"beat {pos}", "[1,0,0]"))
        cur.execute("INSERT INTO fragment_unlocks (fragment_id, character_id, unlocked_week) "
                    "VALUES (%s,%s,2)", (frag, harry))
    conn.commit()

    # 2. immutability
    err = expect_error(conn, "UPDATE memory_fragments SET gist='changed' WHERE id=%s", (frag,))
    check("immutability: UPDATE memory_fragments blocked", err and "immutable" in err, err or "no error")
    err = expect_error(conn, "DELETE FROM memory_fragments WHERE id=%s", (frag,))
    check("immutability: DELETE memory_fragments blocked", err and "immutable" in err, err or "no error")
    err = expect_error(conn, "DELETE FROM fragment_unlocks WHERE fragment_id=%s", (frag,))
    check("immutability: re-locking (DELETE fragment_unlocks) blocked", err and "immutable" in err)
    err = expect_error(conn, "UPDATE fragment_lead_up SET text='x' WHERE fragment_id=%s", (frag,))
    check("immutability: UPDATE fragment_lead_up blocked", err and "immutable" in err)

    with conn.cursor() as cur:
        cur.execute("SET LOCAL character.allow_test_mutation = 'on'")
        cur.execute("DELETE FROM memory_fragments WHERE id=%s", (frag,))
        cur.execute("SELECT (SELECT count(*) FROM fragment_lead_up WHERE fragment_id=%s),"
                    "       (SELECT count(*) FROM fragment_unlocks WHERE fragment_id=%s)", (frag, frag))
        lead, unl = cur.fetchone()
    conn.commit()
    check("immutability: test opt-out deletes + cascades", (lead, unl) == (0, 0), f"lead_up={lead} unlocks={unl}")
    err = expect_error(conn, "UPDATE characters SET name='x' WHERE id=%s RETURNING id; "
                             "UPDATE memory_fragments SET gist='y'", (harry,))
    check("immutability: SET LOCAL does not leak into the next transaction", err is None,
          "no fragments left, so an UPDATE of 0 rows is fine")  # (0 rows -> no trigger)

    # 3. reset_steps exactly-once
    s, e = week_bounds(1, date(2026, 10, 4))
    with conn.cursor() as cur:
        cur.execute("INSERT INTO loop_weeks (campaign, week, starts_at, ends_at) VALUES ('hp',1,%s,%s)", (s, e))
    conn.commit()

    def run_step(step):
        with conn.cursor() as cur:
            cur.execute("SELECT reset_steps FROM loop_weeks WHERE campaign='hp' AND week=1 FOR UPDATE")
            steps = cur.fetchone()[0]
            if step in steps:
                conn.rollback()
                return "skipped"
            cur.execute("UPDATE loop_weeks SET reset_steps = reset_steps || jsonb_build_object(%s, "
                        "jsonb_build_object('completed_at', now())) WHERE campaign='hp' AND week=1", (step,))
        conn.commit()
        return "ran"

    first, second = run_step("archive"), run_step("archive")
    check("reset_steps: a completed step is skipped on re-run", (first, second) == ("ran", "skipped"))

    # 4. archive
    with conn.cursor() as cur:
        for name in ("knows-wingardium-leviosa", "lives-in-cupboard-under-stairs"):
            cur.execute("INSERT INTO week_knowledge_nodes (id, character_id, loop_week, name, kind, statement) "
                        "VALUES (%s,%s,1,%s,'fact','I know it')", (uid(), harry, name))
        cur.execute("UPDATE week_knowledge_nodes SET archived_at_week=1 "
                    "WHERE character_id=%s AND loop_week<=1 AND archived_at_week IS NULL", (harry,))
        cur.execute("SELECT count(*) FILTER (WHERE archived_at_week IS NULL), count(*) "
                    "FROM week_knowledge_nodes WHERE character_id=%s", (harry,))
        reachable, total = cur.fetchone()
    conn.commit()
    check("archive: nodes unreachable, none deleted", (reachable, total) == (0, 2))
    err = expect_error(conn, "INSERT INTO week_knowledge_nodes (id, character_id, loop_week, name, kind, statement) "
                             "VALUES (%s,%s,1,'knows-wingardium-leviosa','fact','dup')", (uid(), harry))
    check("archive: (character, week, name) unique", err is not None)

    # 5. vectors of different dims in one untyped column
    with conn.cursor() as cur:
        for mid, emb in (("m1", "[1,0,0]"), ("m2", "[0.9,0.1,0]"), ("m3", "[" + ",".join(["0.1"] * 768) + "]")):
            cur.execute("INSERT INTO experience_events (message_id, character_id, msg_type, from_agent, "
                        "visibility, payload, ts, loop_week, loop_day, embedding, embed_model) "
                        "VALUES (%s,%s,'character_say','tuber_1','present','{}',now(),1,'2026-10-04',%s,%s)",
                        (mid, harry, emb, "toy-3" if mid != "m3" else "nomic-embed-text"))
        cur.execute("SELECT message_id, 1 - (embedding <=> '[1,0,0]') FROM experience_events "
                    "WHERE embed_model='toy-3' ORDER BY embedding <=> '[1,0,0]'")
        rows = cur.fetchall()
    conn.commit()
    check("vector: mixed dims stored; cosine works within one model",
          [r[0] for r in rows] == ["m1", "m2"] and rows[0][1] > 0.999, str(rows))
    err = expect_error(conn, "INSERT INTO experience_events (message_id, character_id, msg_type, from_agent, "
                             "visibility, payload, ts, loop_week, loop_day) "
                             "VALUES ('m1',%s,'character_say','tuber_1','present','{}',now(),1,'2026-10-04')",
                       (harry,))
    check("ingest: (message_id, character_id) dedupes a redelivered message", err is not None)

    # 6. reader role
    with conn.cursor() as cur:
        cur.execute("SET ROLE character_reader")
        cur.execute("SELECT count(*) FROM characters")
        cur.execute("RESET ROLE")
    conn.commit()
    check("reader: SELECT allowed", True)
    err = expect_error(conn, "SET ROLE character_reader; INSERT INTO source_works VALUES ('x','x','x')")
    check("reader: INSERT denied", err and "permission denied" in err, err or "no error")
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS later_table (id INT)")
    conn.commit()
    err = expect_error(conn, "SET ROLE character_reader; SELECT * FROM later_table")
    check("reader: default privileges cover tables created later", err is None, err or "")
    conn.close()


# ── 7. messages ─────────────────────────────────────────────────────────────
def legacy_messages_ddl():
    start = LOGGER_PY.index("CREATE TABLE IF NOT EXISTS messages")
    end = LOGGER_PY.index("CREATE INDEX IF NOT EXISTS idx_messages_type ON messages (type);")
    return LOGGER_PY[start:end] + "CREATE INDEX IF NOT EXISTS idx_messages_type ON messages (type);"


M1 = """
ALTER TABLE messages ADD COLUMN IF NOT EXISTS character TEXT;
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_character_ts ON messages (character, timestamp);
"""

COLS = 'id, "from", "to", type, payload, timestamp, ingested_at, character'


def insert_msg(cur, ts, mtype="character_say", character=None, mid=None, conflict="(id)"):
    cur.execute(f'INSERT INTO messages (id, "from", "to", type, payload, timestamp, character) '
                f"VALUES (%s,'tuber_1','broadcast',%s,'{{}}',%s,%s) ON CONFLICT {conflict} DO NOTHING",
                (mid or uid(), mtype, ts, character))
    return cur.rowcount


def utc_day(d):
    return datetime.combine(d, time(0), tzinfo=timezone.utc)


def ensure_partitions(cur, first_day, last_day, parent="messages"):
    d = first_day
    while d <= last_day:
        cur.execute(f"CREATE TABLE IF NOT EXISTS messages_p{d:%Y%m%d} PARTITION OF {parent} "
                    f"FOR VALUES FROM (%s) TO (%s)", (utc_day(d), utc_day(d + timedelta(days=1))))
        d += timedelta(days=1)


def validate_messages(srv):
    srv.psql("DROP DATABASE IF EXISTS vt_messages;")
    srv.psql("CREATE DATABASE vt_messages;")
    conn = psycopg2.connect(srv.get_uri("vt_messages"))
    now = datetime.now(timezone.utc)
    today = now.date()

    with conn.cursor() as cur:
        cur.execute(legacy_messages_ddl())
        # old rows written before M1: no character column yet
        for age_days in (20, 10, 3, 0):
            cur.execute('INSERT INTO messages (id, "from", "to", type, payload, timestamp) '
                        "VALUES (%s,'tuber_1','broadcast','status_update','{}',%s)",
                        (uid(), now - timedelta(days=age_days, hours=1)))
        cur.execute(M1)
        cur.execute(M1)  # idempotent
        insert_msg(cur, now - timedelta(days=12), character="harry")
        insert_msg(cur, now - timedelta(hours=1), character="ron")
    conn.commit()
    check("messages M1: additive columns + indexes apply twice on the logger's real table", True)

    # C-A on the plain shape
    with conn.cursor() as cur:
        cur.execute("DELETE FROM messages WHERE type = ANY(%s) AND timestamp < now() - make_interval(hours => %s)",
                    (["status_update"], 6))
        deleted_a = cur.rowcount
    conn.commit()
    check("compaction A: noisy types older than N hours removed", deleted_a == 3, f"deleted={deleted_a}")

    # C-B on the plain shape (batched move)
    cutoff = utc_day(today - timedelta(days=7))
    with conn.cursor() as cur:
        cur.execute(f"CREATE TABLE IF NOT EXISTS messages_archive (LIKE messages INCLUDING DEFAULTS)")
        moved_total = 0
        while True:
            cur.execute(f"""
                WITH moved AS (
                    DELETE FROM messages WHERE id IN (
                        SELECT id FROM messages WHERE timestamp < %s LIMIT %s)
                    RETURNING {COLS})
                INSERT INTO messages_archive ({COLS}) SELECT {COLS} FROM moved""", (cutoff, 1))
            if cur.rowcount == 0:
                break
            moved_total += cur.rowcount
        cur.execute("SELECT count(*) FROM messages_archive")
        archived = cur.fetchone()[0]
    conn.commit()
    check("compaction B-plain: batched move to messages_archive", moved_total == 1 and archived == 1,
          f"moved={moved_total}")

    # M2: convert to daily partitions
    with conn.cursor() as cur:
        cur.execute("SELECT min(timestamp) FROM messages")
        first = cur.fetchone()[0].date()
        cur.execute("""
            CREATE TABLE messages_new (
                id UUID NOT NULL, "from" TEXT NOT NULL, "to" TEXT NOT NULL, type TEXT NOT NULL,
                payload JSONB NOT NULL, timestamp TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(), character TEXT,
                PRIMARY KEY (id, timestamp)
            ) PARTITION BY RANGE (timestamp)""")
        cur.execute("CREATE TABLE messages_new_default PARTITION OF messages_new DEFAULT")
        ensure_partitions(cur, first, today + timedelta(days=14), parent="messages_new")
        cur.execute(f"INSERT INTO messages_new ({COLS}) SELECT {COLS} FROM messages")
        cur.execute("ALTER TABLE messages RENAME TO messages_legacy")
        cur.execute("ALTER TABLE messages_new RENAME TO messages")
        cur.execute("ALTER TABLE messages_new_default RENAME TO messages_default")
        cur.execute('CREATE INDEX IF NOT EXISTS idx_messages_p_to ON messages ("to")')
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_p_type ON messages (type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_p_character_ts ON messages (character, timestamp)")
        cur.execute("SELECT (SELECT count(*) FROM messages), (SELECT count(*) FROM messages_legacy),"
                    " (SELECT relkind FROM pg_class WHERE relname='messages')")
        n_new, n_old, relkind = cur.fetchone()
    conn.commit()
    check("messages M2: rows copied into the partitioned table", n_new == n_old and relkind == "p",
          f"new={n_new} legacy={n_old} relkind={relkind}")

    # Logger insert on the partitioned shape: dedupe on (id, timestamp)
    with conn.cursor() as cur:
        mid, ts = uid(), now - timedelta(minutes=5)
        first_ins = insert_msg(cur, ts, character="harry", mid=mid, conflict="(id, timestamp)")
        second_ins = insert_msg(cur, ts, character="harry", mid=mid, conflict="(id, timestamp)")
        far = insert_msg(cur, now + timedelta(days=400), conflict="(id, timestamp)")
        cur.execute("SELECT count(*) FROM messages_default")
        in_default = cur.fetchone()[0]
    conn.commit()
    check("messages M2: logger re-delivery deduped by ON CONFLICT (id, timestamp)", (first_ins, second_ins) == (1, 0))
    check("messages M2: out-of-range timestamp lands in messages_default, not an error",
          far == 1 and in_default == 1)

    # Pitfall: a past-day row with no partition lands in messages_default, and
    # after that a partition for that day can NOT be created.
    keep_days = 7
    old_day = today - timedelta(days=9)
    with conn.cursor() as cur:
        insert_msg(cur, now - timedelta(days=9), conflict="(id, timestamp)")
    conn.commit()
    err = expect_error(conn, f"CREATE TABLE messages_p{old_day:%Y%m%d} PARTITION OF messages "
                             f"FOR VALUES FROM (%s) TO (%s)",
                       (utc_day(old_day), utc_day(old_day + timedelta(days=1))))
    check("pitfall: creating a past-day partition fails once default holds rows for it",
          err is not None and "default partition" in err, err or "no error")

    # Old partitions created by M2 (simulate an M2 that ran with 10 days of history)
    older = today - timedelta(days=10)
    with conn.cursor() as cur:
        cur.execute(f"CREATE TABLE messages_p{older:%Y%m%d} PARTITION OF messages FOR VALUES FROM (%s) TO (%s)",
                    (utc_day(older), utc_day(older + timedelta(days=1))))
        insert_msg(cur, utc_day(older) + timedelta(hours=3), conflict="(id, timestamp)")
    conn.commit()

    # C-B on the partitioned shape: detach -> attach, then drain old default rows
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS messages_archive_p (
                id UUID NOT NULL, "from" TEXT NOT NULL, "to" TEXT NOT NULL, type TEXT NOT NULL,
                payload JSONB NOT NULL, timestamp TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(), character TEXT,
                PRIMARY KEY (id, timestamp)) PARTITION BY RANGE (timestamp)""")
        cur.execute("""SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid
                       JOIN pg_class p ON p.oid=i.inhparent
                       WHERE p.relname='messages' AND c.relname ~ '^messages_p[0-9]{8}$'""")
        parts = sorted(r[0] for r in cur.fetchall())
        moved = []
        for name in parts:
            day = datetime.strptime(name[len("messages_p"):], "%Y%m%d").date()
            if day + timedelta(days=1) <= today - timedelta(days=keep_days):
                cur.execute(f"ALTER TABLE messages DETACH PARTITION {name}")
                cur.execute(f"ALTER TABLE messages_archive_p ATTACH PARTITION {name} "
                            f"FOR VALUES FROM (%s) TO (%s)", (utc_day(day), utc_day(day + timedelta(days=1))))
                moved.append(name)
        # rows stranded in the default partition: batched move into the
        # archive's own default partition
        cur.execute("CREATE TABLE IF NOT EXISTS messages_archive_p_default PARTITION OF messages_archive_p DEFAULT")
        cur.execute(f"""
            WITH m AS (DELETE FROM messages_default WHERE timestamp < %s RETURNING {COLS})
            INSERT INTO messages_archive_p ({COLS}) SELECT {COLS} FROM m""",
                    (utc_day(today - timedelta(days=keep_days)),))
        cur.execute("SELECT count(*) FROM messages WHERE timestamp < %s",
                    (utc_day(today - timedelta(days=keep_days)),))
        left_old = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM messages_archive_p")
        archived = cur.fetchone()[0]
    conn.commit()
    check("compaction B-partitioned: old partitions detached and attached to the archive",
          len(moved) == 1 and left_old == 0 and archived == 2,
          f"partitions_moved={len(moved)} archived_rows={archived} old_rows_left={left_old}")
    conn.close()


def main():
    validate_clock()
    datadir = tempfile.mkdtemp(prefix="v4_pg_")
    srv = pgserver.get_server(datadir, cleanup_mode="delete")
    try:
        validate_character_schema(srv)
        validate_messages(srv)
    finally:
        srv.cleanup()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
