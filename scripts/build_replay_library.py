#!/usr/bin/env python3
"""
build_replay_library.py
Batch-parses claudeBackupUtility session logs into an episode library for
the "Rerun Theater" replay pane (docs/replay_pane.md).

Run on the machine that has the logs (the Windows dev box), then sync the
output directory to the deployment host at /opt/virtualTubers/replays —
docker-compose mounts it read-only into the workers at /data/replays.

    .venv/Scripts/python.exe scripts/build_replay_library.py \
        --logs "path/to/logs/claude/virtualTubers" --out replays

Skips sessions that produce fewer than --min-events events (nothing
watchable in them). Redaction happens inside the parser; this script also
runs the same strict leak audit the test suite uses and refuses to write
any episode that fails it.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from session_log_parser import parse_session  # noqa: E402

# Strict last-line-of-defense audit — a leaked episode must never reach a
# broadcastable library. Private LAN IPs (192.168.x etc.) are allowed by
# policy; tailnet (100.x) and credential-looking assignments are not.
# The password arm tolerates JSON escaping (password\": \"...) and only
# fires when the value is NOT the parser's [password] dummy marker.
LEAK_AUDIT = re.compile(
    r"frogg|sk-ant-[A-Za-z0-9_-]{8}|ghp_[A-Za-z0-9]{8}|100\.\d{1,3}\.\d"
    r"|(?i:\w*(?:password|passwd|passphrase|pwd|secret)(?:\\?[\"'])*\s*[:=]>?\s*(?:\\?[\"'])*"
    r"(?!\[password\])[^\s\\\"',;&|=\[])"
)


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
        leak = LEAK_AUDIT.search(payload)
        if leak:
            print(f"  LEAK  {session_dir.name}: {leak.group(0)!r} — NOT writing")
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
