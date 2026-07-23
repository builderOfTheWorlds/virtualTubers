#!/usr/bin/env python3
"""
tail_bus.py
Standalone display process for the tmux "Message Bus" pane. Consumes every
message on the bus (not just ones addressed to this worker) and prints a rich,
configurable, aligned feed line — one per message — using plain print() so it
renders correctly under xterm + ffmpeg capture (NO full-screen TUI).

Configuration comes from two files:
  --bus-config   worker.yaml providing Kafka connection + worker_id.
  --feed-config  resolved kafka_feed panel yaml (its `content:` block controls
                 colors / filters / highlight / payload / timestamp / header).

Both are optional-friendly: sane built-in defaults keep the pane running even
if a config file is missing or malformed. Environment variables override file
values for Kafka connection (KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC, WORKER_ID).

The formatting and filtering helpers below are pure functions with no Kafka
dependency so they can be unit-tested directly.
"""
import os
import sys
import time
import argparse
import logging
from datetime import datetime, timezone

import yaml

from message_bus import load_worker_config, MessageConsumer, BROADCAST, resolve


# Logs go to STDERR only — stdout is the display feed and must stay clean.
logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("TAIL_BUS_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s tail_bus %(message)s",
)
log = logging.getLogger("tail_bus")


# ── ANSI color helpers ────────────────────────────────────────────────────────
# Simple, dependency-free ANSI. The xterm capture is a real tty, so codes are
# emitted unconditionally; they degrade to inert escape sequences elsewhere.
ANSI_CODES = {
    "black": "30",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "gray": "90",
    "grey": "90",
}
RESET = "\033[0m"


def colorize(text, color):
    """Wrap text in an ANSI color. Unknown/empty color returns text unchanged."""
    code = ANSI_CODES.get((color or "").lower())
    if not code:
        return text
    return f"\033[{code}m{text}{RESET}"


# ── Config defaults + loading ─────────────────────────────────────────────────
DEFAULT_FEED_CONFIG = {
    "colors": {
        "coder": "green",
        "manager": "yellow",
        "tester": "magenta",
        "broadcast": "cyan",
        "operator": "blue",
    },
    "filters": {
        # NOTE: agent.py publishes its per-tick heartbeat flood as type
        # "status_update" (payload {"text": "heartbeat #N"}), NOT "heartbeat".
        # Both are hidden by default so the feed is not flooded. This list is
        # fully configurable via the feed config's filters.hide_types.
        "hide_types": ["heartbeat", "status_update"],
        "show_types": [],       # empty = show all except hidden; else whitelist
        "direction": "all",     # all | broadcast | to:<id> | from:<id>
    },
    "highlight": {
        "task_complete": "green",
        "clarification_request": "yellow",
        "error": "red",
        "bug_report": "red",
        "test_passed": "green",
        "manager_report": "cyan",
        "operator_reply": "blue",
    },
    "payload": {"mode": "pretty", "max_chars": 80},   # pretty | raw | hidden
    "timestamp": {"format": "%H:%M:%S", "local": True},
    "header": True,
}




def load_feed_config(path):
    """Load the feed content block, tolerant of missing files / shapes.

    Accepts a file whose top level is either the panel definition (with a
    `content:` sub-tree) or the content block itself. Missing keys fall back
    to DEFAULT_FEED_CONFIG. Never raises for a bad path — returns defaults.
    """
    log.debug("load_feed_config path=%s", path)
    if not path:
        log.debug("no feed-config supplied; using built-in defaults")
        return _merge_feed_config({})
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.error("failed to read feed-config %s: %s; using defaults", path, exc)
        return _merge_feed_config({})

    # Tolerate both a full panel file and a bare content block.
    content = raw.get("content", raw) if isinstance(raw, dict) else {}
    return _merge_feed_config(content)


def _merge_feed_config(content):
    """Deep-ish merge of a content block over DEFAULT_FEED_CONFIG (one level)."""
    merged = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
              for k, v in DEFAULT_FEED_CONFIG.items()}
    if not isinstance(content, dict):
        return merged
    for key, default_val in DEFAULT_FEED_CONFIG.items():
        if key not in content:
            continue
        val = content[key]
        if isinstance(default_val, dict) and isinstance(val, dict):
            merged[key] = {**default_val, **val}
        else:
            merged[key] = val
    return merged


# ── Filtering (pure) ──────────────────────────────────────────────────────────
def passes_filters(msg, filters):
    """Return True if a message should be displayed given the filters block.

    Order: hide_types, then show_types (whitelist if non-empty), then direction.
    """
    msg_type = msg.get("type")
    hide_types = filters.get("hide_types") or []
    if msg_type in hide_types:
        return False

    show_types = filters.get("show_types") or []
    if show_types and msg_type not in show_types:
        return False

    direction = (filters.get("direction") or "all").strip()
    if direction and direction != "all":
        if direction == "broadcast":
            if msg.get("to") != BROADCAST:
                return False
        elif direction.startswith("to:"):
            if msg.get("to") != direction[3:]:
                return False
        elif direction.startswith("from:"):
            if msg.get("from") != direction[5:]:
                return False
        # Unknown direction spec is treated as "all" (permissive).
    return True


# ── Formatting (pure) ─────────────────────────────────────────────────────────
def format_timestamp(iso_ts, ts_config):
    """Format an ISO-8601 UTC timestamp per config (local conversion optional)."""
    fmt = (ts_config or {}).get("format", "%H:%M:%S")
    local = (ts_config or {}).get("local", True)
    try:
        dt = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return str(iso_ts)
    if local:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone()
    return dt.strftime(fmt)


def format_payload(payload, payload_config):
    """Render a payload dict per mode (pretty|raw|hidden) truncated to max_chars."""
    mode = (payload_config or {}).get("mode", "pretty")
    max_chars = (payload_config or {}).get("max_chars", 80)

    if mode == "hidden":
        return ""

    if mode == "raw":
        text = repr(payload)
    else:  # pretty
        if isinstance(payload, dict):
            text = ", ".join(f"{k}={v}" for k, v in payload.items())
        else:
            text = str(payload)

    if isinstance(max_chars, int) and max_chars > 0 and len(text) > max_chars:
        if max_chars <= 1:
            text = text[:max_chars]
        else:
            text = text[: max_chars - 1] + "…"  # ellipsis
    return text


# Column widths for aligned output.
SENDER_WIDTH = 10
TO_WIDTH = 10
TYPE_WIDTH = 22


def format_line(msg, feed_config):
    """Render one message dict into a colorized, aligned feed line.

    Layout: ``HH:MM:SS from ──▶ to  type  payload``
    - sender colored per feed_config['colors'] keyed by the `from` value
    - type column colored per feed_config['highlight'] when it matches
    - payload rendered per feed_config['payload']
    """
    colors = feed_config.get("colors", {})
    highlight = feed_config.get("highlight", {})

    ts = format_timestamp(msg.get("timestamp"), feed_config.get("timestamp", {}))

    sender = str(msg.get("from", ""))
    to = str(msg.get("to", ""))
    msg_type = str(msg.get("type", ""))

    sender_cell = colorize(sender.ljust(SENDER_WIDTH), colors.get(sender))
    to_cell = to.ljust(TO_WIDTH)

    type_padded = msg_type.ljust(TYPE_WIDTH)
    type_color = highlight.get(msg_type)
    type_cell = colorize(type_padded, type_color) if type_color else type_padded

    payload_text = format_payload(msg.get("payload", {}), feed_config.get("payload", {}))

    line = f"{ts} {sender_cell} ──▶ {to_cell} {type_cell} {payload_text}"
    return line.rstrip()


def format_header():
    """One-time column header line (plain text, dim)."""
    ts = "TIME".ljust(8)
    sender = "FROM".ljust(SENDER_WIDTH)
    to = "TO".ljust(TO_WIDTH)
    type_col = "TYPE".ljust(TYPE_WIDTH)
    header = f"{ts} {sender}     {to} {type_col} PAYLOAD"
    return colorize(header, "gray")


CONNECT_RETRY_BASE_SECONDS = 3
CONNECT_RETRY_MAX_SECONDS = 30


def connect_with_retry(bootstrap_servers, topic, group_id, sleep=time.sleep):
    """Build a MessageConsumer, retrying with backoff instead of raising.

    The pane process (unlike agent.py) isn't restarted by Docker on crash — only
    the whole container is — so a transient bootstrap timeout must not kill this
    process and drop the tmux pane to a bare shell. Retries forever.
    """
    delay = CONNECT_RETRY_BASE_SECONDS
    while True:
        try:
            return MessageConsumer(bootstrap_servers, topic, group_id=group_id)
        except Exception as exc:  # noqa: BLE001 — must not crash the pane on connect
            log.error("failed to connect Kafka consumer: %s; retrying in %ss", exc, delay)
            print(f"Message bus unreachable ({exc}); retrying in {delay}s...", flush=True)
            sleep(delay)
            delay = min(delay * 2, CONNECT_RETRY_MAX_SECONDS)


# ── Run loop (Kafka; guarded so the module imports cleanly for tests) ──────────
def run(bus_config_path, feed_config_path):
    log.debug("run bus_config=%s feed_config=%s", bus_config_path, feed_config_path)

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

    feed_config = load_feed_config(feed_config_path)

    if feed_config.get("header"):
        print(format_header(), flush=True)

    print("Waiting for message bus...", flush=True)

    consumer = connect_with_retry(
        bootstrap_servers, topic, group_id=f"vtuber-display-{worker_id}"
    )

    filters = feed_config.get("filters", {})
    while True:
        try:
            for msg in consumer.poll_new(to_filter=False):
                if not passes_filters(msg, filters):
                    log.debug("filtered out type=%s to=%s", msg.get("type"), msg.get("to"))
                    continue
                print(format_line(msg, feed_config), flush=True)
        except Exception as exc:  # noqa: BLE001 — keep the feed alive on transient errors
            log.error("error while polling/formatting: %s", exc)
            time.sleep(1)  # avoid a tight retry loop if the broker is down


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kafka message-bus display feed.")
    parser.add_argument(
        "--bus-config",
        default="/config/worker.yaml",
        help="worker.yaml providing Kafka connection + worker_id",
    )
    parser.add_argument(
        "--feed-config",
        default=None,
        help="resolved kafka_feed panel yaml (its content: block); optional",
    )
    args = parser.parse_args(argv)
    run(args.bus_config, args.feed_config)


if __name__ == "__main__":
    main()
