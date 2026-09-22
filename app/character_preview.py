#!/usr/bin/env python3
"""
character_preview.py
The agent-facing iteration loop for the character generator
(docs/avatar_3d_design.md §5 step 3): take a slider dict, render the
resulting head with the codec pixel pipeline, save PNG(s). An agent (or you)
calls this, looks at the frame(s), adjusts sliders, and calls it again.

Renders with codec_head.py + pixel_raster.py — the low-poly MGS2-codec-style
head with flat shading and dark eye/brow/mouth materials that replaced the
original smooth-sphere ascii_raster/head_mesh pipeline (see
docs/character_generator.md for why). Pure numpy + a dependency-free PNG
writer — no termgl, no TTY, no container, so the loop is fast and fully
local. This is a **preview of the same codec renderer** the live pane uses
(pixel_raster.render); it is not guaranteed pixel-identical to whatever
frame timing/caching the live avatar path applies on top.

Usage:
    # the flagship character, 4-view turntable -> preview_out/*.png
    python3 app/character_preview.py --preset chadwick

    # iterate on individual sliders
    python3 app/character_preview.py --preset chadwick --set jaw_width=0.9 --set ear_size=0.2

    # a full param dict (what an agent typically does)
    python3 app/character_preview.py --params '{"head_width":0.8,"eye_size":0.7}'

    # single view, custom output path
    python3 app/character_preview.py --preset chadwick --view front -o out.png

    # amber tint instead of the default codec green
    python3 app/character_preview.py --preset chadwick --tint amber

    # machine-readable, for an agent that wants file paths as data
    python3 app/character_preview.py --preset chadwick --json
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from character_schema import (  # noqa: E402
    PARAM_DEFAULTS,
    PRESETS,
    CharacterParamError,
    resolve_params,
)
from codec_head import build_codec_head  # noqa: E402
import gl_raster  # noqa: E402
from pixel_raster import (  # noqa: E402
    TINT_AMBER,
    TINT_CODEC_GREEN,
    apply_codec_screen,
    write_png,
)

log = logging.getLogger("character_preview")

#: Default preview geometry. Matches the "real avatar pane" render call
#: proven out in the codec_final.png test render (docs/character_generator.md)
#: so what an agent judges here is what the live pane renders, not a
#: differently-framed stand-in.
WIDTH = 560
HEIGHT = 700
DIST = 3.25
ROT_X = 0.06
PAN_Y = 0.05

#: Default output directory for saved frames (gitignored — throwaway renders).
OUT_DIR = Path(__file__).resolve().parent.parent / "preview_out"

TINTS = {"green": TINT_CODEC_GREEN, "amber": TINT_AMBER}

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


def render_views(params, views, width, height, dist, tint,
                 scanlines=True, force_cpu=False):
    """Render one (label, rot_y) list into [(label, rgb array, backend)].

    GPU-first via gl_raster.render_with_fallback() (--cpu forces the numpy
    path, useful for comparing the two or when debugging a GPU issue).
    """
    log.debug("render_views(%d views, %dx%d)", len(views), width, height)
    verts, faces, mats = build_codec_head(params)
    log.info("built codec head: %d verts, %d faces", len(verts), len(faces))
    frames = []
    for label, rot in views:
        t0 = time.perf_counter()
        if force_cpu:
            from pixel_raster import render as cpu_render
            img = cpu_render(verts, faces, mats, width=width, height=height,
                             rot_x=ROT_X, rot_y=rot, dist=dist, pan_y=PAN_Y,
                             tint=tint)
            backend = "cpu"
        else:
            img, backend = gl_raster.render_with_fallback(
                verts, faces, mats, width=width, height=height,
                rot_x=ROT_X, rot_y=rot, dist=dist, pan_y=PAN_Y, tint=tint)
        if scanlines:
            img = apply_codec_screen(img)
        dt = time.perf_counter() - t0
        log.info("rendered %r in %.3fs (%.1f fps, %s)", label, dt, 1.0 / dt, backend)
        frames.append((label, img, dt, backend))
    return frames


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render a parametric character head with the codec pixel "
                    "renderer for agent-driven slider iteration "
                    "(docs/avatar_3d_design.md).")
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
    parser.add_argument("--tint", choices=sorted(TINTS), default="green",
                        help="codec screen tint (default: green)")
    parser.add_argument("--no-scanlines", action="store_true",
                        help="skip the CRT scanline/vignette/glow post-pass")
    parser.add_argument("--cpu", action="store_true",
                        help="force the pure-numpy pixel_raster path instead of "
                             "GPU (gl_raster); useful to compare or when "
                             "debugging a GPU/driver issue")
    parser.add_argument("-o", "--out", help="output PNG path (single view only)")
    parser.add_argument("--out-dir", default=str(OUT_DIR),
                        help=f"directory for turntable frames (default: {OUT_DIR})")
    parser.add_argument("--json", action="store_true",
                        help="emit {params, frames:[{label,path,seconds}]} as JSON")
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

    if args.out and len(views) != 1:
        print("error: -o/--out only applies to a single view "
              "(pass --view or --rot-y)", file=sys.stderr)
        return 2

    frames = render_views(params, views, args.width, args.height, args.dist,
                          tint=TINTS[args.tint],
                          scanlines=not args.no_scanlines,
                          force_cpu=args.cpu)

    out_dir = Path(args.out_dir)
    saved = []
    for label, img, dt, backend in frames:
        if args.out:
            path = Path(args.out)
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_label = label.replace("=", "_")
            path = out_dir / f"{safe_label}.png"
        write_png(str(path), img)
        saved.append({"label": label, "path": str(path), "seconds": round(dt, 4),
                     "backend": backend})

    if args.json:
        print(json.dumps({"params": params, "frames": saved},
                         indent=2, sort_keys=True))
    else:
        print("params: " + json.dumps(params, sort_keys=True))
        print()
        for entry in saved:
            print(f"{entry['label']:>16}: {entry['path']}  "
                  f"({entry['seconds']:.3f}s, {1.0/entry['seconds']:.1f} fps, "
                  f"{entry['backend']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
