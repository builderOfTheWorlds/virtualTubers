"""Render the Live Agent Table on vLLM build-plan diagram.

Companion to .claude/prompts/agent_dnd_vllm_build_plan.md (PLAN v1.0,
2026-10-07). Top: target runtime architecture. Bottom: phase order with
status colouring (exists / partial / to build).

Run:   .venv/bin/python scripts/render_agent_dnd_vllm_plan.py
Out:   docs/agent_dnd_vllm_plan.png
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BG = "#14161c"
FG = "#e6e6e6"
COL = {
    "exists": "#2e7d4f",   # running / built
    "partial": "#a67c1a",  # built but needs change
    "new": "#7a2e3a",      # to build
    "infra": "#2d4f7c",
    "user": "#5b3f8c",
}

fig = plt.figure(figsize=(18, 13), facecolor=BG)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 18)
ax.set_ylim(0, 13)
ax.axis("off")


def box(x, y, w, h, text, kind, fs=9.5):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle="round,pad=0.04,rounding_size=0.15",
                                fc=COL[kind], ec="#cfd3da", lw=1.0, alpha=0.95))
    ax.text(x, y, text, ha="center", va="center", color=FG, fontsize=fs,
            family="monospace")


def arrow(x1, y1, x2, y2, label="", rad=0.0, fs=8.5, both=False, dy=0.0):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
                                 arrowstyle="<|-|>" if both else "-|>",
                                 mutation_scale=13, color="#9aa3ad", lw=1.2,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + dy, label, ha="center", va="center",
                color="#cdd3da", fontsize=fs, family="monospace",
                bbox=dict(fc=BG, ec="none", pad=1.5))


ax.text(9, 12.6, "Live Agent Table on vLLM — target architecture + build order",
        ha="center", color=FG, fontsize=16, weight="bold")
ax.text(9, 12.2, "plan: .claude/prompts/agent_dnd_vllm_build_plan.md  (2026-10-07)",
        ha="center", color="#9aa3ad", fontsize=10, family="monospace")

# ---- architecture --------------------------------------------------------
box(9, 11.25, 9.0, 0.95,
    "vllm-agents :8092  — ONE model shared by every agent (P0.1, benchmark-picked P1)\n"
    "continuous batching · prefix caching · reasoning_content stream",
    "new", fs=10)

box(3.2, 9.0, 5.4, 1.9,
    "worker-gm  (tuber_0)\n"
    "GM AGENT  — full context:\n"
    "pack · all sheets · TRUTH layer · scene contract\n"
    "TURN ARBITER  app/turns.py  (state -> Redis)\n"
    "LOADED>DIRECT>THINK>SPEAK>ADJUDICATE>RESOLVE",
    "new", fs=9)

box(11.4, 9.0, 8.6, 1.9,
    "worker tuber_1 … tuber_N   (one CHARACTER AGENT each; N=4 D&D, 7 office)\n"
    "scoped context: BRIEF (who · believed · wants · what you know)\n"
    "+ session unlocks + committed transcript (by value)\n"
    "THINK pass (parallel, private)  ->  SPEAK pass (in turn)\n"
    "tmux: radar · knowledge graph · avatar · THINKING (reasoning) · feed",
    "partial", fs=9)

arrow(3.2, 9.95, 6.0, 10.78, "GM calls", rad=-0.15)
arrow(11.4, 9.95, 10.5, 10.78, "N seat calls (batched)", rad=0.1)

box(9, 6.7, 8.0, 1.0,
    "Kafka vtuber.messages  — transport + audit only\n"
    "scene_direction · think_request · turn_assignment · character_reply\n"
    "retake · gm_overrule · adjudication · scene_resolve · agent_thinking",
    "exists", fs=8.8)

arrow(5.5, 8.05, 5.5, 7.2)
ax.text(5.6, 7.62, "direct /\nassign", color="#cdd3da", fontsize=8, family="monospace",
        ha="left", va="center", bbox=dict(fc=BG, ec="none", pad=1.0))
arrow(11.4, 8.05, 11.0, 7.2, "think_done / reply", rad=-0.05, dy=0.05)

box(2.6, 6.7, 3.9, 1.0,
    "character_profile PG\n192.168.1.120:5433\n(schema only; DEPLOY P2.1)",
    "partial", fs=8.8)
arrow(2.6, 7.2, 2.6, 8.05, "truth (P2.7)", fs=8.0)
arrow(4.55, 6.95, 7.9, 8.05, rad=0.0)
ax.text(7.25, 7.42, "brief (P2.6)", color="#cdd3da", fontsize=8, family="monospace",
        ha="center", va="center", bbox=dict(fc=BG, ec="none", pad=1.0))

box(9, 5.05, 8.0, 0.95,
    "worker-roundtable = THE TABLE\nrelay file (live feed) -> tiles + voice gate + TTS  (P4)",
    "partial", fs=9)
arrow(9, 6.2, 9, 5.55, "", rad=0)

box(2.6, 5.05, 3.9, 0.95, "campaigns/<pack>/profiles\n-> loader -> DB (P2.4/P2.5)",
    "partial", fs=8.8)
arrow(2.6, 5.55, 2.6, 6.2, "", rad=0)
box(15.6, 6.7, 3.6, 1.0, "Redis world-state\nscene · round · turn\nunlocks · state_delta",
    "exists", fs=8.8)
ax.text(15.6, 5.95, "written only by the arbiter", color="#9aa3ad", fontsize=8,
        family="monospace", ha="center")

# ---- legend ---------------------------------------------------------------
for i, (k, t) in enumerate([("exists", "exists / running"),
                            ("partial", "exists, needs change"),
                            ("new", "to build"),
                            ("user", "needs you")]):
    ax.add_patch(FancyBboxPatch((0.5 + i * 3.0, 4.05), 0.4, 0.3,
                                boxstyle="round,pad=0.02", fc=COL[k], ec="#cfd3da"))
    ax.text(1.05 + i * 3.0, 4.2, t, color=FG, fontsize=9.5, va="center")

# ---- phase timeline -------------------------------------------------------
ax.text(0.5, 3.55, "Build order", color=FG, fontsize=13, weight="bold")
phases = [
    (1.5, 2.6, "P0\nvLLM serve\n+ client\n+ reasoning", "new"),
    (4.4, 3.0, "P1  GATE\nbenchmark 3\nmodels on vLLM\n(you score)", "user"),
    (4.4, 1.35, "P2  knowledge\nDB deploy · brief\nD&D profiles\n(you review)", "user"),
    (7.3, 2.2, "P3\nslice: GM +\n1 character", "new"),
    (9.7, 2.2, "P4\nroundtable\nlive feed", "new"),
    (12.1, 2.2, "P5\nfull table\nD&D 4 · office 7", "new"),
    (14.5, 2.2, "P6\nreveal /\nleak proof", "new"),
    (16.8, 2.2, "P7\nrecords +\noperator", "new"),
]
for x, y, t, k in phases:
    box(x, y, 2.1, 1.2, t, k, fs=8.8)
arrow(2.55, 2.75, 3.35, 3.0)
arrow(2.55, 2.45, 3.35, 1.45)
arrow(5.45, 3.0, 6.25, 2.35, "deadlines", fs=7.5)
arrow(5.45, 1.35, 6.25, 2.05)
for a, b in [(7.3, 9.7), (9.7, 12.1), (12.1, 14.5), (14.5, 16.8)]:
    arrow(a + 1.05, 2.2, b - 1.05, 2.2)
ax.text(9, 0.35,
        "P8 later: v4 loop — ingest · nightly summaries · Sunday reset · fragments · recall",
        ha="center", color="#9aa3ad", fontsize=10, family="monospace")

fig.savefig("docs/agent_dnd_vllm_plan.png", dpi=110, facecolor=BG)
print("wrote docs/agent_dnd_vllm_plan.png")
