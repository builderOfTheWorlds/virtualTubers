"""Render the as-built vs corrected 3-layer generator flow as a PNG.

Manual (x, y) layout per codebase-visualization skill — a spring layout
produces an unreadable hairball for an architecture/flow diagram.
Two panels side by side: AS BUILT (left) and CORRECTED (right).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch

FIG_W, FIG_H = 27, 17

COLORS = {
    "source": "#c08040",
    "pack": "#4080c0",
    "gen": "#8040c0",
    "bad": "#e04040",
    "good": "#40b060",
    "air": "#40c080",
    "gate": "#c0c040",
    "dead": "#707070",
}

# (label, x, y, group, width, height)
LEFT = [
    ("Obsidian vault\nashioridCampaign", 1.0, 15.2, "source", 3.0, 1.0),
    ("HUMAN\nauthors by hand", 1.0, 13.5, "source", 3.0, 1.0),
    ("scenes/*.yaml\n49 files\n15 spine + 34 ambient\nNEVER GROWS", 1.0, 11.3, "pack", 3.4, 1.5),
    ("cast/*.yaml\nlore/*.md", 8.7, 11.6, "pack", 2.4, 1.0),
    ("Layer 1  plan_arc.py\n28 segments x 6h\n+ ring.py", 1.0, 8.9, "gen", 3.4, 1.2),
    ("Layer 2  plan_segment.py\nrecursive slot tree", 1.0, 7.0, "gen", 3.4, 1.0),
    ("Layer 3  generate_segment_dialogue.py\ncalls improviser OFFLINE", 1.0, 5.1, "bad", 4.2, 1.1),
    ("generated/\n2,671 FROZEN takes\nnever passes load_pack()", 1.0, 3.0, "bad", 3.8, 1.3),
    ("build_campaign_episode.py", 6.0, 8.9, "air", 3.0, 0.8),
    ("build_generated_episode.py", 6.0, 3.0, "air", 3.0, 0.8),
    ("Episode store\n-> TTS -> Twitch", 6.0, 5.9, "air", 3.0, 1.1),
]

RIGHT = [
    ("NEW SOURCE MATERIAL\nvault / plot notes / lore", 14.0, 15.2, "source", 3.6, 1.1),
    ("Layer 0  ingest_source.py\nNEW: digest + lore", 14.0, 13.4, "gen", 3.6, 1.0),
    ("Layer 1  plan_arc.py + ring.py\nplans arc AND declares\nnew_spine[] proposals", 14.0, 11.5, "gen", 3.9, 1.3),
    ("Layer 2  plan_segment.py\nslots = SCENE SPECS", 14.0, 9.8, "gen", 3.9, 1.0),
    ("Layer 3'  author_scenes.py\nwrites SCENE YAML", 14.0, 8.1, "good", 3.9, 1.1),
    ("output/proposed_scenes/\nSTAGING", 14.0, 6.5, "gen", 3.4, 0.9),
    ("pack_gate.py\nload_pack() on overlay\nlore? cast? primitives?", 14.0, 4.7, "gate", 3.9, 1.3),
    ("rejected\nwith scene id", 18.7, 4.7, "bad", 2.2, 0.9),
    ("--promote\nexplicit human step", 14.0, 2.9, "gate", 3.4, 1.0),
    ("scenes/*.yaml\nTHIS NOW GROWS", 19.3, 2.9, "pack", 3.2, 1.0),
    ("spine_chain.py\nwires default_next", 19.3, 1.2, "pack", 3.2, 0.9),
    ("cast/ + lore/", 23.4, 7.4, "pack", 2.4, 0.8),
    ("AIR TIME\nrenderer.py -> variant pools\n-> improviser.py LIVE\n-> Episode store -> Twitch", 19.3, 9.9, "air", 4.0, 1.9),
    ("generated/ FROZEN\nlegacy only", 19.3, 13.6, "dead", 3.0, 0.9),
]

# (src_idx, dst_idx, label, style)
LEFT_EDGES = [
    (0, 1, "read manually", "solid"),
    (1, 2, "hand-written", "solid"),
    (2, 4, "READ ONLY\nschedule only", "dashed"),
    (3, 5, "", "dashed"),
    (4, 5, "arc_plan", "solid"),
    (5, 6, "brief + tree", "solid"),
    (6, 7, "frozen words", "solid"),
    (2, 8, "", "solid"),
    (8, 10, "", "solid"),
    (7, 9, "", "solid"),
    (9, 10, "", "solid"),
]

RIGHT_EDGES = [
    (0, 1, "", "solid"),
    (1, 2, "", "solid"),
    (2, 3, "arc_plan\n+ new_spine", "solid"),
    (3, 4, "brief + tree", "solid"),
    (4, 5, "", "solid"),
    (5, 6, "", "solid"),
    (6, 7, "PackError", "solid"),
    (6, 8, "clean load", "solid"),
    (8, 9, "new scenes", "solid"),
    (9, 10, "", "solid"),
    (9, 12, "single bridge", "solid"),
    (9, 2, "context:\nexisting scenes", "dashed"),
    (11, 4, "", "dashed"),
]


def draw_panel(ax, nodes, edges, x_off=0.0):
    boxes = {}
    for i, (label, x, y, group, w, h) in enumerate(nodes):
        color = COLORS[group]
        box = FancyBboxPatch(
            (x - w / 2 + x_off, y - h / 2), w, h,
            boxstyle="round,pad=0.12",
            facecolor=color, edgecolor="white", linewidth=1.6, alpha=0.92, zorder=3,
        )
        ax.add_patch(box)
        ax.text(x + x_off, y, label, ha="center", va="center",
                fontsize=8.5, color="white", weight="bold", zorder=4)
        boxes[i] = (x + x_off, y, w, h)

    for src, dst, label, style in edges:
        x1, y1, w1, h1 = boxes[src]
        x2, y2, w2, h2 = boxes[dst]
        # anchor on nearest edges
        if abs(y2 - y1) > abs(x2 - x1):
            sy = y1 - h1 / 2 if y2 < y1 else y1 + h1 / 2
            ey = y2 + h2 / 2 if y2 < y1 else y2 - h2 / 2
            sx, ex = x1, x2
        else:
            sx = x1 + w1 / 2 if x2 > x1 else x1 - w1 / 2
            ex = x2 - w2 / 2 if x2 > x1 else x2 + w2 / 2
            sy, ey = y1, y2
        rad = 0.16 if abs(x2 - x1) > 0.1 and abs(y2 - y1) > 0.1 else 0.0
        arrow = FancyArrowPatch(
            (sx, sy), (ex, ey),
            connectionstyle=f"arc3,rad={rad}",
            arrowstyle="-|>", mutation_scale=15,
            linewidth=1.5, color="#cccccc",
            linestyle="--" if style == "dashed" else "-",
            zorder=2, alpha=0.85,
        )
        ax.add_patch(arrow)
        if label:
            mx, my = (sx + ex) / 2, (sy + ey) / 2
            ax.text(mx + 0.55, my, label, fontsize=6.8, color="#e8e8a0",
                    ha="left", va="center", style="italic", zorder=5)


fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
fig.patch.set_facecolor("#12141a")
ax.set_facecolor("#12141a")

draw_panel(ax, LEFT, LEFT_EDGES)
draw_panel(ax, RIGHT, RIGHT_EDGES)

# divider
ax.plot([11.7, 11.7], [0.2, 16.4], color="#444", linewidth=2, linestyle=":", zorder=1)

ax.text(3.6, 16.6, "AS BUILT  —  generator writes DIALOGUE",
        fontsize=17, color="#ff6060", weight="bold", ha="center")
ax.text(18.2, 16.6, "CORRECTED  —  generator writes SCENES",
        fontsize=17, color="#60d080", weight="bold", ha="center")

ax.text(3.6, 1.4, "scenes/ has NO inbound arrow.\nThe pack never grows.",
        fontsize=10.5, color="#ff8080", ha="center", style="italic")
ax.text(18.2, 0.0, "scenes/ grows, gated by load_pack().\nDialogue moves back to air time.",
        fontsize=10.5, color="#80e0a0", ha="center", style="italic")

legend = [
    Patch(facecolor=COLORS["source"], label="Source material"),
    Patch(facecolor=COLORS["gen"], label="Generator layer"),
    Patch(facecolor=COLORS["pack"], label="Campaign pack"),
    Patch(facecolor=COLORS["gate"], label="Validation gate"),
    Patch(facecolor=COLORS["air"], label="Air-time path"),
    Patch(facecolor=COLORS["bad"], label="Misdirected / rejected"),
    Patch(facecolor=COLORS["good"], label="Retargeted layer"),
    Patch(facecolor=COLORS["dead"], label="Frozen legacy"),
]
ax.legend(handles=legend, loc="lower left", fontsize=9.5, facecolor="#1c1f28",
          edgecolor="#555", labelcolor="white", ncol=4, bbox_to_anchor=(0.01, -0.02))

ax.set_xlim(-0.8, 25.4)
ax.set_ylim(-0.6, 17.2)
ax.axis("off")
plt.tight_layout()
out = "docs/generator_retarget_flow.png"
plt.savefig(out, dpi=140, facecolor=fig.get_facecolor(), bbox_inches="tight")
print(f"wrote {out}")
