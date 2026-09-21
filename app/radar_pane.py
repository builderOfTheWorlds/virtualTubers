#!/usr/bin/env python3
"""
radar_pane.py
Standalone display process for the tmux "Stats" pane. Reads the per-worker
model-performance metrics file `app/agent_metrics.py` writes (Contract A of
docs/tuber_base_layout_plan.md) and renders a REAL shaded 3D radar/spike
mesh via termgl (docs/panels.md's "3D rendering (termgl)" section),
refreshing on a short loop. Runs under the /opt/render3d Python 3.11 venv
(see Dockerfile) — termgl requires Python >=3.11, unlike the rest of this
app which targets the system python3.10.

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
stale data renders as a flat/zeroed mesh with a "STALE"/"no data" header
line instead (termgl draws only the mesh; the status header is a plain
`puts()` text row above it, same house style as before).

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
from mesh3d import build_radar_mesh


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

# (label, metrics_key, lo, hi, invert)
# invert=True means "lower is better" — the spike height fills based on
# (hi - value) instead of value, so e.g. a LOW error rate still reads as a
# tall/healthy spike.
METRIC_SPECS = [
    ("tok/s", "tokens_per_sec", 0, 50, False),
    ("latency", "avg_latency_s", 0, 10, True),
    ("context", "context_tokens", 0, 8000, False),
    ("err%", "error_rate_pct", 0, 100, True),
    ("uptime%", "uptime_pct", 0, 100, False),
    ("msgs", "messages_sent", 0, 200, False),
]

WIDTH = 37
HEIGHT = 20
FOV = 1.0
VIEW_ROT_X = 0.5
VIEW_ROT_Z_SPEED = 0.06  # radians/frame — slow idle rotation
VIEW_DIST = 2.2


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


# ── Fraction mapping (pure) ─────────────────────────────────────────────────
def _clamp01(x):
    return max(0.0, min(1.0, x))


def normalize(value, lo, hi, invert=False):
    """Map `value` in [lo, hi] to a 0..1 fraction, clamped, tolerant of
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


def metrics_to_fractions(metrics):
    """METRIC_SPECS order -> list of 0..1 fractions, for build_radar_mesh."""
    return [normalize(metrics.get(key, DEFAULT_METRICS.get(key)), lo, hi, invert)
            for _, key, lo, hi, invert in METRIC_SPECS]


# ── Rendering (imports termgl lazily — only the /opt/render3d Python 3.11
# venv has it; keeping this out of module-level imports means the pure
# metrics/normalize logic above stays unit-testable under the regular
# test venv without termgl installed) ──────────────────────────────────────
def render_radar_3d(ctx, camera, fractions, angle, stale=False, worker_id=None):
    """Draw one frame: the shaded 3D radar mesh + a plain-text status header."""
    import numpy as np
    import termgl as tgl
    from render3d_common import make_view, LitPixelShader, write_header_line

    stale_color = tgl.PixFmt(tgl.Idx(tgl.Color.RED))
    live_color = tgl.PixFmt(tgl.Idx(tgl.Color.GREEN, flags=tgl.FmtFlag.BOLD))

    trigs = build_radar_mesh(fractions)
    if len(trigs) > 0:
        view = make_view(rot_x=VIEW_ROT_X, rot_y=0.0, rot_z=angle, dist=VIEW_DIST)
        vertex_shader = tgl.VertexShaderSimple(np.matmul(camera, view))
        pixel_shader = LitPixelShader()
        pixel_shader.base_color = stale_color if stale else live_color
        for trig in trigs:
            pixel_shader.trig = trig
            ctx.triangle_3d(trig, vertex_shader, pixel_shader)

    ctx.flush()
    ctx.clear(tgl.Buffer.FRAME | tgl.Buffer.Z | tgl.Buffer.OUTPUT)

    # Written AFTER flush(), via plain ANSI rather than ctx.puts() — see
    # write_header_line's docstring (DOUBLE_CHARS doubles puts() text too).
    label = (worker_id or "?")
    status = "STALE" if stale else "live"
    write_header_line(f"[{label}] stats ({status})",
                       color_name="RED" if stale else "GREEN", bold=not stale)


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


def _make_context_lazy():
    from render3d_common import make_context
    return make_context(WIDTH, HEIGHT)


def _make_camera_lazy():
    from render3d_common import make_camera
    return make_camera(WIDTH, HEIGHT, fov=FOV)


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

    ctx = _make_context_lazy()
    camera = _make_camera_lazy()
    angle = 0.0

    while True:
        metrics = load_metrics(metrics_path)
        stale = is_stale(metrics)
        fractions = metrics_to_fractions(metrics)
        render_radar_3d(ctx, camera, fractions, angle, stale=stale, worker_id=worker_id)
        angle += VIEW_ROT_Z_SPEED
        sleep(REFRESH_INTERVAL_S)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Radar/stats pane: live model-performance metrics as a 3D mesh.")
    parser.add_argument("--config", default="/config/worker.yaml",
                        help="worker.yaml providing worker_id (for metrics path derivation)")
    parser.add_argument("--metrics-path", default=None,
                        help="explicit metrics JSON path (overrides derived <runtime-dir>/metrics_<worker_id>.json)")
    args = parser.parse_args(argv)
    run(args.config, args.metrics_path)


if __name__ == "__main__":
    main()
