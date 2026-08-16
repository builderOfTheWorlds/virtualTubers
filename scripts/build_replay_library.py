#!/usr/bin/env python3
"""
build_replay_library.py
Batch-parses claudeBackupUtility session logs into an episode library for
the "Rerun Theater" replay pane (docs/replay_pane.md).

Run on the machine that has the logs (the Windows dev box), then upload the
episodes to the running stack, which validates each one and stores it in
Postgres (docs/episode_store.md) — the workers read the library from there,
not from a mounted directory:

    .venv/Scripts/python.exe scripts/build_replay_library.py \
        --logs "path/to/logs/claude/virtualTubers" --out replays

    for f in replays/*.json; do
        curl -sS -X POST http://<host>:8090/replays \
            -H 'Content-Type: application/json' --data-binary @"$f"
    done

Skips sessions that produce fewer than --min-events events (nothing
watchable in them). Redaction happens inside the parser; this script also
runs the same strict leak audit the server applies on upload
(session_log_parser.audit) and refuses to write any episode that fails it.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from session_log_parser import audit, parse_session  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Parse session logs into a replay episode library")
    parser.add_argument("--logs", required=True, help="Directory of <timestamp>_<id> session log dirs")
    parser.add_argument("--out", default="replays", help="Episode library output directory")
    parser.add_argument("--min-events", type=int, default=5,
                        help="Skip sessions with fewer performable events (default 5)")
    args = parser.parse_args()

    logs = Path(args.logs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    written, skipped, failed = 0, 0, 0
    for session_dir in sorted(p for p in logs.iterdir() if p.is_dir()):
        try:
            script = parse_session(session_dir)
        except Exception as exc:
            print(f"  FAIL  {session_dir.name}: {exc}")
            failed += 1
            continue
        if len(script["events"]) < args.min_events:
            skipped += 1
            continue
        payload = json.dumps(script, indent=1, ensure_ascii=False)
        leak = audit(payload)
        if leak:
            print(f"  LEAK  {session_dir.name}: {leak!r} — NOT writing")
            failed += 1
            continue
        (out / f"{session_dir.name}.json").write_text(payload, encoding="utf-8")
        print(f"  ok    {session_dir.name}: {len(script['events'])} events")
        written += 1

    print(f"[build_replay_library] wrote {written} episode(s) to {out} "
          f"(skipped {skipped} thin, {failed} failed/leaked)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
