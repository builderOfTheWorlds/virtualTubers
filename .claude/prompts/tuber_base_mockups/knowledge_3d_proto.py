#!/usr/bin/env python3
"""
knowledge_3d_proto.py — prototype: render the Knowledge panel as a real 3D
node/edge graph via termgl (nodes = small octahedra on a Fibonacci sphere,
edges = projected 3D lines). Solves the label/edge-collision problem the
flat ASCII mockups hit, since termgl's Z-buffer naturally occludes/shrinks
far-side nodes instead of overlapping their labels.

Run standalone: /opt/render3d/bin/python3 knowledge_3d_proto.py

Prototype only (.claude/prompts/tuber_base_mockups/) — mirrors
app/knowledge_graph_pane.py's placeholder shape but does not touch that file.
"""
import argparse
import sys
import time

sys.path.insert(0, ".")
import numpy as np
import termgl as tgl

from mesh3d import build_node_positions, build_graph_mesh
from render3d_common import make_context, make_camera, make_view, LitPixelShader, project_point

SAMPLE_NODES = ["Kael", "Ashiorid", "Malvakar", "Riddle", "Vault", "Ring"]
SAMPLE_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0), (0, 2)]

EDGE_COLOR = tgl.PixFmt(tgl.Idx(tgl.Color.CYAN))


class EdgeShader(tgl.PixelShader):
    def pixel_shader(self, u, v):
        return (EDGE_COLOR, ord("."))


def ndc_to_screen(ndc, width, height):
    """termgl screen coords: x in [0, width), y in [0, height) (per the
    tutorial's v0=(19,1,...) examples), y presumably top-down like curses."""
    x = (ndc[0] * 0.5 + 0.5) * width
    y = (1.0 - (ndc[1] * 0.5 + 0.5)) * height
    return x, y


def render_frame(ctx, camera, width, height, node_positions, edges, angle):
    view = make_view(rot_x=0.4, rot_y=angle, rot_z=0.0, dist=2.8)
    mvp = np.matmul(camera, view)

    node_trigs, edge_segments = build_graph_mesh(node_positions, edges, node_r=0.07)
    vertex_shader = tgl.VertexShaderSimple(mvp)
    pixel_shader = LitPixelShader()
    pixel_shader.base_color = tgl.PixFmt(tgl.Idx(tgl.Color.CYAN, flags=tgl.FmtFlag.BOLD))
    for trig in node_trigs:
        pixel_shader.trig = trig
        ctx.triangle_3d(trig, vertex_shader, pixel_shader)

    # Edges: project each endpoint through the same MVP, draw as 2D lines.
    edge_shader = EdgeShader()
    for p0, p1 in edge_segments:
        ndc0 = project_point(mvp, p0)
        ndc1 = project_point(mvp, p1)
        if ndc0 is None or ndc1 is None:
            continue
        sx0, sy0 = ndc_to_screen(ndc0, width, height)
        sx1, sy1 = ndc_to_screen(ndc1, width, height)
        verts = np.array([
            (sx0, sy0, ndc0[2], 0, 0),
            (sx1, sy1, ndc1[2], 0, 0),
        ], dtype=tgl.Vert)
        ctx.line(verts, edge_shader)

    ctx.flush()
    ctx.clear(tgl.Buffer.FRAME | tgl.Buffer.Z | tgl.Buffer.OUTPUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=37)
    parser.add_argument("--height", type=int, default=40)
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--fps-report", action="store_true")
    args = parser.parse_args()

    ctx = make_context(args.width, args.height)
    camera = make_camera(args.width, args.height, fov=1.1)
    positions = build_node_positions(len(SAMPLE_NODES), radius=0.9)

    angle = 0.0
    times = []
    n = 0
    while args.frames == 0 or n < args.frames:
        t0 = time.time()
        render_frame(ctx, camera, args.width, args.height, positions, SAMPLE_EDGES, angle)
        times.append(time.time() - t0)
        angle += 0.1
        n += 1
        if args.frames != 1:
            time.sleep(0.05)

    if args.fps_report and times:
        avg = sum(times) / len(times)
        print(f"frames={len(times)} avg_render_s={avg:.4f} max_render_s={max(times):.4f}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
