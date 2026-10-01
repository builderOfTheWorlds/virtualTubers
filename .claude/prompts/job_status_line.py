"""Print one status line for a generator job read as JSON on stdin.
Used by watch_job.sh; with --final, prints the result/error block instead."""
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception as exc:
    print("api-error", exc)
    sys.exit(0)

if "--final" in sys.argv:
    print("RESULT", json.dumps(d.get("result")), "ERROR", d.get("error"))
else:
    p = d.get("progress") or {}
    print(d.get("status"), f"{p.get('done', '-')}/{p.get('total', '-')}", p.get("last_node", "-"))
