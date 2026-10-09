#!/usr/bin/env python3
"""scripts/table_ctl.py: operator control of the live table's GM, through message-api.

    .venv/bin/python scripts/table_ctl.py status
    .venv/bin/python scripts/table_ctl.py next [--force]
    .venv/bin/python scripts/table_ctl.py start <scene_id | index> [--force]
    .venv/bin/python scripts/table_ctl.py stop

Every command posts one message to the GM (default tuber_0) via message-api POST /messages
(it arrives from "operator"), then waits for the GM's `table_status` reply, which
message-logger stores and message-api serves at GET /logs/messages. Stdlib only.
Exit 0 on a started/stopped/status reply, 1 on busy/error, 2 when no reply arrived.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DEFAULT_URL = "http://localhost:8090"


def post(url, to, type_, payload):
    body = json.dumps({"to": to, "type": type_, "payload": payload}).encode()
    req = urllib.request.Request(f"{url}/messages", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def wait_reply(url, gm, request_id, since, timeout_s):
    query = urllib.parse.urlencode({"worker_id": gm, "since": since, "limit": 200})
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/logs/messages?{query}", timeout=10) as r:
                rows = json.load(r).get("messages", [])
        except Exception:  # noqa: BLE001 - logger lag or a transient error: keep polling
            rows = []
        # rows carry from/to/type/payload/timestamp only (no ids): take the newest
        # table_status the GM sent the operator since this request went out.
        replies = [r for r in rows if r.get("type") == "table_status" and r.get("from") == gm
                   and r.get("to") == "operator"]
        if replies:
            payload = replies[-1].get("payload")
            return json.loads(payload) if isinstance(payload, str) else (payload or {})
        time.sleep(1)
    return None


def build(args):
    if args.cmd == "status":
        return "table_status_request", {}
    if args.cmd == "stop":
        return "scene_stop", {}
    if args.cmd == "next":
        return "scene_request", {"next": True, "force": args.force}
    target = args.scene
    key = "index" if target.isdigit() else "scene_id"
    return "scene_request", {key: int(target) if key == "index" else target, "force": args.force}


def main(argv=None):
    p = argparse.ArgumentParser(description="Operator control of the live table GM.")
    p.add_argument("--url", default=DEFAULT_URL, help=f"message-api base URL (default {DEFAULT_URL})")
    p.add_argument("--gm", default="tuber_0", help="the GM worker id (default tuber_0)")
    p.add_argument("--timeout", type=float, default=20.0, help="seconds to wait for the GM's reply")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show the table state")
    for name, helptext in (("next", "start the next scene in the arc"),):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--force", action="store_true", help="end the running scene first")
    sp = sub.add_parser("start", help="start a scene by scene_id or index")
    sp.add_argument("scene")
    sp.add_argument("--force", action="store_true", help="end the running scene first")
    sub.add_parser("stop", help="skip the running scene")
    args = p.parse_args(argv)

    type_, payload = build(args)
    since = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    try:
        sent = post(args.url, args.gm, type_, payload)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not reach message-api at {args.url}: {exc}", file=sys.stderr)
        return 2
    reply = wait_reply(args.url, args.gm, sent.get("id"), since, args.timeout)
    if reply is None:
        print(f"sent {type_} (id {sent.get('id')}); no table_status reply within {args.timeout:.0f}s "
              "(is the GM running with role table_gm?)", file=sys.stderr)
        return 2
    print(json.dumps(reply, indent=2))
    return 1 if reply.get("result") in ("busy", "error") else 0


if __name__ == "__main__":
    sys.exit(main())
