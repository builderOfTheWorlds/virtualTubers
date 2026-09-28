"""Fakes and raw-SQL helpers for the character v4 Phase 3a tests (WP-15..WP-20).

Written by hand (playbook §1.4: test files and fixtures). Nothing here imports
a `character.*` module at import time, so importing it never trips the
pending guard (tests/character/pending.py); the fixtures that need
`character.db` import it lazily.

OB-41 (.claude/prompts/ashiorid_office_build_plan.md): the cast is the 8
office characters; office seats publish from worker ids `tuber_N`
(app/office/roles.py SEAT); the office clock ("office_clock",
app/office/protocol.py CLOCK_SENDER) is the GM-equivalent non-character.
"""
import json
import logging
import uuid
from collections import namedtuple
from datetime import datetime, time, timedelta, timezone

import pytest

log = logging.getLogger(__name__)

#: slug -> worker id (copy of app/office/roles.py SEAT, OB-41).
OFFICE_SEATS = {"ceo": "tuber_0", "tech_lead": "tuber_1", "analyst": "tuber_2",
                "engineer": "tuber_3", "tester": "tuber_4", "marketing": "tuber_5",
                "office_manager": "tuber_6", "party_member": "tuber_7"}
CLOCK_SENDER = "office_clock"
CAMPAIGN = "ashiorid_office"


# ── bus envelopes ──────────────────────────────────────────────────────────────
def envelope(type_, from_, payload, ts, to="broadcast", msg_id=None):
    """A bus message shaped like app/message_bus.py build_message(), with a chosen timestamp."""
    if isinstance(ts, datetime):
        ts = ts.astimezone(timezone.utc).isoformat()
    msg_id = msg_id or str(uuid.uuid4())
    return {"id": msg_id, "from": from_, "to": to, "type": type_, "payload": payload,
            "timestamp": ts, "correlation_id": msg_id, "causation_id": None}


def say(slug, text, ts, present, addressees=(), from_=None, scene_id="standup"):
    """A character_say (plan §3.2) spoken by `slug`."""
    return envelope("character_say", from_ or f"char:{slug}",
                    {"campaign": CAMPAIGN, "scene_id": scene_id, "character": slug,
                     "addressees": list(addressees), "present": list(present), "text": text}, ts)


def scene(text, ts, present, kind="narration", from_=CLOCK_SENDER, scene_id="standup"):
    """A scene_event (plan §3.2) with no speaking character."""
    return envelope("scene_event", from_,
                    {"campaign": CAMPAIGN, "scene_id": scene_id, "kind": kind,
                     "character": None, "present": list(present), "text": text}, ts)


def thinking(from_, text, ts):
    """An agent_thinking (app/agent_metrics.py:240-261): payload {"text"} only."""
    return envelope("agent_thinking", from_, {"text": text}, ts)


# ── LLM / embeddings / producer fakes ──────────────────────────────────────────
class FakeLLM:
    """A `complete(system, user, shape) -> dict` callable returning canned replies in order.

    `replies` items are dicts (returned as copies) or callables (user -> dict).
    When they run out the last one repeats. Every call is kept in `.calls`.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, system, user, shape):
        self.calls.append({"system": system, "user": user, "shape": shape})
        if not self.replies:
            raise AssertionError("FakeLLM called but no reply was configured")
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        return reply(user) if callable(reply) else json.loads(json.dumps(reply))


class FakeEmbed:
    """`embed(texts) -> list[list[float]]`: a fixed, distinct unit-ish vector per text."""

    def __init__(self, dim=4):
        self.dim = dim
        self.calls = []

    def vector(self, text):
        seed = sum(ord(ch) for ch in text) or 1
        return [round(((seed * (i + 3)) % 97) / 97.0 + 0.01, 6) for i in range(self.dim)]

    def __call__(self, texts):
        texts = list(texts)
        self.calls.append(texts)
        return [self.vector(text) for text in texts]


class FakeProducer:
    """MessageProducer stand-in: `.send(msg)` keeps the message."""

    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


# ── Kafka consumer / DB connection fakes (unit tests of the ingest loop) ───────
Record = namedtuple("Record", "value")


class FakeConsumer:
    """KafkaConsumer stand-in for IngestLoop.

    poll(timeout_ms=..., max_records=...) returns {"vtuber.messages-0": [Record]}
    with at most `per_poll` (and at most max_records) queued messages, or {}.
    `on_poll()` runs before each poll (e.g. advance a FakeClock). `commit()`
    appends "kafka_commit" to the shared `journal` list.
    """

    def __init__(self, messages, per_poll=50, journal=None, on_poll=None):
        self.queue = list(messages)
        self.per_poll = per_poll
        self.journal = journal if journal is not None else []
        self.on_poll = on_poll
        self.polls = 0
        self.commits = 0

    def poll(self, timeout_ms=0, max_records=None):
        self.polls += 1
        if self.on_poll is not None:
            self.on_poll()
        take = self.per_poll if max_records is None else min(self.per_poll, max_records)
        batch, self.queue = self.queue[:take], self.queue[take:]
        return {"vtuber.messages-0": [Record(value) for value in batch]} if batch else {}

    def commit(self, *args, **kwargs):
        self.commits += 1
        self.journal.append("kafka_commit")


class FakeConn:
    """A connection stand-in that only journals commit/rollback (no cursor)."""

    def __init__(self, journal=None):
        self.journal = journal if journal is not None else []

    def commit(self):
        self.journal.append("db_commit")

    def rollback(self):
        self.journal.append("db_rollback")

    def cursor(self, *args, **kwargs):
        raise AssertionError("FakeConn has no cursor: inject insert_many/update_status")

    def close(self):
        self.journal.append("closed")


class FakeSourceCursor:
    """A read-only cursor over fixture `messages` rows, for the backfill tests.

    Every execute() is kept in `.executed` as (query, params). A query that
    mentions to_regclass answers fetchone() with (None,) unless the table is in
    `tables`; any other query pages through `rows` with fetchmany()/fetchall().
    """

    def __init__(self, rows, tables=("messages",)):
        self.rows = list(rows)
        self.tables = set(tables)
        self.executed = []
        self._pending = []
        self._one = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))
        text = query if isinstance(query, str) else repr(query)
        if "to_regclass" in text:
            name = str(params[0] if params else "").split(".")[-1]
            self._one = (name if name in self.tables else None,)
            self._pending = []
        else:
            self._one = None
            self._pending = list(self.rows) if "messages_archive" not in text else []

    def fetchone(self):
        if self._one is not None:
            return self._one
        return self._pending.pop(0) if self._pending else None

    def fetchmany(self, size=500):
        batch, self._pending = self._pending[:size], self._pending[size:]
        return batch

    def fetchall(self):
        batch, self._pending = self._pending, []
        return batch

    def close(self):
        pass


class FakeSourceConn:
    """The virtualtubers DB stand-in: one FakeSourceCursor shared by every cursor() call."""

    def __init__(self, rows, tables=("messages",)):
        self.cur = FakeSourceCursor(rows, tables)
        self.closed = False
        self.commits = 0

    def cursor(self, *args, **kwargs):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def message_row(msg):
    """A `messages` row tuple (id, from, to, type, payload, timestamp) for FakeSourceConn."""
    return (msg["id"], msg["from"], msg["to"], msg["type"], msg["payload"],
            datetime.fromisoformat(msg["timestamp"]))


# ── character_profile seeding (raw SQL, so tests don't depend on other stores) ─
def seed_agents(conn, mapping):
    """INSERT character_agents rows {agent_id: character_id}. Does not commit."""
    with conn.cursor() as cur:
        for agent_id, character_id in mapping.items():
            cur.execute("INSERT INTO character_agents (agent_id, character_id) VALUES (%s, %s)",
                        (agent_id, character_id))


def seed_event(conn, character_id, *, text, ts, loop_week, loop_day, visibility="present",
               msg_type="character_say", from_agent="tuber_0", message_id=None, scene_id=None):
    """INSERT one experience_events row; return its message_id. Does not commit."""
    message_id = message_id or str(uuid.uuid4())
    payload = {"text": text}
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO experience_events (message_id, character_id, msg_type, from_agent, "
            "scene_id, visibility, text, payload, ts, loop_week, loop_day) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)",
            (message_id, character_id, msg_type, from_agent, scene_id, visibility, text,
             json.dumps(payload), ts, loop_week, loop_day))
    return message_id


def seed_ingest_status(conn, last_message_ts, group="character-ingest"):
    """Pretend the ingest consumer has committed up to `last_message_ts`. Does not commit."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO ingest_status (consumer_group, last_message_ts, last_committed_at) "
                    "VALUES (%s, %s, now()) ON CONFLICT (consumer_group) DO UPDATE SET "
                    "last_message_ts = EXCLUDED.last_message_ts", (group, last_message_ts))


def count(conn_or_dsn, sql, params=()):
    """SELECT count(*)-style scalar, on a fresh connection when given a DSN string."""
    import psycopg2

    own = isinstance(conn_or_dsn, str)
    conn = psycopg2.connect(conn_or_dsn) if own else conn_or_dsn
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]
    finally:
        if own:
            conn.close()


@pytest.fixture
def migrated_dsn(pg):
    """`pg` migrated with character.db.migrate (import this fixture into a test module)."""
    import psycopg2
    from character import db

    conn = psycopg2.connect(pg)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    return pg


def connect_to(dsn):
    """A `connect(cfg, role="main")` for character.jobs.run_job, opening `dsn`."""
    import psycopg2

    def _connect(cfg, role="main"):
        conn = psycopg2.connect(dsn)
        conn.autocommit = False
        return conn
    return _connect


# ── the virtualtubers `messages` table (WP-17, WP-18) ──────────────────────────
#: The pre-v4 logger DDL for `messages` (services/message-logger/logger.py:21-38
#: at commit 324be04), frozen here so the M1/M2/compaction tests start from the
#: real legacy shape even after WP-17 rewrites logger.py.
LEGACY_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY,
    "from"      TEXT NOT NULL,
    "to"        TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     JSONB NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    correlation_id UUID,
    causation_id   UUID
);
CREATE INDEX IF NOT EXISTS idx_messages_to ON messages ("to");
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages (type);
CREATE INDEX IF NOT EXISTS idx_messages_correlation ON messages (correlation_id);
"""

M1_SQL = """
ALTER TABLE messages ADD COLUMN IF NOT EXISTS character TEXT;
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_character_ts ON messages (character, timestamp);
"""

#: The M1 statements, one per line, exactly as docs/charcterProfileGenerationNotes/v4_reference_messages.sql.
M1_STATEMENTS = tuple(line for line in M1_SQL.strip().splitlines())


def utc_midnight(day):
    return datetime.combine(day, time(0), tzinfo=timezone.utc)


def create_legacy_messages(conn, with_m1=True):
    """Create the legacy (unpartitioned) messages table, optionally with M1. Commits."""
    with conn.cursor() as cur:
        cur.execute(LEGACY_MESSAGES_DDL)
        if with_m1:
            cur.execute(M1_SQL)
    conn.commit()


def create_partitioned_messages(conn, first_day, last_day):
    """The M2 shape (reference SQL, plus the logger's correlation columns), built
    directly: messages PARTITION BY RANGE (timestamp), messages_default, and one
    messages_pYYYYMMDD per UTC day first_day..last_day. Commits."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE messages (
                id UUID NOT NULL, "from" TEXT NOT NULL, "to" TEXT NOT NULL, type TEXT NOT NULL,
                payload JSONB NOT NULL, timestamp TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                correlation_id UUID, causation_id UUID, character TEXT,
                PRIMARY KEY (id, timestamp)
            ) PARTITION BY RANGE (timestamp)""")
        cur.execute("CREATE TABLE messages_default PARTITION OF messages DEFAULT")
        day = first_day
        while day <= last_day:
            cur.execute(f"CREATE TABLE messages_p{day:%Y%m%d} PARTITION OF messages "
                        "FOR VALUES FROM (%s) TO (%s)",
                        (utc_midnight(day), utc_midnight(day + timedelta(days=1))))
            day += timedelta(days=1)
    conn.commit()


def insert_message(conn, ts, type_="character_say", character=None, from_="tuber_3",
                   msg_id=None, payload=None, correlation_id=None):
    """Insert one messages row (either shape); return its id. Does not commit."""
    msg_id = msg_id or str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute('INSERT INTO messages (id, "from", "to", type, payload, timestamp, '
                    "correlation_id, character) VALUES (%s, %s, 'broadcast', %s, %s::jsonb, %s, %s, %s)",
                    (msg_id, from_, type_, json.dumps(payload or {"text": "x"}), ts,
                     correlation_id, character))
    return msg_id


def relkind(conn, name):
    """pg_class.relkind of a public relation ('r' plain, 'p' partitioned), or None."""
    with conn.cursor() as cur:
        cur.execute("SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relname = %s", (name,))
        row = cur.fetchone()
    return row[0] if row else None


def partitions_of(conn, parent):
    """Sorted names of the partitions attached to `parent`."""
    with conn.cursor() as cur:
        cur.execute("SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = %s", (parent,))
        return sorted(row[0] for row in cur.fetchall())


def index_names(conn, table):
    with conn.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s",
                    (table,))
        return {row[0] for row in cur.fetchall()}
