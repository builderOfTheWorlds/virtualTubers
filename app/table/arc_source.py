"""Story source for the live agent table (build plan P3.2, decision U8).

The table's story comes from the 3-layer generator's arc plan (generator
Postgres, table `generation_artifacts`), NOT from campaigns/<pack>/scenes/.
This module only READS a run's `arc_plan`, `brief` and `tree` artifacts;
Layer 3 `dialogue` is ignored because the live table replaces it.

Connection: the GENERATOR Postgres (not the app DB), configured by
GENERATOR_POSTGRES_HOST (127.0.0.1), _PORT (5455), _DB (generation),
_USER (generation), _PASSWORD (required). The session is forced read-only
(default_transaction_read_only=on). The password is never logged.

The query is a minimal copy of services/3layer-generator/generation_store.py's
read path (no cross-package import). psycopg2 is imported lazily so the module
imports cleanly on machines without it.
"""
import dataclasses
import json
import logging
import os
from typing import Any, Dict, Mapping

log = logging.getLogger(__name__)

KINDS = ("arc_plan", "brief", "tree")

_QUERY = (
    "SELECT kind, segment_id, content FROM generation_artifacts "
    "WHERE pack = %s AND kind IN ('arc_plan', 'brief', 'tree') "
    "ORDER BY kind, segment_id"
)


class ArcSourceError(Exception):
    """A run cannot be used as a table story (missing / empty arc plan, bad data)."""


class EmptyArcPlanError(ArcSourceError):
    """The run's arc_plan has no segments."""


class ConfigError(Exception):
    """Generator DB connection is not configured (e.g. password missing)."""


@dataclasses.dataclass(frozen=True)
class RunArtifacts:
    run_id: str
    arc_plan: Dict[str, Any]
    briefs: Dict[str, Any]
    trees: Dict[str, Any]

    def segments(self):
        """arc_plan segments sorted by `order` (ties broken by id)."""
        segs = self.arc_plan.get("segments") or []
        return sorted(segs, key=lambda s: (s.get("order", 0), str(s.get("id"))))

    @classmethod
    def from_rows(cls, run_id, rows):
        """Build from (kind, segment_id, content) rows. Raises ArcSourceError /
        EmptyArcPlanError when the run has no usable arc plan."""
        arc_plan = None
        briefs, trees = {}, {}
        for kind, segment_id, content in rows:
            if isinstance(content, str):
                content = json.loads(content)
            if kind == "arc_plan":
                arc_plan = content
            elif kind == "brief":
                briefs[segment_id] = content
            elif kind == "tree":
                trees[segment_id] = content
        if arc_plan is None:
            raise ArcSourceError(f"run {run_id!r}: no arc_plan artifact")
        if not isinstance(arc_plan, dict) or not arc_plan.get("segments"):
            raise EmptyArcPlanError(
                f"run {run_id!r}: arc_plan has no segments (empty plan); "
                "pick another generator run or re-run plan_arc")
        return cls(run_id=run_id, arc_plan=arc_plan, briefs=briefs, trees=trees)

    @classmethod
    def from_json(cls, path):
        """Load a fixture exported as {"run_id": ..., "artifacts": [{kind,
        segment_id, content}, ...]}."""
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        rows = [(a["kind"], a.get("segment_id"), a["content"])
                for a in doc.get("artifacts") or []]
        return cls.from_rows(doc["run_id"], rows)


def generator_dsn_params(env: Mapping[str, str] = os.environ) -> Dict[str, Any]:
    """Connection kwargs from GENERATOR_POSTGRES_*. Raises ConfigError when the
    password is missing. Never log the returned dict (it holds the password)."""
    password = env.get("GENERATOR_POSTGRES_PASSWORD")
    if not password:
        raise ConfigError("GENERATOR_POSTGRES_PASSWORD is not set")
    return {
        "host": env.get("GENERATOR_POSTGRES_HOST") or "127.0.0.1",
        "port": int(env.get("GENERATOR_POSTGRES_PORT") or 5455),
        "dbname": env.get("GENERATOR_POSTGRES_DB") or "generation",
        "user": env.get("GENERATOR_POSTGRES_USER") or "generation",
        "password": password,
        "connect_timeout": 5,
        "options": "-c default_transaction_read_only=on",
    }


def connect_generator(env: Mapping[str, str] = os.environ):
    """Open a READ-ONLY psycopg2 connection to the generator Postgres."""
    params = generator_dsn_params(env)
    import psycopg2  # lazy: optional dependency
    log.info("connecting to generator db %s@%s:%s/%s (read-only)",
             params["user"], params["host"], params["port"], params["dbname"])
    conn = psycopg2.connect(**params)
    conn.set_session(readonly=True, autocommit=False)
    return conn


def load_run(conn, run_id: str) -> RunArtifacts:
    """Read one run's arc_plan / brief / tree artifacts (read-only)."""
    cur = conn.cursor()
    try:
        cur.execute(_QUERY, (run_id,))
        rows = cur.fetchall()
    finally:
        cur.close()
        try:
            conn.rollback()  # end the read-only transaction; never commits
        except Exception:  # pragma: no cover - connection already gone
            pass
    if not rows:
        raise ArcSourceError(f"run {run_id!r}: no artifacts in generation_artifacts")
    return RunArtifacts.from_rows(run_id, rows)
