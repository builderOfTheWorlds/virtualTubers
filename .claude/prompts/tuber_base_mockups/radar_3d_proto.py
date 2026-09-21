#!/usr/bin/env python3
"""
radar_3d_proto.py — prototype: render the Stats panel's 6 live metrics as a
real shaded 3D radar/spike mesh via termgl, instead of ASCII bars/polygon.
Run standalone: /opt/render3d/bin/python3 radar_3d_proto.py [--frames N]

Prototype only (.claude/prompts/tuber_base_mockups/) — mirrors
app/radar_pane.py's METRIC_SPECS shape but does not touch that file.
"""
import argparse
import math
import sys
import time

sys.path.insert(0, ".")
import numpy as np
import termgl as tgl

from mesh3d import build_radar_mesh
from render3d_common import make_context, make_camera, make_view, LitPixelShader

SAMPLE_METRICS = [0.62, 0.35, 0.71, 0.08, 0.95, 0.44]
LABELS = ["tok/s", "latency", "context", "err%", "uptime%", "msgs"]


def render_frame(ctx, camera, values, angle):
    trigs = build_radar_mesh(values)
    view = make_view(rot_x=1.0, rot_y=0.0, rot_z=angle, dist=2.6)
    vertex_shader = tgl.VertexShaderSimple(np.matmul(camera, view))
    pixel_shader = LitPixelShader()
    for trig in trigs:
        pixel_shader.trig = trig
        ctx.triangle_3d(trig, vertex_shader, pixel_shader)
    ctx.flush()
    ctx.clear(tgl.Buffer.FRAME | tgl.Buffer.Z | tgl.Buffer.OUTPUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=37)
    parser.add_argument("--height", type=int, default=20)
    parser.add_argument("--frames", type=int, default=1,
                         help="render N frames then exit (0 = loop forever)")
    parser.add_argument("--fps-report", action="store_true",
                         help="print avg frame render time to stderr")
    args = parser.parse_args()

    ctx = make_context(args.width, args.height)
    camera = make_camera(args.width, args.height)

    angle = 0.0
    n = 0
    times = []
    while args.frames == 0 or n < args.frames:
        t0 = time.time()
        render_frame(ctx, camera, SAMPLE_METRICS, angle)
        times.append(time.time() - t0)
        angle += 0.15
        n += 1
        if args.frames != 1:
            time.sleep(0.05)

    if args.fps_report and times:
        avg = sum(times) / len(times)
        print(f"frames={len(times)} avg_render_s={avg:.4f} max_render_s={max(times):.4f}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
