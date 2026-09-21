#!/usr/bin/env python3
"""
radar_pane.py
Standalone display process for the tmux "Stats" pane. Reads the per-worker
model-performance metrics file `app/agent_metrics.py` writes (Contract A of
docs/tuber_base_layout_plan.md) and renders a small ASCII bar/radar plot of
the six tracked metrics, refreshing on a short loop. Plain print() loop (NOT
a full-screen TUI) so it renders correctly under xterm + ffmpeg capture,
matching every other pane's house style (see tail_bus.py, avatar.py).

Metrics file contract (frozen, written by agent_metrics.py — see docs/
tuber_base_layout_plan.md "A. Metrics file"):
    {
      "worker_id": "coder",
      "updated_at": "2026-09-21T19:00:00+00:00",
      "tokens_per_sec": 12.4,
      "avg_latency_s": 3.2,
      "context_tokens": 5400,
      "error_rate_pct": 4.5,
      "uptime_pct": 100.0,
      "messages_sent": 87
    }

This pane is deliberately DEFENSIVE about that file: a worker that just
started (no LLM call yet), a metrics writer that hasn't landed yet, or a
transient torn-read must never crash or exit the pane — missing/malformed/
stale data renders as zeros/placeholder with a clear "no data yet" /
"STALE" marker instead.

Path resolution for the metrics file (no `{runtime_dir}`/`{worker_id}`
placeholders exist in build_layout.py's substitution context — only
`{config_path}` and `{resolved_path}` are guaranteed, see build_layout.py's
`build_context`): this pane is invoked with just `--config {config_path}`
and derives the metrics path itself the same way agent_metrics.py writes
it — `<runtime-dir>/metrics_<worker_id>.json`, runtime-dir defaulting to
`/tmp/panes` (the same directory build_layout.py already writes resolved
pane configs to). `--metrics-path` is available to override this directly
(matching the plan's literal example command), and the env var
METRICS_PATH wins over both, per this project's env-over-file precedence
convention (see message_bus.resolve).
"""
import os
import sys
import time
import argparse
import logging
from datetime import datetime, timezone

import yaml

from message_bus import load_worker_config, resolve


logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("RADAR_PANE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s radar_pane %(message)s",
)
log = logging.getLogger("radar_pane")


DEFAULT_RUNTIME_DIR = "/tmp/panes"
REFRESH_INTERVAL_S = 3
STALE_AFTER_S = 60  # metrics file older than this reads as STALE, not live

DEFAULT_METRICS = {
    "worker_id": None,
    "updated_at": None,
    "tokens_per_sec": 0.0,
    "avg_latency_s": 0.0,
    "context_tokens": 0,
    "error_rate_pct": 0.0,
    "uptime_pct": 0.0,
    "messages_sent": 0,
}

# (label, metrics_key, lo, hi, invert, unit)
# invert=True means "lower is better" — the bar fills based on (hi - value).
METRIC_SPECS = [
    ("tok/s", "tokens_per_sec", 0, 50, False, ""),
    ("latency", "avg_latency_s", 0, 10, True, "s"),
    ("context", "context_tokens", 0, 8000, False, ""),
    ("err%", "error_rate_pct", 0, 100, True, "%"),
    ("uptime%", "uptime_pct", 0, 100, False, "%"),
    ("msgs", "messages_sent", 0, 200, False, ""),
]

BAR_WIDTH = 10
FILLED = "█"
EMPTY = "░"


# ── Metrics loading (pure, defensive) ──────────────────────────────────────
def load_metrics(path):
    """Read the metrics JSON file, tolerant of missing/malformed/torn data.

    Never raises. Returns DEFAULT_METRICS (copy) merged with whatever valid
    keys were present, so a partial/older-shaped file still renders sane
    values for the fields it has.
    """
    import json

    merged = dict(DEFAULT_METRICS)
    if not path or not os.path.exists(path):
        log.debug("metrics file missing path=%s", path)
        return merged
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("metrics file unreadable/malformed path=%s err=%s", path, exc)
        return merged
    if not isinstance(raw, dict):
        log.debug("metrics file did not contain an object path=%s", path)
        return merged
    for key in DEFAULT_METRICS:
        if key in raw:
            merged[key] = raw[key]
    return merged


def is_stale(metrics, now=None, max_age_s=STALE_AFTER_S):
    """True if `updated_at` is missing, unparseable, or older than max_age_s."""
    now = now if now is not None else datetime.now(timezone.utc)
    updated_at = metrics.get("updated_at")
    if not updated_at:
        return True
    try:
        dt = datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() > max_age_s


# ── Rendering (pure) ────────────────────────────────────────────────────────
def _clamp01(x):
    return max(0.0, min(1.0, x))


def normalize(value, lo, hi, invert=False):
    """Map `value` in [lo, hi] to a 0..1 fill fraction, clamped, tolerant of
    non-numeric input (renders as 0)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if hi == lo:
        return 0.0
    frac = (value - lo) / (hi - lo)
    if invert:
        frac = 1.0 - frac
    return _clamp01(frac)


def render_bar(frac, width=BAR_WIDTH):
    """Render a `frac` (0..1) fill as a block-character bar of `width` cols."""
    frac = _clamp01(frac)
    filled_n = round(frac * width)
    return FILLED * filled_n + EMPTY * (width - filled_n)


def _format_value(value, unit):
    if isinstance(value, float):
        text = f"{value:.1f}"
    else:
        text = str(value)
    return f"{text}{unit}"


def render_radar(metrics, stale=False, worker_id=None):
    """Render the full compact stats box as a single multi-line string.

    Reads fine at ~20% terminal width: label + 10-char bar + value, six rows,
    plus a status header line.
    """
    lines = []
    label = worker_id or metrics.get("worker_id") or "?"
    status = "STALE" if stale else "live"
    lines.append(f"[{label}] stats ({status})")
    for name, key, lo, hi, invert, unit in METRIC_SPECS:
        value = metrics.get(key, DEFAULT_METRICS.get(key))
        frac = normalize(value, lo, hi, invert)
        bar = render_bar(frac)
        value_text = _format_value(value, unit)
        lines.append(f"{name:<8}{bar} {value_text}")
    return "\n".join(lines)


# ── Path resolution ──────────────────────────────────────────────────────────
def resolve_metrics_path(worker_config, explicit_path=None):
    """explicit_path (CLI flag) > METRICS_PATH env > derived
    `<runtime-dir>/metrics_<worker_id>.json`, matching agent_metrics.py's
    write location (Contract A)."""
    env_path = os.environ.get("METRICS_PATH")
    if env_path:
        return env_path
    if explicit_path:
        return explicit_path

    worker_config = worker_config or {}
    bus_config = worker_config.get("message_bus", {}) or {}
    worker_id = resolve("WORKER_ID", bus_config.get("worker_id"), "worker")
    runtime_dir = os.environ.get("METRICS_RUNTIME_DIR") or DEFAULT_RUNTIME_DIR
    return os.path.join(runtime_dir, f"metrics_{worker_id}.json")


# ── Run loop ──────────────────────────────────────────────────────────────
def run(config_path, metrics_path_arg, sleep=time.sleep):
    worker_config = {}
    if config_path and os.path.exists(config_path):
        try:
            worker_config = load_worker_config(config_path) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error("failed to load config %s: %s", config_path, exc)

    metrics_path = resolve_metrics_path(worker_config, metrics_path_arg)
    bus_config = worker_config.get("message_bus", {}) or {}
    worker_id = resolve("WORKER_ID", bus_config.get("worker_id"), "worker")
    log.info("worker_id=%s metrics_path=%s", worker_id, metrics_path)

    while True:
        metrics = load_metrics(metrics_path)
        stale = is_stale(metrics)
        os.system("clear")
        print(render_radar(metrics, stale=stale, worker_id=worker_id), flush=True)
        sleep(REFRESH_INTERVAL_S)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Radar/stats pane: live model-performance metrics.")
    parser.add_argument("--config", default="/config/worker.yaml",
                        help="worker.yaml providing worker_id (for metrics path derivation)")
    parser.add_argument("--metrics-path", default=None,
                        help="explicit metrics JSON path (overrides derived <runtime-dir>/metrics_<worker_id>.json)")
    args = parser.parse_args(argv)
    run(args.config, args.metrics_path)


if __name__ == "__main__":
    main()
