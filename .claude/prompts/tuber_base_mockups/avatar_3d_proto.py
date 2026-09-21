#!/usr/bin/env python3
"""
avatar_3d_proto.py — prototype: rotating placeholder 3D mesh (icosahedron)
standing in for a real rigged character model, via termgl. Proves the
render/animation loop shape a future real avatar mesh would use (idle
rotation now; talk/gesture states would just swap which mesh/pose gets fed
into the same render_frame loop).

Run standalone: /opt/render3d/bin/python3 avatar_3d_proto.py

Prototype only (.claude/prompts/tuber_base_mockups/) — does not touch
app/avatar.py or app/avatar_providers/*.
"""
import argparse
import sys
import time

sys.path.insert(0, ".")
import numpy as np
import termgl as tgl

from mesh3d import build_icosahedron
from render3d_common import make_context, make_camera, make_view, LitPixelShader


def render_frame(ctx, camera, mesh, angle):
    view = make_view(rot_x=0.4, rot_y=angle, rot_z=0.0, dist=3.0)
    vertex_shader = tgl.VertexShaderSimple(np.matmul(camera, view))
    pixel_shader = LitPixelShader()
    pixel_shader.base_color = tgl.PixFmt(tgl.Idx(tgl.Color.YELLOW, flags=tgl.FmtFlag.BOLD))
    for trig in mesh:
        pixel_shader.trig = trig
        ctx.triangle_3d(trig, vertex_shader, pixel_shader)
    ctx.flush()
    ctx.clear(tgl.Buffer.FRAME | tgl.Buffer.Z | tgl.Buffer.OUTPUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=55)
    parser.add_argument("--height", type=int, default=24)
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--fps-report", action="store_true")
    args = parser.parse_args()

    ctx = make_context(args.width, args.height)
    camera = make_camera(args.width, args.height, fov=1.2)
    mesh = build_icosahedron(radius=1.0)

    angle = 0.0
    times = []
    n = 0
    while args.frames == 0 or n < args.frames:
        t0 = time.time()
        render_frame(ctx, camera, mesh, angle)
        times.append(time.time() - t0)
        angle += 0.08
        n += 1
        if args.frames != 1:
            time.sleep(0.05)

    if args.fps_report and times:
        avg = sum(times) / len(times)
        print(f"frames={len(times)} avg_render_s={avg:.4f} max_render_s={max(times):.4f}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
