#!/usr/bin/env python3
"""Export a generator run's table story (arc_plan + brief + tree) as a contracts fixture.

    python3 scripts/export_table_fixture.py <run_id> [--out tests/table/fixtures/<run_id>.json]

The GM container reads the fixture via table.contracts_fixture (mounted at
/data/table/fixtures), so the live table needs no generator-DB credentials.
Reads the GENERATOR Postgres (generation @ 127.0.0.1:5455) through
`docker exec virtualtubers-generator-postgres-1 psql`, read-only, so no
password leaves the container. Then validates the fixture with the same code
the GM uses (table.arc_source + table.contract) and prints a contract summary.
"""
import argparse
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "app"))

CONTAINER = "virtualtubers-generator-postgres-1"
SQL = ("SELECT coalesce(json_agg(json_build_object('kind', kind, 'segment_id', segment_id, "
       "'content', content) ORDER BY kind, segment_id), '[]'::json) "
       "FROM generation_artifacts WHERE pack = '{run}' AND kind IN ('arc_plan', 'brief', 'tree')")


def fetch(run_id):
    if not all(c.isalnum() or c in "_-" for c in run_id):
        raise SystemExit(f"bad run id {run_id!r}")
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "sh", "-c",
         'psql -U "${POSTGRES_USER:-generation}" -d "${POSTGRES_DB:-generation}" -At '
         '-v ON_ERROR_STOP=1 -c "$0"', SQL.format(run=run_id)],
        check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_id")
    ap.add_argument("--out")
    ap.add_argument("--table-config", default=str(REPO / "config/table/ashiorid.yaml"),
                    help="seat map used to validate the contracts (default: the D&D table)")
    args = ap.parse_args()
    artifacts = fetch(args.run_id)
    for a in artifacts:
        if isinstance(a["content"], str):
            a["content"] = json.loads(a["content"])
    out = pathlib.Path(args.out or REPO / "tests/table/fixtures" / f"{args.run_id}.json")
    out.write_text(json.dumps({"run_id": args.run_id, "artifacts": artifacts}, indent=1))

    import yaml
    sys.path.insert(0, str(REPO / "scripts"))
    from build_table_configs import _seat_of
    from table import arc_source, contract
    tcfg = yaml.safe_load(pathlib.Path(args.table_config).read_text())
    seat_of = _seat_of(tcfg["seats"])
    arts = arc_source.RunArtifacts.from_json(out)
    segs = arts.segments()
    contracts = contract.contracts_for_run(arts, seat_of=seat_of,
                                           pack_cast=[s["slug"] for s in tcfg["seats"]])
    players = {s["seat"] for s in tcfg["seats"]} - {tcfg["gm_seat"]}
    playable = [c for c in contracts if players & set(c.to_payload().get("participants", []))]
    warnings = getattr(contracts, "warnings", [])
    missing = [s["id"] for s in segs if s["id"] not in arts.trees]
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    print(f"segments={len(segs)} briefs={len(arts.briefs)} trees={len(arts.trees)} "
          f"contracts={len(contracts)} with_players={len(playable)} warnings={len(warnings)} "
          f"segments_without_tree={missing}")
    for c in playable[:3]:
        p = c.to_payload()
        print("  e.g.", p["scene_id"], "|", p["participants"], "|", (p.get("canon_goal") or "")[:140])
    for w in warnings[:15]:
        print("  warn:", w)


if __name__ == "__main__":
    main()
