#!/usr/bin/env python3
"""
knowledge_graph_pane.py
Standalone display process for the tmux "Knowledge" pane. Renders a STATIC
placeholder ASCII node/edge graph — per docs/tuber_base_layout_plan.md
Decision 3, this is a stub only; wiring to real campaign-pack/Postgres data
is explicitly out of scope for this pane and left for future work. The
render is clearly labeled "(placeholder)" so nobody mistakes it for a real
knowledge graph.

Plain print() (NOT a full-screen TUI), matching every other pane's house
style (see tail_bus.py, avatar.py). The graph is static so there is no
refresh loop, but the process stays resident (sleeping) so the tmux pane
doesn't fall back to a bare shell prompt.
"""
import argparse
import sys
import time


PLACEHOLDER_GRAPH = r"""
   (A)
    |\
    | \
   (B)-(C)
    |   |
   (D)-(E)
""".strip("\n")


def render_placeholder_graph():
    """Pure render of the static placeholder graph, clearly labeled."""
    lines = ["Knowledge Graph (placeholder)", ""]
    lines.extend(PLACEHOLDER_GRAPH.splitlines())
    lines.append("")
    lines.append("(static stub — not wired to real campaign data)")
    return "\n".join(lines)


def run(config_path=None, sleep=time.sleep):
    print(render_placeholder_graph(), flush=True)
    # Static pane: nothing to refresh, just stay resident.
    while True:
        sleep(3600)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Static placeholder knowledge-graph pane.")
    parser.add_argument("--config", default="/config/worker.yaml",
                        help="worker.yaml (unused today — reserved for future real data wiring)")
    args = parser.parse_args(argv)
    run(args.config)


if __name__ == "__main__":
    main()
