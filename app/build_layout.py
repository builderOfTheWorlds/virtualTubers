#!/usr/bin/env python3
"""
build_layout.py
Config-driven tmux layout engine for the worker container.

Replaces the hardcoded tmux block that used to live in ``startup.sh``. Given a
worker config, it:

  1. Determines the selected layout preset (``layout.preset`` / ``layout: <name>``
     in the worker config, env-overridable via ``LAYOUT_PRESET``).
  2. Loads ``config/layouts/<preset>.yaml`` and, for every pane it lists, the
     matching panel-type default from ``config/panels/<use>.yaml``.
  3. Resolves each pane by merging (later wins):
        panel-type default -> layout placement/overrides (incl. ``with:``)
        -> worker-config per-pane override
  4. Emits, on STDOUT, a shell-evaluable sequence of ``tmux`` commands that
     reproduces the layout (``new-session`` + ordered ``split-window`` /
     ``select-pane`` / ``set pane-border-format`` / per-pane border color /
     ``send-keys``). ``startup.sh`` runs ``eval "$(python3 build_layout.py ...)"``.
  5. Writes each pane's fully-resolved config dict to ``<runtime-dir>/<id>.yaml``
     so pane processes (e.g. tail_bus.py) read a single source of truth.

STDOUT is reserved for the tmux script that gets eval'd — ALL logging goes to
STDERR. Nothing else may be printed to stdout.

Geometry / ``size`` semantics
-----------------------------
Each pane (except the base pane) is created by splitting a *target* pane. A
pane's ``size`` is the percentage handed to ``tmux split-window -p`` — tmux
sizes the NEWLY-CREATED pane to that percentage. The base pane has no split;
its ``size`` is informational. ``target`` names the ``use`` of the pane to split
from; when omitted it defaults to the base (first) pane.

Placeholder substitution
-------------------------
Any ``{name}`` token in a pane's ``command`` is substituted from a context that
always includes:
  * ``{config_path}``   — the ``--config`` value passed to this engine.
  * ``{resolved_path}`` — the ``<runtime-dir>/<id>.yaml`` file written for that pane.
plus every scalar key of the resolved pane dict. This is what lets the
kafka_feed panel-type template
  ``python3 /app/tail_bus.py --bus-config {config_path} --feed-config {resolved_path}``
resolve to the agreed CLI contract.
"""
import os
import sys
import argparse
import logging
from pathlib import Path

import yaml


# ── Logging (STDERR only — stdout is the tmux script) ─────────────────────────
logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("BUILD_LAYOUT_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s build_layout %(message)s",
)
log = logging.getLogger("build_layout")

# CLAUDE.md asks for TRACE — Python has no TRACE level; map it just below DEBUG.
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


# ── Defaults (work both in-container: /app,/config and for a local dry-run) ───
SESSION_NAME = "worker"
DEFAULT_COLS = 240
DEFAULT_ROWS = 67


def _default_dir(name):
    """Pick an existing config dir: prefer in-container /config/<name>, then
    the repo-relative config/<name> (for local dry-runs)."""
    in_container = Path("/config") / name
    if in_container.is_dir():
        return str(in_container)
    return str(Path("config") / name)


# ── Config loading ────────────────────────────────────────────────────────────
def load_yaml(path):
    """Load a YAML file into a dict. Raises on missing/unreadable/malformed."""
    _trace("load_yaml path=%s", path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        log.error("failed to load YAML %s: %s", path, exc)
        raise
    return data or {}


def select_preset(worker_config):
    """Determine the layout preset name from the worker config, env-overridable.

    Supports both ``layout: {preset: coder}`` and the shorthand ``layout: coder``.
    ``LAYOUT_PRESET`` env var wins over the file value.
    """
    layout = worker_config.get("layout")
    file_preset = None
    if isinstance(layout, dict):
        file_preset = layout.get("preset")
    elif isinstance(layout, str):
        file_preset = layout

    preset = os.environ.get("LAYOUT_PRESET") or file_preset or "coder"
    log.debug("selected layout preset=%s (file=%s env=%s)",
              preset, file_preset, os.environ.get("LAYOUT_PRESET"))
    return preset


def _worker_pane_overrides(worker_config):
    """Per-pane overrides from the worker config's ``layout.panes`` mapping.

    Shape: ``layout: {preset: coder, panes: {editor: {command: "vim"}, ...}}``.
    Keyed by pane ``id`` (which defaults to the panel ``use``). Returns {} if none.
    """
    layout = worker_config.get("layout")
    if isinstance(layout, dict):
        panes = layout.get("panes")
        if isinstance(panes, dict):
            return panes
    return {}


# ── Merge ─────────────────────────────────────────────────────────────────────
def deep_merge(base, override):
    """Return a new dict = base with override applied (nested dicts merged,
    scalars/lists replaced). Neither input is mutated."""
    result = dict(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def resolve_pane(placement, panels_dir, worker_overrides):
    """Resolve one pane: panel-type default -> layout placement/overrides
    (incl. ``with:``) -> worker per-pane override.

    Returns the merged pane dict, or None if disabled.
    """
    use = placement.get("use")
    pane_id = placement.get("id", use)
    _trace("resolve_pane use=%s id=%s", use, pane_id)

    panel_path = Path(panels_dir) / f"{use}.yaml"
    try:
        panel_default = load_yaml(panel_path)
    except (OSError, yaml.YAMLError):
        log.error("pane '%s': cannot load panel type %s; skipping", pane_id, panel_path)
        return None

    resolved = dict(panel_default)

    # Layout placement contributes the universal knobs + free-form `with:`.
    placement_layer = {}
    for key in ("title", "border_color", "command", "split", "size",
                "target", "enabled", "id"):
        if key in placement:
            placement_layer[key] = placement[key]
    with_block = placement.get("with")
    if isinstance(with_block, dict):
        log.debug("pane '%s': applying with-block %s", pane_id, with_block)
        placement_layer = deep_merge(placement_layer, with_block)
    resolved = deep_merge(resolved, placement_layer)

    # Worker-config per-pane override wins over the layout.
    wo = worker_overrides.get(pane_id) or worker_overrides.get(use)
    if isinstance(wo, dict):
        log.debug("pane '%s': applying worker override %s", pane_id, wo)
        resolved = deep_merge(resolved, wo)

    resolved.setdefault("id", pane_id)
    resolved.setdefault("use", use)

    if resolved.get("enabled") is False:
        log.debug("pane '%s' disabled; omitting", pane_id)
        return None

    return resolved


# ── Placeholder substitution + runtime files ──────────────────────────────────
def build_context(resolved, config_path, resolved_path):
    """Substitution context for a pane's command: the pane's scalar fields plus
    the two contract placeholders."""
    ctx = {k: v for k, v in resolved.items()
           if isinstance(v, (str, int, float, bool))}
    ctx["config_path"] = config_path
    ctx["resolved_path"] = resolved_path
    return ctx


def substitute(template, context):
    """Substitute ``{name}`` tokens using ``context``. Unknown tokens are left
    intact (str.format_map with a defaulting mapping)."""
    if not isinstance(template, str):
        return template

    class _Default(dict):
        def __missing__(self, key):
            log.debug("command placeholder '{%s}' has no value; left intact", key)
            return "{" + key + "}"

    try:
        return template.format_map(_Default(context))
    except (ValueError, IndexError) as exc:
        log.error("failed to format command %r: %s", template, exc)
        return template


def write_runtime_config(resolved, runtime_dir):
    """Write a pane's resolved dict to ``<runtime-dir>/<id>.yaml``. Returns the path."""
    pane_id = resolved["id"]
    out_dir = Path(runtime_dir)
    out_path = out_dir / f"{pane_id}.yaml"
    _trace("write_runtime_config id=%s path=%s", pane_id, out_path)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(resolved, f, sort_keys=True, default_flow_style=False)
    except OSError as exc:
        log.error("failed to write runtime config %s: %s", out_path, exc)
        raise
    return str(out_path)


# ── tmux emission ─────────────────────────────────────────────────────────────
def _q(s):
    """Single-quote a string for safe embedding in the emitted shell script."""
    return "'" + str(s).replace("'", "'\\''") + "'"


def emit_tmux(panes, config_path, runtime_dir, cols=DEFAULT_COLS, rows=DEFAULT_ROWS):
    """Build the ordered list of tmux command lines that reproduce the layout.

    ``panes`` is the ordered list of resolved (enabled) pane dicts. Panes are
    laid out in list order: the first is the base pane, each subsequent pane is
    created by splitting its ``target`` pane (default: the base pane). tmux
    numbers panes 0..N in creation order, which is exactly the emission order,
    so ``id -> pane index`` is deterministic.
    """
    lines = []
    session = SESSION_NAME

    lines.append(f"tmux new-session -d -s {session} -x {cols} -y {rows}")

    # id -> tmux pane index, assigned as panes are created (base=0, then 1,2,...).
    index_of = {}
    if panes:
        index_of[panes[0]["id"]] = 0

    next_index = 1
    for pane in panes[1:]:
        target_use = pane.get("target")
        # Resolve target to a pane index: match by id/use of an already-placed pane.
        target_idx = 0
        if target_use is not None:
            target_idx = index_of.get(target_use, 0)
        else:
            # Default: split the base pane.
            target_idx = 0

        split_flag = "-h" if str(pane.get("split", "v")).lower() == "h" else "-v"
        size = pane.get("size")

        lines.append(f"tmux select-pane -t {session}:0.{target_idx}")
        if isinstance(size, int):
            lines.append(f"tmux split-window {split_flag} -t {session}:0.{target_idx} -p {size}")
        else:
            lines.append(f"tmux split-window {split_flag} -t {session}:0.{target_idx}")

        index_of[pane["id"]] = next_index
        next_index += 1

    # Titles (pane-border-format), border colors, and commands.
    lines.append(f"tmux set -t {session} pane-border-status top")
    for pane in panes:
        idx = index_of[pane["id"]]
        target = f"{session}:0.{idx}"

        title = pane.get("title")
        if title:
            lines.append(
                f"tmux select-pane -t {target} -T {_q(title)}"
            )

        border_color = pane.get("border_color")
        if border_color:
            # Per-pane border color: tmux select-pane -P sets this pane's border style.
            lines.append(
                f"tmux select-pane -t {target} -P {_q('fg=' + str(border_color))}"
            )

    # send-keys last, so all panes exist before any process starts.
    for pane in panes:
        idx = index_of[pane["id"]]
        target = f"{session}:0.{idx}"
        # The emitted script runs in the Linux container — join with forward
        # slashes regardless of the host OS this engine was invoked on.
        resolved_path = runtime_dir.rstrip("/\\") + "/" + f"{pane['id']}.yaml"
        command = pane.get("command")
        if not command:
            log.debug("pane '%s' has no command; not sending keys", pane["id"])
            continue
        ctx = build_context(pane, config_path, resolved_path)
        command = substitute(command, ctx)
        lines.append(f"tmux send-keys -t {target} {_q(command)} Enter")

    return lines


# ── Orchestration ─────────────────────────────────────────────────────────────
def build(config_path, panels_dir, layouts_dir, runtime_dir):
    """Resolve panes and return (tmux_lines, resolved_panes). Also writes each
    resolved pane's config to ``runtime_dir``."""
    _trace("build config=%s panels=%s layouts=%s runtime=%s",
           config_path, panels_dir, layouts_dir, runtime_dir)

    worker_config = load_yaml(config_path)
    preset = select_preset(worker_config)

    layout_path = Path(layouts_dir) / f"{preset}.yaml"
    layout = load_yaml(layout_path)
    placements = layout.get("panes") or []
    if not placements:
        log.error("layout preset %s has no panes", layout_path)

    worker_overrides = _worker_pane_overrides(worker_config)

    resolved_panes = []
    for placement in placements:
        pane = resolve_pane(placement, panels_dir, worker_overrides)
        if pane is None:
            continue
        # Persist the resolved config so pane processes read one source of truth.
        write_runtime_config(pane, runtime_dir)
        resolved_panes.append(pane)

    log.info("resolved %d panes for preset '%s'", len(resolved_panes), preset)

    lines = emit_tmux(resolved_panes, config_path, runtime_dir)
    return lines, resolved_panes


def main(argv=None):
    parser = argparse.ArgumentParser(description="Config-driven tmux layout engine.")
    parser.add_argument("--config", default="/config/worker.yaml",
                        help="worker.yaml selecting a layout preset")
    parser.add_argument("--panels-dir", default=None,
                        help="dir of panel-type yaml files (default: /config/panels or config/panels)")
    parser.add_argument("--layouts-dir", default=None,
                        help="dir of layout preset yaml files (default: /config/layouts or config/layouts)")
    parser.add_argument("--runtime-dir", default="/tmp/panes",
                        help="dir to write resolved per-pane configs (default: /tmp/panes)")
    args = parser.parse_args(argv)

    panels_dir = args.panels_dir or _default_dir("panels")
    layouts_dir = args.layouts_dir or _default_dir("layouts")

    try:
        lines, _ = build(args.config, panels_dir, layouts_dir, args.runtime_dir)
    except Exception as exc:  # noqa: BLE001 — surface a clean error on stderr, fail loud
        log.error("layout build failed: %s", exc)
        return 1

    # STDOUT: the tmux script to eval. Nothing else may go here.
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
