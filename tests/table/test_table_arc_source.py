"""P3.2 tests for app/table/arc_source.py (story from the generator arc plan, U8)."""
import json
import os
import pathlib

import pytest

from pending import require

arc_source = require("table.arc_source", "app/table/arc_source.py", wp="P3.2")

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
DND_RUN = "ashiorid_1_20260913_180158_ce8d"
OFFICE_RUN = "ashiorid_office_20260929_135927_9c1b"


@pytest.mark.parametrize("run_id,n_segments", [(DND_RUN, 15), (OFFICE_RUN, 4)])
def test_from_json_loads_fixture(run_id, n_segments):
    run = arc_source.RunArtifacts.from_json(FIX / f"{run_id}.json")
    assert run.run_id == run_id
    segs = run.segments()
    assert len(segs) == n_segments
    assert [s["order"] for s in segs] == sorted(s["order"] for s in segs)
    ids = {s["id"] for s in segs}
    assert set(run.trees) == ids
    assert set(run.briefs) == ids


def test_fixtures_hold_no_dialogue():
    for run_id in (DND_RUN, OFFICE_RUN):
        doc = json.loads((FIX / f"{run_id}.json").read_text())
        assert {a["kind"] for a in doc["artifacts"]} == {"arc_plan", "brief", "tree"}


def test_empty_arc_plan_rejected(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"run_id": "r0", "artifacts": [
        {"kind": "arc_plan", "segment_id": None, "content": {"segments": []}}]}))
    with pytest.raises(arc_source.EmptyArcPlanError, match="r0"):
        arc_source.RunArtifacts.from_json(p)


def test_missing_arc_plan_rejected():
    with pytest.raises(arc_source.ArcSourceError):
        arc_source.RunArtifacts.from_rows("r1", [("tree", "s", {})])


def test_from_rows_decodes_json_strings():
    run = arc_source.RunArtifacts.from_rows("r2", [
        ("arc_plan", None, json.dumps({"segments": [{"id": "s", "order": 0}]})),
        ("tree", "s", json.dumps({"s": {"kind": "leaf"}})),
    ])
    assert run.trees["s"]["s"]["kind"] == "leaf"


def test_dsn_requires_password():
    with pytest.raises(arc_source.ConfigError):
        arc_source.generator_dsn_params({})
    with pytest.raises(arc_source.ConfigError):
        arc_source.connect_generator({"GENERATOR_POSTGRES_HOST": "127.0.0.1"})


def test_dsn_defaults_and_read_only():
    p = arc_source.generator_dsn_params({"GENERATOR_POSTGRES_PASSWORD": "x"})
    assert (p["host"], p["port"], p["dbname"], p["user"]) == ("127.0.0.1", 5455, "generation", "generation")
    assert "default_transaction_read_only=on" in p["options"]
    p = arc_source.generator_dsn_params({"GENERATOR_POSTGRES_PASSWORD": "x", "GENERATOR_POSTGRES_PORT": "6000",
                                         "GENERATOR_POSTGRES_HOST": "db", "GENERATOR_POSTGRES_DB": "g",
                                         "GENERATOR_POSTGRES_USER": "u"})
    assert (p["host"], p["port"], p["dbname"], p["user"]) == ("db", 6000, "g", "u")


class _FakeCursor:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, rows):
        self.cur = _FakeCursor(rows)
        self.rolled_back = self.committed = False

    def cursor(self):
        return self.cur

    def rollback(self):
        self.rolled_back = True

    def commit(self):  # pragma: no cover - must never be called
        self.committed = True


def test_load_run_with_fake_conn_is_read_only():
    run = arc_source.RunArtifacts.from_json(FIX / f"{OFFICE_RUN}.json")
    rows = [("arc_plan", None, run.arc_plan)] + [("tree", k, v) for k, v in run.trees.items()]
    conn = _FakeConn(rows)
    got = arc_source.load_run(conn, OFFICE_RUN)
    assert got.trees == run.trees
    sql, params = conn.cur.executed[0]
    assert sql.lstrip().upper().startswith("SELECT") and params == (OFFICE_RUN,)
    assert conn.rolled_back and not conn.committed


def test_load_run_unknown_run():
    with pytest.raises(arc_source.ArcSourceError):
        arc_source.load_run(_FakeConn([]), "nope")


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("GENERATOR_POSTGRES_PASSWORD"),
                    reason="GENERATOR_POSTGRES_PASSWORD not set (live generator DB)")
def test_live_load_matches_fixture():
    conn = arc_source.connect_generator()
    try:
        live = arc_source.load_run(conn, DND_RUN)
    finally:
        conn.close()
    fixture = arc_source.RunArtifacts.from_json(FIX / f"{DND_RUN}.json")
    assert [s["id"] for s in live.segments()] == [s["id"] for s in fixture.segments()]
    assert set(live.trees) == set(fixture.trees)
