"""Render the source-as-parameter pipeline (plan §6F) as a PNG.

Panel A: the repeatable process — any source corpus in, a week of content out,
         with the adapter layer making the source a parameter.
Panel B: the three run modes and what each touches.
Manual (x, y) layout per the codebase-visualization skill.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Patch

C = {
    "src": "#c08040",
    "adapt": "#b06030",
    "gen": "#8040c0",
    "gate": "#c0a020",
    "pack": "#4080c0",
    "air": "#40b878",
    "prov": "#30909a",
    "skip": "#606878",
    "new": "#40b060",
    "stale": "#c06030",
}

fig = plt.figure(figsize=(23, 15))
fig.patch.set_facecolor("#12141a")

# ================= Panel A =================
axA = fig.add_axes((0.03, 0.46, 0.94, 0.48))
axA.set_facecolor("#12141a")

axA.text(50, 11.6, "THE PIPELINE AS A PRODUCT — source is a PARAMETER, not a constant",
         ha="center", fontsize=17, color="white", weight="bold")
axA.text(50, 11.0,
         "Swap the corpus, get a different week. Edit the corpus, refresh only what went stale.",
         ha="center", fontsize=11, color="#98a0b0", style="italic")


def box(ax, cx, cy, w, h, label, fc, fs=9.0, ec="white"):
    ax.add_patch(FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                                boxstyle="round,pad=0.12",
                                facecolor=fc, edgecolor=ec,
                                linewidth=1.6, alpha=0.94))
    ax.text(cx, cy, label, ha="center", va="center", fontsize=fs,
            color="white", weight="bold")


def arrow(ax, p1, p2, color="#cccccc", style="-", label=None, lx=0.0, ly=0.0):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=16,
                                 linewidth=1.7, color=color, linestyle=style))
    if label:
        ax.text((p1[0] + p2[0]) / 2 + lx, (p1[1] + p2[1]) / 2 + ly, label,
                fontsize=7.6, color="#e8e8a0", ha="center", style="italic")


# sources (interchangeable)
box(axA, 11, 9.3, 19, 1.5, "Ashiorid Obsidian vault\n~40k words, 180 notes", C["src"], 9.2)
box(axA, 11, 7.4, 19, 1.3, "a DIFFERENT vault\n(next week's campaign)", C["src"], 9.0)
box(axA, 11, 5.7, 19, 1.3, "flat markdown / wiki\n/ transcripts", C["src"], 9.0)
axA.text(11, 4.62, "sources are interchangeable", ha="center", fontsize=8.6,
         color="#d0a070", style="italic")

# adapter
box(axA, 32, 7.4, 15, 2.6,
    "SOURCE ADAPTER\nload_source(path)\n-> SourceNote[]\nid/title/text/kind\n/rel_path/HASH", C["adapt"], 8.8)
for sy in (9.3, 7.4, 5.7):
    arrow(axA, (20.5, sy), (24.5, 7.4 + (sy - 7.4) * 0.25))

# generator stack
box(axA, 53, 9.5, 17, 1.35, "Layer 1  arc + ring\nring-shaped, paced", C["gen"], 8.8)
box(axA, 53, 7.6, 17, 1.35, "Layer 2  segment\nslots = scene specs", C["gen"], 8.8)
box(axA, 53, 5.7, 17, 1.35, "Layer 3'  author_scenes\nwrites scene YAML\n+ source: provenance", C["gen"], 8.4)
arrow(axA, (39.5, 7.9), (44.5, 9.5), label="notes + kind", ly=0.35)
arrow(axA, (53, 8.82), (53, 8.28))
arrow(axA, (53, 6.92), (53, 6.38))

# gate + pack
box(axA, 75, 7.6, 15, 2.0, "pack_gate.py\nload_pack() overlay\n+ --promote", C["gate"], 8.8)
arrow(axA, (61.5, 5.9), (67.5, 7.0))

box(axA, 93, 9.3, 14, 1.6, "campaigns/<pack>/\nscenes + lore\nTHE WEEK", C["pack"], 9.0)
box(axA, 93, 6.6, 14, 1.9, "provenance index\nnote hash -> scenes\n(enables --refresh)", C["prov"], 8.6)
arrow(axA, (82.5, 8.1), (86, 9.0))
arrow(axA, (82.5, 7.1), (86, 6.9))

# feedback loop
axA.add_patch(FancyArrowPatch((93, 5.6), (93, 3.4),
                              arrowstyle="-|>", mutation_scale=16,
                              linewidth=2.0, color="#40c0c0", linestyle="--"))
axA.add_patch(FancyArrowPatch((86, 3.0), (11, 3.0),
                              connectionstyle="arc3,rad=0.06",
                              arrowstyle="-|>", mutation_scale=16,
                              linewidth=2.0, color="#40c0c0", linestyle="--"))
axA.add_patch(FancyArrowPatch((11, 3.4), (11, 4.95),
                              arrowstyle="-|>", mutation_scale=16,
                              linewidth=2.0, color="#40c0c0", linestyle="--"))
axA.text(55, 2.35, "RE-RUN LOOP — edit the source, --refresh regenerates only what that edit touched",
         ha="center", fontsize=10.2, color="#60d0d0", weight="bold")

axA.set_xlim(-1, 103)
axA.set_ylim(1.9, 12.2)
axA.axis("off")

# ================= Panel B =================
axB = fig.add_axes((0.03, 0.03, 0.94, 0.37))
axB.set_facecolor("#12141a")

axB.text(50, 10.2, "THE THREE RUN MODES", ha="center", fontsize=15,
         color="white", weight="bold")

MODES = [
    (17, "--seed", "new pack from a corpus", C["new"], [
        ("everything generated", C["new"]),
        ("provenance recorded", C["prov"]),
        ("nothing pre-exists", C["skip"]),
    ]),
    (50, "--refresh", "source changed", C["stale"], [
        ("unchanged notes  -> SKIP", C["skip"]),
        ("changed notes    -> regenerate", C["stale"]),
        ("human-edited     -> NEVER touched", C["pack"]),
        ("new notes        -> insert (§6C)", C["new"]),
    ]),
    (83, "--densify", "no source change", C["air"], [
        ("find longest ambient gaps", C["air"]),
        ("author bridge scenes", C["new"]),
        ("contracts keep neighbours safe", C["gate"]),
    ]),
]

for cx, flag, sub, fc, rows in MODES:
    box(axB, cx, 8.3, 29, 1.7, f"{flag}\n{sub}", fc, 11.0)
    y = 6.6
    for text, rc in rows:
        axB.add_patch(FancyBboxPatch((cx - 14, y - 0.52), 28, 1.04,
                                     boxstyle="round,pad=0.07",
                                     facecolor=rc, edgecolor="none", alpha=0.82))
        axB.text(cx, y, text, ha="center", va="center", fontsize=8.6,
                 color="white", weight="bold")
        y -= 1.25

axB.text(50, 1.35,
         "Every mode prints a plan and changes nothing without --promote. "
         "A scene marked authored: human or human-edited is reported, never regenerated.",
         ha="center", fontsize=10.2, color="#98a0b0", style="italic")
axB.text(50, 0.45,
         "Honest limit: --refresh replaces whole scenes with NEW prose — it cannot diff-edit existing wording. "
         "The source corpus is the record, not the generated pack.",
         ha="center", fontsize=9.8, color="#ff9c9c", style="italic")

legend = [
    Patch(facecolor=C["src"], label="Source corpus (swappable)"),
    Patch(facecolor=C["adapt"], label="Adapter layer"),
    Patch(facecolor=C["gen"], label="Generator (campaign-agnostic)"),
    Patch(facecolor=C["gate"], label="Gate / promote"),
    Patch(facecolor=C["pack"], label="Pack output"),
    Patch(facecolor=C["prov"], label="Provenance index"),
]
axB.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, -0.03),
           fontsize=9.5, facecolor="#1c1f28", edgecolor="#555",
           labelcolor="white", ncol=6)

axB.set_xlim(-1, 101)
axB.set_ylim(-0.3, 10.8)
axB.axis("off")

out = "docs/generator_source_pipeline.png"
plt.savefig(out, dpi=140, facecolor=fig.get_facecolor(), bbox_inches="tight")
print(f"wrote {out}")
