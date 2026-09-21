#!/usr/bin/env python3
"""
thinking_pane.py
Standalone display process for the tmux "Thinking" pane. Consumes the
`agent_thinking` bus message (Contract B of docs/tuber_base_layout_plan.md,
published by app/agent_metrics.py's InstrumentedLLMClient wrapper) and
prints each one as it arrives — plain scrolling text, no TUI, matching
tail_bus.py's house style.

Unlike tail_bus.py (which shows every worker's traffic), this pane pins the
CURRENT worker's own reasoning stream only: it filters to
`type == agent_thinking AND from == <this worker's WORKER_ID>`, so each
worker's thinking pane shows only that worker's own chain-of-thought, not
the whole cast's.

Contract B payload shape:
    {"from": "coder", "to": "broadcast", "type": "agent_thinking",
     "payload": {"text": "<trimmed thinking block content>"}}
"""
import os
import sys
import time
import argparse
import logging
from datetime import datetime, timezone

import yaml

from message_bus import load_worker_config, resolve
from tail_bus import connect_with_retry


logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("THINKING_PANE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s thinking_pane %(message)s",
)
log = logging.getLogger("thinking_pane")

TIMESTAMP_FORMAT = "%H:%M:%S"


# ── Filtering + formatting (pure) ──────────────────────────────────────────
def is_own_thinking(msg, worker_id):
    """True if `msg` is an agent_thinking message from this worker's own id."""
    return msg.get("type") == "agent_thinking" and msg.get("from") == worker_id


def format_thinking_line(msg, ts_format=TIMESTAMP_FORMAT):
    """Render one agent_thinking message as a plain scrolling line.

    Missing/empty text renders as an empty string (skip-worthy) so the
    caller can decide whether to print it; this function never raises.
    """
    ts_raw = msg.get("timestamp")
    ts = ts_raw
    if ts_raw:
        try:
            dt = datetime.fromisoformat(ts_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.astimezone().strftime(ts_format)
        except (TypeError, ValueError):
            ts = str(ts_raw)
    else:
        ts = "??:??:??"

    text = (msg.get("payload") or {}).get("text", "")
    return f"[{ts}] {text}"


# ── Run loop (Kafka; guarded so the module imports cleanly for tests) ──────
def run(bus_config_path):
    log.debug("run bus_config=%s", bus_config_path)

    try:
        worker_config = load_worker_config(bus_config_path)
    except (OSError, yaml.YAMLError) as exc:
        log.error("failed to load bus-config %s: %s", bus_config_path, exc)
        worker_config = {}
    bus_config = (worker_config or {}).get("message_bus", {}) or {}

    worker_id = resolve("WORKER_ID", bus_config.get("worker_id"), "worker")
    bootstrap_servers = resolve("KAFKA_BOOTSTRAP_SERVERS", bus_config.get("bootstrap_servers"))
    topic = resolve("KAFKA_TOPIC", bus_config.get("topic"))
    log.info("worker_id=%s bootstrap=%s topic=%s", worker_id, bootstrap_servers, topic)

    print(f"Thinking ({worker_id})...", flush=True)

    consumer = connect_with_retry(
        bootstrap_servers, topic, group_id=f"vtuber-thinking-{worker_id}"
    )

    while True:
        try:
            for msg in consumer.poll_new(to_filter=False):
                if not is_own_thinking(msg, worker_id):
                    continue
                line = format_thinking_line(msg)
                if not line.strip():
                    continue
                print(line, flush=True)
        except Exception as exc:  # noqa: BLE001 — keep the pane alive on transient errors
            log.error("error while polling/formatting: %s", exc)
            time.sleep(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Per-worker agent_thinking display feed.")
    parser.add_argument(
        "--bus-config",
        default="/config/worker.yaml",
        help="worker.yaml providing Kafka connection + worker_id",
    )
    args = parser.parse_args(argv)
    run(args.bus_config)


if __name__ == "__main__":
    main()
