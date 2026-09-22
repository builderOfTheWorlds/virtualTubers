#!/usr/bin/env python3
"""
character_preview.py
The agent-facing iteration loop for the character generator
(docs/avatar_3d_design.md §5 step 3): take a slider dict, render the
resulting head as ASCII, print it. An agent calls this, reads the frames
out of stdout, adjusts sliders, and calls it again.

Runs on plain numpy — no termgl, no TTY, no container — so the loop is
fast and fully local (see ascii_raster.py for why termgl itself can't be
used off a real terminal). The frames it prints are a faithful preview of
the pane's shading and silhouette, not a pixel-exact capture; the real
pane in the worker container remains ground truth.

Usage:
    # the flagship character, 4-view turntable
    python3 app/character_preview.py --preset chadwick

    # iterate on individual sliders
    python3 app/character_preview.py --preset chadwick --set jaw_width=0.9 --set ear_size=0.2

    # a full param dict (what an agent typically does)
    python3 app/character_preview.py --params '{"head_width":0.8,"eye_size":0.7}'

    # machine-readable, for an agent that wants the grid as data
    python3 app/character_preview.py --preset chadwick --json
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ascii_raster import render_mesh  # noqa: E402
from character_schema import (  # noqa: E402
    PARAM_DEFAULTS,
    PRESETS,
    CharacterParamError,
    resolve_params,
)
from head_mesh import build_head_params_mesh  # noqa: E402

log = logging.getLogger("character_preview")

#: Default preview geometry. WIDTH/HEIGHT match termgl_avatar.py's pane
#: constants so what an agent judges is what the pane will draw; DIST is
#: tuned so a default-proportioned head fills most of the frame without
#: clipping at the widest slider settings.
WIDTH = 55
HEIGHT = 24
DIST = 2.4
ROT_X = 0.4

#: Angles rendered by a turntable, labelled for the agent's benefit —
#: judging a face from one view is how you end up with a head that looks
#: right head-on and broken in profile, and the pane rotates continuously.
TURNTABLE = [
    ("front", 0.0),
    ("three-quarter", 0.9),
    ("profile", 1.5708),
    ("rear-quarter", 2.6),
]


def parse_set(pairs):
    """Turn `--set key=value` strings into a params dict. Values parse as
    JSON when possible (so 0.8 is a float) and fall back to the raw string
    (so accent_color=CYAN works without quoting)."""
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise CharacterParamError(
                f"--set expects key=value, got {pair!r}")
        key, _, raw = pair.partition("=")
        key = key.strip()
        raw = raw.strip()
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError:
            out[key] = raw
    return out


def build_params(args):
    """Merge --preset / --params / --set into one normalized params dict.

    Precedence, lowest to highest: preset, --params, --set. That ordering
    lets an agent start from a known-good face and nudge one slider.
    """
    log.debug("build_params(preset=%s, params=%s, set=%s)",
              args.preset, bool(args.params), args.set)
    raw = {}
    if args.preset:
        raw.update(resolve_params(args.preset))
    if args.params:
        try:
            parsed = json.loads(args.params)
        except json.JSONDecodeError as exc:
            raise CharacterParamError(f"--params is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise CharacterParamError("--params must be a JSON object")
        raw.update(parsed)
    raw.update(parse_set(args.set))
    return resolve_params(raw, strict=True)


def render_views(params, views, width, height, dist, double_chars=True):
    """Render one (label, rot_y) list into [(label, rows)]."""
    log.debug("render_views(%d views, %dx%d)", len(views), width, height)
    verts, faces = build_head_params_mesh(params)
    log.info("built head mesh: %d verts, %d faces", len(verts), len(faces))
    return [
        (label, render_mesh(verts, faces, width=width, height=height,
                            rot_y=rot, rot_x=ROT_X, dist=dist,
                            double_chars=double_chars))
        for label, rot in views
    ]


def format_text(params, frames, show_params=True):
    """Human/agent-readable report: the sliders that produced the frames,
    then each labelled view. Params are echoed so a rendered frame pasted
    into a conversation is self-describing — an agent comparing two
    iterations can see exactly what changed."""
    out = []
    if show_params:
        out.append("params: " + json.dumps(params, sort_keys=True))
        out.append("")
    for label, rows in frames:
        out.append(f"--- {label} ---")
        out.extend(row.rstrip() for row in rows)
        out.append("")
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render a parametric character head as ASCII for agent-driven "
                    "slider iteration (docs/avatar_3d_design.md).")
    parser.add_argument("--preset", choices=sorted(PRESETS),
                        help="start from a named preset")
    parser.add_argument("--params", help="JSON object of slider values")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="override one parameter; repeatable")
    parser.add_argument("--view", choices=[v[0] for v in TURNTABLE],
                        help="render a single named view instead of the turntable")
    parser.add_argument("--rot-y", type=float,
                        help="render a single view at this Y rotation (radians)")
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--dist", type=float, default=DIST,
                        help="camera distance; lower = larger head in frame")
    parser.add_argument("--no-double-chars", action="store_true",
                        help="disable termgl's DOUBLE_CHARS pixel doubling "
                             "(halves output width; aspect is corrected)")
    parser.add_argument("--json", action="store_true",
                        help="emit {params, frames} as JSON instead of text")
    parser.add_argument("--list-params", action="store_true",
                        help="print the slider schema and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    if args.list_params:
        print(json.dumps(PARAM_DEFAULTS, indent=2, sort_keys=True))
        return 0

    try:
        params = build_params(args)
    except CharacterParamError as exc:
        # An agent iterating on sliders WILL send a bad key or a bad value;
        # a clear one-line reason on stderr plus a non-zero exit is more
        # useful to it than a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.rot_y is not None:
        views = [(f"rot_y={args.rot_y:.2f}", args.rot_y)]
    elif args.view:
        views = [v for v in TURNTABLE if v[0] == args.view]
    else:
        views = TURNTABLE

    frames = render_views(params, views, args.width, args.height, args.dist,
                          double_chars=not args.no_double_chars)

    if args.json:
        print(json.dumps({
            "params": params,
            "frames": [{"label": label, "rows": rows} for label, rows in frames],
        }, indent=2, sort_keys=True))
    else:
        print(format_text(params, frames))
    return 0


if __name__ == "__main__":
    sys.exit(main())
