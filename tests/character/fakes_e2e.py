"""Fakes and helpers for the character v4 Phase 3b tests (WP-21..WP-25, OB-41).

Kept apart from fakes.py (Phase 1), fakes_generator.py (Phase 2) and
fakes_runtime.py (Phase 3a) so parallel work never edits the same file.
Nothing here imports a `character.*` module at import time, so importing it
never trips the pending guard and never needs a database.

- `TopicEmbed`: a deterministic fake embedder. Texts assigned to the same
  topic get vectors with cosine ~0.94; any other pair is ~orthogonal (|cos|
  well under the recall bias 0.6). Mirrors what the recall probe measured with
  nomic-embed-text: paraphrase ~0.8, unrelated ~0.4 (plan §7).
- `FakeProducer`: records `send(message)` like message_bus.MessageProducer.
- `FakeWorkerControl`: `is_enabled` / `set_enabled` like worker_control.WorkerControl.
- `ScriptedLLM`: an improviser LLM (`complete(system, messages)`) that records
  every prompt and replies with the structured {"line", "emotion"} JSON.
- `FakeJudge`: the recall judge; returns {"echoes", "reason"} from a script.
- `CannedCharacterLLM`: canned `summary` / `fragment` completions for the
  e2e test (reply shapes of app/character/prompts/summary_day.md and
  fragment.md); `NullConn`: a messages-DB stand-in.
- `bus_message`, `snapshot`, `load_yaml_fixture`, `FIXTURES`.
"""
import copy
import hashlib
import json
import logging
import math
import random
import re
import uuid
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
OFFICE_PACK = REPO_ROOT / "campaigns" / "ashiorid_office"

#: What a brief must never contain (playbook T22.2; user decision 2026-09-28
#: (item 1): "the characters are unaware of time passing; to them it's the same
#: week over and over"). Week NUMBERS and loop / reset / repetition wording are
#: forbidden; the ordinary word "week" in a character's own experience ("one
#: green week", "three weeks", "in my first week") is allowed and must survive.
#: Same pattern, byte for byte, as character.brief.FORBIDDEN_RE
#: (tools/qwen_worker/specs/character_wp22_brief.yaml).
FORBIDDEN_BRIEF_RE = re.compile(r"""
    \bweeks?\s*(?:\#|no\.?\s*|number\s+)?\d+\b
  | \b\d+(?:st|nd|rd|th)\s+weeks?\b
  | \bweek\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b
  | \bW\d+\b
  | \b(?:loop|loops|looped|looping|reset|resets|resetting)\b
  | \btime\s+(?:repeats?|repeated|repeating|resets?|restarts?)\b
  | \brepeats?\s+itself\b
  | \bagain\s+and\s+again\b
  | \bover\s+and\s+over\b
  | \bsame\s+weeks?\b
  | \breliv(?:e|es|ed|ing)\b
  | \bgroundhog\b
""", re.IGNORECASE | re.VERBOSE)

#: Samples for the scrub tests: each FORBIDDEN one must be dropped, each
#: ALLOWED one must be kept verbatim (user decision 2026-09-28, item 1).
FORBIDDEN_BRIEF_SAMPLES = (
    "Week 2 has been rough.", "It is week #3 now.", "By the 3rd week I knew.", "It was week one.",
    "See W4 for details.", "There is a loop in the scorer.", "They looped the demo.",
    "We reset the board.", "Resetting is easy.", "Time repeats here.", "Time resets at midnight.",
    "History repeats itself.", "It happens again and again.", "Over and over, the same call.",
    "It is the same week.", "I relive that call.", "It is Groundhog Day.",
)
ALLOWED_BRIEF_SAMPLES = (
    "What I want is one week.", "I want one green week.", "We overlapped for a few weeks.",
    "The Office Manager moved their chair a week later.", "I had to ask her in my first week.",
    "The weekly numbers are in.", "I kept my head down.", "A soup lasts a week.",
)

#: Tables a reset / undo touches (plan §5.2). character_jobs and
#: character_artifacts are audit logs and are deliberately left out.
LOOP_TABLES = ("loop_weeks", "experience_events", "daily_summaries", "week_knowledge_nodes",
               "week_knowledge_edges", "memory_fragments", "fragment_lead_up",
               "fragment_links", "fragment_unlocks", "fragment_recalls")


def load_yaml_fixture(name):
    """Parse tests/character/fixtures/<name>."""
    with open(FIXTURES / name, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _unit(vec):
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _gauss(seed_text, dim):
    rng = random.Random(hashlib.sha256(seed_text.encode("utf-8")).hexdigest())
    return [rng.gauss(0.0, 1.0) for _ in range(dim)]


class TopicEmbed:
    """embed(texts) -> list of vectors; same topic => cosine ~0.94.

    `assign(text, topic)` puts a text on a topic. An unassigned text gets its
    own direction, so it is ~orthogonal to everything else. Every call is
    recorded in `calls` (a list of the text lists).
    """

    def __init__(self, dim=64, noise=0.25, salt="topic-embed"):
        self.dim = dim
        self.noise = noise
        self.salt = salt
        self.topics = {}
        self.calls = []

    def assign(self, text, topic):
        self.topics[text] = str(topic)
        return self

    def assign_many(self, texts, topic):
        for text in texts:
            self.assign(text, topic)
        return self

    def vector(self, text):
        topic = self.topics.get(text)
        if topic is None:
            return _unit(_gauss(f"{self.salt}|free|{text}", self.dim))
        axis = _unit(_gauss(f"{self.salt}|topic|{topic}", self.dim))
        jitter = _unit(_gauss(f"{self.salt}|jitter|{text}", self.dim))
        return _unit([a + self.noise * j for a, j in zip(axis, jitter)])

    def __call__(self, texts):
        texts = list(texts)
        self.calls.append(texts)
        return [self.vector(text) for text in texts]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0)


def embed_for_probe_fixture(fixture, **kwargs):
    """A TopicEmbed where each lead-up beat is topic <i> and every window beat
    listed in fixture["paraphrase_of"][<window>] shares its lead-up beat's topic.
    The gist gets its own topic. Everything else stays unassigned (noise)."""
    embed = TopicEmbed(**kwargs)
    for index, text in enumerate(fixture["lead_up"]):
        embed.assign(text, f"lead-{index}")
    embed.assign(fixture["fragment"]["gist"], "gist")
    for window, mapping in (fixture.get("paraphrase_of") or {}).items():
        for text, lead_index in zip(fixture["windows"][window], mapping):
            if lead_index is not None:
                embed.assign(text, f"lead-{lead_index}")
    return embed


class FakeProducer:
    """Records every message sent; `fail=True` makes send() raise."""

    def __init__(self, fail=False, on_send=None):
        self.sent = []
        self.fail = fail
        self.on_send = on_send

    def send(self, message):
        if self.fail:
            raise RuntimeError("fake producer is down")
        self.sent.append(copy.deepcopy(message))
        if self.on_send is not None:
            self.on_send(message)
        return message

    def of_type(self, type_):
        return [m for m in self.sent if m.get("type") == type_]


class FakeWorkerControl:
    """In-memory WorkerControl: a missing key reads as enabled (fail open)."""

    def __init__(self, enabled=None):
        self.flags = dict(enabled or {})
        self.checks = []
        self.sets = []

    def is_enabled(self, worker_id):
        self.checks.append(worker_id)
        return self.flags.get(worker_id, True)

    def set_enabled(self, worker_id, enabled):
        self.sets.append((worker_id, bool(enabled)))
        self.flags[worker_id] = bool(enabled)
        return enabled


class ScriptedLLM:
    """complete(system, messages) -> '{"line": ..., "emotion": "neutral"}'."""

    def __init__(self, slug, line=None):
        self.slug = slug
        self.line = line or f"Okay, so, {slug} has a thought about that."
        self.calls = []

    def complete(self, system_prompt, messages):
        self.calls.append({"system": system_prompt, "messages": copy.deepcopy(messages)})
        return json.dumps({"line": self.line, "emotion": "neutral"})


class FakeJudge:
    """judge(candidate, recent_texts) -> {"echoes": bool, "reason": str}."""

    def __init__(self, verdicts=(True,), default=False):
        self.verdicts = list(verdicts)
        self.default = default
        self.calls = []

    def __call__(self, candidate, recent_texts):
        self.calls.append((getattr(candidate, "fragment_id", candidate), list(recent_texts)))
        echoes = self.verdicts.pop(0) if self.verdicts else self.default
        return {"echoes": bool(echoes), "reason": "fake judge"}


class CannedCharacterLLM:
    """Canned summary and fragment replies for the e2e test, as the
    `complete(system, user, shape) -> dict` callables the Phase 3a jobs take
    (DailyMaintenanceJob(complete=...), WeeklyResetJob(complete_summary=...,
    complete_fragment=...)), in the reply shapes of
    app/character/prompts/summary_day.md and fragment.md.

    - `summary`: {"summary", "nodes"}. The day's token (the first of `tokens`
      found in the prompt) is echoed into the node name and statement, so a
      test can tell which day's knowledge a brief holds.
    - `fragment`: {"anchor_event_id", "gist", "name", "hooks",
      "rank_rationale"}. The anchor is the first of `anchor_ids` found in the
      prompt (the moments are listed as "<event_id>: <text>"), else the first
      "e2e-" id there.
    Every call is kept in `calls` as (kind, system, user).
    """

    ID_RE = re.compile(r"\be2e-[a-z0-9-]+\b")

    def __init__(self, gist, hooks, anchor_ids=(), tokens=()):
        self.gist = gist
        self.hooks = hooks
        self.anchor_ids = list(anchor_ids)
        self.tokens = list(tokens)
        self.calls = []

    def summary(self, system, user, shape=None):
        self.calls.append(("summary", system, user))
        token = next((t for t in self.tokens if t in user), "plain")
        return {"summary": f"I worked through the day and kept thinking about the {token} note. "
                           "I kept my head down and did my part.",
                "nodes": [{"name": f"remembers-{token}-note", "kind": "fact",
                           "statement": f"I remember the {token} note on my desk."}]}

    def fragment(self, system, user, shape=None):
        self.calls.append(("fragment", system, user))
        anchor = next((a for a in self.anchor_ids if a in user), None)
        if anchor is None:
            found = self.ID_RE.findall(user)
            anchor = found[0] if found else None
        return {"anchor_event_id": anchor, "gist": self.gist,
                "name": "remembers-red-latency-graph", "hooks": copy.deepcopy(self.hooks),
                "rank_rationale": "It is the moment that stayed with me."}

    def kinds(self):
        return [call[0] for call in self.calls]


class NullConn:
    """A messages-DB connection stand-in for daily-maintenance's compaction step
    (the e2e test replaces compaction itself; this only has to exist)."""

    def cursor(self, *args, **kwargs):
        raise AssertionError("the e2e test replaces compaction; NullConn has no cursor")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def bus_message(type_, sender, payload, ts, message_id=None, to="broadcast"):
    """A bus envelope in the app/message_bus.py build_message shape with a
    fixed id and body timestamp (ingest reads loop time from the body, plan §3.3)."""
    return {"id": message_id or str(uuid.uuid4()), "from": sender, "to": to, "type": type_,
            "payload": copy.deepcopy(payload), "timestamp": ts.isoformat(),
            "correlation_id": message_id, "causation_id": None}


def snapshot(dsn, tables=LOOP_TABLES):
    """{table: sorted list of each row as JSON text}, read in a new session."""
    import psycopg2
    from psycopg2 import sql

    conn = psycopg2.connect(dsn)
    try:
        out = {}
        with conn.cursor() as cur:
            for table in tables:
                cur.execute(sql.SQL("SELECT to_jsonb(t)::text FROM {} t ORDER BY 1")
                            .format(sql.Identifier(table)))
                out[table] = [row[0] for row in cur.fetchall()]
        return out
    finally:
        conn.close()


def count(dsn, query, params=()):
    """One scalar from a fresh session."""
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()[0]
    finally:
        conn.close()
