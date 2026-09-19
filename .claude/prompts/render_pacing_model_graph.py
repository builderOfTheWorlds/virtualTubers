"""Render the duration-driven pacing model (plan §6A) as a PNG.

Panel A: the 168h week as a ring-phase tempo bar — spine vs ambient share
         per phase, showing tempo as a dial rather than a constant.
Panel B: old take-driven budget vs new duration-driven budget, as a flow.
Manual (x, y) layout per the codebase-visualization skill.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Patch

C = {
    "spine": "#e0654a",
    "ambient": "#4a90c0",
    "descent": "#6a5acd",
    "keystone": "#c0a020",
    "ascent": "#40a870",
    "old": "#a03030",
    "new": "#30a060",
    "neutral": "#505868",
}

# phase, segments, hours, spine_share
PHASES = [
    ("DESCENT", 16, 96.0, 0.15, "descent", "world-building, slow burn\nsparse spine, long campfires"),
    ("KEYSTONE", 4, 24.0, 0.45, "keystone", "the turn\ndense, stacked events"),
    ("ASCENT", 8, 48.0, 0.30, "ascent", "consequences, release\nquickening"),
]

fig = plt.figure(figsize=(23, 14))
fig.patch.set_facecolor("#12141a")

# ---------------- Panel A: tempo bar ----------------
axA = fig.add_axes((0.04, 0.50, 0.92, 0.42))
axA.set_facecolor("#12141a")

x = 0.0
TOTAL_H = 168.0
for name, segs, hours, share, ckey, blurb in PHASES:
    w = hours / TOTAL_H * 100.0
    spine_h = hours * share
    amb_h = hours - spine_h

    # phase header band
    axA.add_patch(FancyBboxPatch((x + 0.25, 7.5), w - 0.5, 1.5,
                                 boxstyle="round,pad=0.1",
                                 facecolor=C[ckey], edgecolor="white",
                                 linewidth=1.6, alpha=0.95))
    axA.text(x + w / 2, 8.25, f"{name}   {segs} seg   {hours:.0f} h",
             ha="center", va="center", fontsize=12, color="white", weight="bold")

    # spine block (proportional height)
    sh = share * 6.0
    axA.add_patch(FancyBboxPatch((x + 0.25, 6.9 - sh), w - 0.5, sh,
                                 boxstyle="round,pad=0.06",
                                 facecolor=C["spine"], edgecolor="white",
                                 linewidth=1.2, alpha=0.95))
    axA.text(x + w / 2, 6.9 - sh / 2,
             f"SPINE {int(share*100)}%\n{spine_h:.1f} h",
             ha="center", va="center", fontsize=10.5, color="white", weight="bold")

    # ambient block fills the rest
    ah = 6.0 - sh
    axA.add_patch(FancyBboxPatch((x + 0.25, 0.7), w - 0.5, ah,
                                 boxstyle="round,pad=0.06",
                                 facecolor=C["ambient"], edgecolor="white",
                                 linewidth=1.2, alpha=0.88))
    axA.text(x + w / 2, 0.7 + ah / 2,
             f"AMBIENT {int((1-share)*100)}%\n{amb_h:.1f} h\nELASTIC FILLER",
             ha="center", va="center", fontsize=10.5, color="white", weight="bold")

    axA.text(x + w / 2, 0.05, blurb, ha="center", va="center",
             fontsize=8.5, color="#a8b0c0", style="italic")
    x += w

axA.text(50, 9.8, "THE 168-HOUR WEEK — tempo is a dial, set by ring phase",
         ha="center", fontsize=17, color="white", weight="bold")
axA.text(50, 9.25,
         "Significant scenes run as long as the story needs; ambient stretches to fill the gaps.",
         ha="center", fontsize=11, color="#98a0b0", style="italic")
axA.set_xlim(-1, 101)
axA.set_ylim(-0.5, 10.4)
axA.axis("off")

# ---------------- Panel B: old vs new derivation ----------------
axB = fig.add_axes((0.04, 0.03, 0.92, 0.43))
axB.set_facecolor("#12141a")


def chain(ax, nodes, y, color, title, title_color):
    ax.text(nodes[0][0] - 3.2, y + 1.85, title, fontsize=13.5,
            color=title_color, weight="bold", ha="left")
    prev = None
    for (cx, label, bad) in nodes:
        fc = C["old"] if bad else color
        ax.add_patch(FancyBboxPatch((cx - 5.6, y - 0.95), 11.2, 1.9,
                                    boxstyle="round,pad=0.12",
                                    facecolor=fc, edgecolor="white",
                                    linewidth=1.5, alpha=0.93))
        ax.text(cx, y, label, ha="center", va="center", fontsize=9.3,
                color="white", weight="bold")
        if prev is not None:
            ax.add_patch(FancyArrowPatch((prev + 5.6, y), (cx - 5.6, y),
                                         arrowstyle="-|>", mutation_scale=17,
                                         linewidth=1.8, color="#cccccc"))
        prev = cx


OLD = [
    (9, "measured_baseline\nwords_per_take = 105\nGPU THROUGHPUT", True),
    (27, "derive_target_slots()\nsegment_schema.py:66", True),
    (45, "slot count\nper node", True),
    (63, "takes generated\nand FROZEN", True),
    (81, "168 h assumed\nNEVER VERIFIED", True),
]

NEW = [
    (9, "ring phase\ndescent / keystone / ascent\nring.py UNCHANGED", False),
    (27, "spine_share_by_phase\n0.15 / 0.45 / 0.30", False),
    (45, "minutes budget\nper segment\nspine + ambient", False),
    (63, "scenes sized by\nNARRATIVE NEED\nvalidated vs min/max", False),
    (81, "168 h is an OUTPUT\n~450 scene files", False),
]

chain(axB, OLD, 6.2, C["old"], "AS BUILT — budget derived from LLM throughput", "#ff6a6a")
chain(axB, NEW, 2.0, C["new"], "CORRECTED — budget derived from story duration", "#5fd08a")

axB.text(45, 4.15, "the editorial question was never asked",
         ha="center", fontsize=10.5, color="#ff8080", style="italic")

# outcome callouts
axB.add_patch(FancyBboxPatch((90.5, 5.0), 18.5, 2.4, boxstyle="round,pad=0.15",
                             facecolor="#3a1a1a", edgecolor="#e04040",
                             linewidth=1.8, alpha=0.95))
axB.text(99.75, 6.2, "2,671 files\n0 new scenes", ha="center", va="center",
         fontsize=12, color="#ff8080", weight="bold")

axB.add_patch(FancyBboxPatch((90.5, 0.8), 18.5, 2.4, boxstyle="round,pad=0.15",
                             facecolor="#153a24", edgecolor="#40c080",
                             linewidth=1.8, alpha=0.95))
axB.text(99.75, 2.0, "~450 scenes\n~198 spine / ~250 ambient",
         ha="center", va="center", fontsize=12, color="#80e0a0", weight="bold")

axB.text(45, -0.75,
         "Ambient definitions are not airings: ~250 definitions x ~10 airings each = 128 h, "
         "reworded every time by variant pools + improv at render time.",
         ha="center", fontsize=10, color="#98a0b0", style="italic")

legend = [
    Patch(facecolor=C["spine"], label="Spine (plot, sized by need)"),
    Patch(facecolor=C["ambient"], label="Ambient (elastic filler)"),
    Patch(facecolor=C["descent"], label="Descent"),
    Patch(facecolor=C["keystone"], label="Keystone"),
    Patch(facecolor=C["ascent"], label="Ascent"),
]
axB.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, -0.05),
           fontsize=10, facecolor="#1c1f28", edgecolor="#555",
           labelcolor="white", ncol=5)

axB.set_xlim(-2, 111)
axB.set_ylim(-1.6, 8.6)
axB.axis("off")

out = "docs/generator_pacing_model.png"
plt.savefig(out, dpi=140, facecolor=fig.get_facecolor(), bbox_inches="tight")
print(f"wrote {out}")
