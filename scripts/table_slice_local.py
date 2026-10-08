#!/usr/bin/env python3
"""scripts/table_slice_local.py: run the live-agent-table slice IN ONE PROCESS (P3.7 pre-flight).

Real pieces: vLLM (VLLMClient per agent), the character DB (local :5433; migrated
and loaded with campaigns/<pack>/profiles), the scene contracts (fixture), the
Arbiter, the GM runtime (agent_handlers.table_gm) and the seat handler
(agent_handlers.table). Replaced: Kafka (an in-memory bus that routes on `to`
exactly like MessageConsumer.poll_new: own worker id or "broadcast"), Redis
(in-memory arbiter state) and the containers.

Secrets are read from their files, never printed:
  VLLM_API_KEY          <- ~/environments/argyreServer/deployments/vllm-agents/.env
  CHARACTER_DB_PASSWORD <- deploy/character-profile-db/.env

    .venv/bin/python scripts/table_slice_local.py [--scenes 1] [--timeout 900] [--retake-once]
Writes a JSON record to utilities/benchmarker/output/table_slice_<ts>.json.
"""
import argparse
import json
import os
import pathlib
import sys
import time
from collections import deque

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "app"))

import logging  # noqa: E402

import yaml  # noqa: E402

logging.basicConfig(level=os.environ.get("SLICE_LOG", "INFO"), stream=sys.stderr,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

VLLM_ENV = pathlib.Path.home() / "environments/argyreServer/deployments/vllm-agents/.env"
DB_ENV = REPO / "deploy" / "character-profile-db" / ".env"


def _env_line(path, key):
    for line in pathlib.Path(path).read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} not found in {path}")


class Bus:
    """In-memory stand-in for Kafka: every send is logged and queued."""

    def __init__(self):
        self.queue = deque()
        self.log = []

    def send(self, msg):
        self.queue.append(msg)
        self.log.append({"t": round(time.monotonic() - T0, 2), "type": msg["type"],
                         "from": msg["from"], "to": msg["to"], "turn": msg.get("turn"),
                         "payload": msg["payload"]})
        return msg


def delivered_to(msg, worker_id):
    return msg.get("to") in (worker_id, "broadcast")


def main():
    global T0
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--retake-once", action="store_true",
                    help="corrupt the seat's first reply with an *action* to force one retake")
    ap.add_argument("--base-url", default="http://localhost:8092")
    ap.add_argument("--extra-body", default="", help="JSON merged into llm.extra_body (model-specific)")
    args = ap.parse_args()

    os.environ["VLLM_API_KEY"] = _env_line(VLLM_ENV, "VLLM_API_KEY")
    os.environ["CHARACTER_DB_PASSWORD"] = _env_line(DB_ENV, "CHARACTER_DB_PASSWORD")
    os.environ.setdefault("CHARACTER_DB_HOST", "127.0.0.1")
    os.environ.setdefault("CHARACTER_DB_PORT", _env_line(DB_ENV, "CHARACTER_DB_PORT"))

    from character import config as char_config, db as char_db
    from character.generator import pack_profiles
    from llm_client import build_llm_client
    from agent_handlers import table as seat_mod, table_gm
    from agent_handlers import MESSAGE_HANDLERS

    gm_doc = yaml.safe_load((REPO / "config/workers/table/gm.yaml").read_text())
    seat_doc = yaml.safe_load((REPO / "config/workers/table/chadwick.yaml").read_text())
    gm_cfg, seat_cfg = gm_doc["agent"], seat_doc["agent"]
    pack = REPO / "campaigns" / "ashiorid"
    for cfg in (gm_cfg, seat_cfg):                       # host paths instead of container mounts
        cfg["table"]["pack_dir"] = str(pack)
    gm_cfg["table"]["contracts_fixture"] = str(REPO / "tests/table/fixtures" /
                                               pathlib.Path(gm_cfg["table"]["contracts_fixture"]).name)
    gm_cfg["table"]["max_scenes"] = args.scenes
    seat_cfg["table"]["memory_dir"] = str(REPO / ".qwen_staging" / "slice_seat_memory")

    conn = char_db.connect(char_config.load())
    applied = char_db.migrate(conn)
    report = pack_profiles.load_pack(conn, pack / "profiles", pack / "cast", campaign="ashiorid")
    conn.commit()
    conn.close()
    print(f"[slice] migrations applied={applied} profiles={dict(report.actions)}")

    llm_cfg = {"llm": dict(gm_doc["llm"], base_url=args.base_url)}   # llm is top-level in worker yaml
    if args.extra_body:
        llm_cfg["llm"]["extra_body"] = {**(llm_cfg["llm"].get("extra_body") or {}),
                                        **json.loads(args.extra_body)}
    gm_llm = build_llm_client(llm_cfg)
    seat_llm = build_llm_client(llm_cfg)
    print(f"[slice] gm llm={gm_llm!r}")

    retake_state = {"armed": args.retake_once}
    real_speak_llm = seat_llm

    class RetakeOnce:
        """Wraps the seat client: the first SPEAK reply gets a stage direction prepended."""
        def complete_stream(self, system, messages, **kw):
            r, c = real_speak_llm.complete_stream(system, messages, **kw)
            if retake_state["armed"] and "Speak exactly one spoken line" in system:
                retake_state["armed"] = False
                c = "*draws his sword* " + c
            return r, c

    seat_client = RetakeOnce() if args.retake_once else seat_llm
    bus = Bus()
    T0 = time.monotonic()
    calls = []

    def timed(name, fn, *a, **k):
        t = time.monotonic()
        fn(*a, **k)
        calls.append({"what": name, "s": round(time.monotonic() - t, 2)})

    deadline = time.monotonic() + args.timeout
    last_tick = 0.0
    while time.monotonic() < deadline:
        if time.monotonic() - last_tick >= 0.5:
            timed("gm_tick", table_gm.table_gm_idle_tick, "tuber_0", gm_cfg, gm_llm, bus, None)
            last_tick = time.monotonic()
        while bus.queue:
            msg = bus.queue.popleft()
            handler = MESSAGE_HANDLERS.get(msg["type"])
            if not handler:
                continue
            if delivered_to(msg, "tuber_1"):
                timed(f"seat:{msg['type']}", handler, "tuber_1", seat_cfg, seat_client, bus, msg, None)
            if delivered_to(msg, "tuber_0"):
                timed(f"gm:{msg['type']}", handler, "tuber_0", gm_cfg, gm_llm, bus, msg, None)
        rt = table_gm.runtime_for("tuber_0")
        resolved = [m for m in bus.log if m["type"] == "scene_resolve"]
        if len(resolved) >= args.scenes or (rt and rt.arbiter is None and rt.index >= len(rt.contracts)):
            break
        time.sleep(0.05)

    wall = time.monotonic() - T0
    out = REPO / "utilities/benchmarker/output" / f"table_slice_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"wall_s": round(wall, 1), "messages": bus.log, "calls": calls},
                              indent=1, default=str))
    print(f"[slice] wall={wall:.1f}s messages={len(bus.log)} -> {out}")
    for m in bus.log:
        p = m["payload"]
        text = p.get("text") or p.get("committed_text") or p.get("reason") or p.get("notes") or ""
        extra = f" verdict={p['verdict']}" if "verdict" in p else ""
        print(f"  {m['t']:7.1f}s {m['type']:<16} {m['from']}->{m['to']:<9} {m['turn'] or ''}{extra}  {str(text)[:150]}")
    return 0 if any(m["type"] == "scene_resolve" for m in bus.log) else 1


if __name__ == "__main__":
    T0 = time.monotonic()
    sys.exit(main())
