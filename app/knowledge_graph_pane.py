#!/usr/bin/env python3
"""
knowledge_graph_pane.py
Standalone display process for the tmux "Knowledge" pane. Renders a
STATIC placeholder graph — per docs/tuber_base_layout_plan.md Decision 3,
this is a stub only; wiring to real campaign-pack/Postgres data is
explicitly a separate future task — as a real shaded 3D node/edge graph
via termgl (docs/panels.md's "3D rendering (termgl)" section): nodes are
small octahedra placed on a Fibonacci sphere, edges are projected 3D
lines. Runs under the /opt/render3d Python 3.11 venv (see Dockerfile) —
termgl requires Python >=3.11, unlike the rest of this app which targets
the system python3.10.

Real depth (termgl's Z-buffer) is what makes this readable at N>4 nodes
where a flat 2D ASCII layout collided labels/edges — see
.claude/prompts/tuber_base_mockups/ for the earlier flat prototypes this
replaced and why they were rejected.

Plain print()-style rendering (termgl's own puts()/triangle_3d() write
directly to the terminal via its OUTPUT_BUFFER, matching every other
pane's "just draw to stdout" house style — NOT a full-screen TUI).
"""
import argparse
import logging
import os
import sys
import time

from mesh3d import build_node_positions, build_graph_mesh

logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("KNOWLEDGE_GRAPH_PANE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s knowledge_graph_pane %(message)s",
)
log = logging.getLogger("knowledge_graph_pane")

WIDTH = 37
HEIGHT = 40
FOV = 1.1
VIEW_ROT_X = 0.4
VIEW_ROT_Y_SPEED = 0.05  # radians/frame — slow idle rotation
VIEW_DIST = 2.8
REFRESH_INTERVAL_S = 0.1  # smoother than the radar pane — rotation is the point
NODE_RADIUS = 0.9
NODE_MESH_R = 0.07

# STATIC PLACEHOLDER data (Decision 3 — do not wire to real data here).
# "(placeholder)" is stamped into the header every frame so nobody mistakes
# this for real character-knowledge data (matches the original ASCII
# placeholder's labeling requirement).
PLACEHOLDER_NODES = ["Kael", "Ashiorid", "Malvakar", "Riddle", "Vault", "Ring"]
PLACEHOLDER_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0), (0, 2)]


def _make_context_lazy():
    from render3d_common import make_context
    return make_context(WIDTH, HEIGHT)


def _make_camera_lazy():
    from render3d_common import make_camera
    return make_camera(WIDTH, HEIGHT, fov=FOV)


def render_graph_3d(ctx, camera, node_positions, edges, angle):
    """Draw one frame: shaded 3D nodes + projected edges + a plain-text header."""
    import numpy as np
    import termgl as tgl
    from render3d_common import make_view, LitPixelShader, project_point, ndc_to_screen, write_header_line

    view = make_view(rot_x=VIEW_ROT_X, rot_y=angle, rot_z=0.0, dist=VIEW_DIST)
    mvp = np.matmul(camera, view)

    node_trigs, edge_segments = build_graph_mesh(node_positions, edges, node_r=NODE_MESH_R)
    if len(node_trigs) > 0:
        vertex_shader = tgl.VertexShaderSimple(mvp)
        pixel_shader = LitPixelShader()
        pixel_shader.base_color = tgl.PixFmt(tgl.Idx(tgl.Color.CYAN, flags=tgl.FmtFlag.BOLD))
        for trig in node_trigs:
            pixel_shader.trig = trig
            ctx.triangle_3d(trig, vertex_shader, pixel_shader)

    edge_color = tgl.PixFmt(tgl.Idx(tgl.Color.CYAN))

    class _EdgeShader(tgl.PixelShader):
        def pixel_shader(self, u, v):
            return (edge_color, ord("."))

    edge_shader = _EdgeShader()
    for p0, p1 in edge_segments:
        ndc0 = project_point(mvp, p0)
        ndc1 = project_point(mvp, p1)
        if ndc0 is None or ndc1 is None:
            continue
        sx0, sy0 = ndc_to_screen(ndc0, WIDTH, HEIGHT)
        sx1, sy1 = ndc_to_screen(ndc1, WIDTH, HEIGHT)
        verts = np.array([
            (sx0, sy0, ndc0[2], 0, 0),
            (sx1, sy1, ndc1[2], 0, 0),
        ], dtype=tgl.Vert)
        ctx.line(verts, edge_shader)

    ctx.flush()
    ctx.clear(tgl.Buffer.FRAME | tgl.Buffer.Z | tgl.Buffer.OUTPUT)

    # Written AFTER flush(), via plain ANSI rather than ctx.puts() — see
    # write_header_line's docstring (DOUBLE_CHARS doubles puts() text too).
    write_header_line("Knowledge (placeholder)", color_name="CYAN")


def run(config_path, sleep=time.sleep):
    log.info("rendering placeholder graph: %d nodes, %d edges",
              len(PLACEHOLDER_NODES), len(PLACEHOLDER_EDGES))

    ctx = _make_context_lazy()
    camera = _make_camera_lazy()
    positions = build_node_positions(len(PLACEHOLDER_NODES), radius=NODE_RADIUS)
    angle = 0.0

    while True:
        render_graph_3d(ctx, camera, positions, PLACEHOLDER_EDGES, angle)
        angle += VIEW_ROT_Y_SPEED
        sleep(REFRESH_INTERVAL_S)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Static placeholder knowledge-graph pane, rendered as a 3D node/edge graph.")
    parser.add_argument("--config", default="/config/worker.yaml",
                        help="worker.yaml (unused today — reserved for future real-data wiring)")
    args = parser.parse_args(argv)
    run(args.config)


if __name__ == "__main__":
    main()
