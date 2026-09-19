"""Render the continuity-contract densification model (plan §6C/§6D).

Panel A: three waves of the same stretch of week — v1 spine-sparse,
         v2 one bridge inserted, v3 two bridges — showing ambient shrinking
         as plot is added, with contracts on the seams.
Panel B: the contract check performed on insertion.
Manual (x, y) layout per the codebase-visualization skill.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Patch

C = {
    "spine": "#e0654a",
    "ambient": "#4a90c0",
    "bridge": "#40b060",
    "contract": "#c0a020",
    "bad": "#c03030",
    "panel": "#1a1d26",
}

fig = plt.figure(figsize=(23, 14.5))
fig.patch.set_facecolor("#12141a")

# ================= Panel A =================
axA = fig.add_axes((0.035, 0.42, 0.93, 0.52))
axA.set_facecolor("#12141a")

# each wave: list of (kind, width, label)
WAVES = [
    ("WAVE 1  —  generate now:  ~40 h spine  /  ~130 h ambient", [
        ("spine", 9, "SPINE\nletos-manor"),
        ("ambient", 30, "AMBIENT  ~14 h\ncampfire / road-talk / night-watch\n(elastic filler)"),
        ("spine", 9, "SPINE\ngrovley"),
        ("ambient", 30, "AMBIENT  ~14 h\nthe-meal / carl-counts / bob-sings"),
        ("spine", 9, "SPINE\nthe-vault"),
    ]),
    ("WAVE 2  —  bridge one gap:  side quest inserted, neighbours untouched", [
        ("spine", 9, "SPINE\nletos-manor"),
        ("ambient", 13, "AMBIENT\n~6 h"),
        ("bridge", 11, "NEW SPINE\nsarahs-inn\n(bridge)"),
        ("ambient", 13, "AMBIENT\n~6 h"),
        ("spine", 9, "SPINE\ngrovley"),
        ("ambient", 30, "AMBIENT  ~14 h\n(untouched)"),
        ("spine", 9, "SPINE\nthe-vault"),
    ]),
    ("WAVE 3  —  keep densifying:  ambient shrinks, plot rises, week never rebuilt", [
        ("spine", 9, "SPINE\nletos-manor"),
        ("ambient", 13, "AMBIENT\n~6 h"),
        ("bridge", 11, "NEW SPINE\nsarahs-inn"),
        ("ambient", 13, "AMBIENT\n~6 h"),
        ("spine", 9, "SPINE\ngrovley"),
        ("ambient", 8, "AMB\n~3 h"),
        ("bridge", 11, "NEW SPINE\nmage-hole"),
        ("ambient", 8, "AMB\n~3 h"),
        ("spine", 9, "SPINE\nthe-vault"),
    ]),
]

y = 8.6
for title, blocks in WAVES:
    axA.text(0, y + 1.28, title, fontsize=12.5, color="white", weight="bold")
    x = 0.0
    for kind, w, label in blocks:
        axA.add_patch(FancyBboxPatch((x + 0.2, y - 0.55), w - 0.4, 1.55,
                                     boxstyle="round,pad=0.07",
                                     facecolor=C[kind], edgecolor="white",
                                     linewidth=1.4, alpha=0.94))
        fs = 8.0 if w < 12 else 8.8
        axA.text(x + w / 2, y + 0.22, label, ha="center", va="center",
                 fontsize=fs, color="white", weight="bold")
        # contract marker on each seam
        if x > 0.01:
            axA.plot([x + 0.2, x + 0.2], [y - 0.75, y + 1.2],
                     color=C["contract"], linewidth=2.4, zorder=6)
        x += w
    y -= 3.55

axA.text(50, 12.15, "PROGRESSIVE DENSIFICATION — intro/outro contracts make insertion safe",
         ha="center", fontsize=17, color="white", weight="bold")
axA.text(50, 11.55,
         "Every seam (gold) is a contract: predecessor's outro must satisfy successor's intro. "
         "New content slots in without regenerating neighbours.",
         ha="center", fontsize=10.5, color="#98a0b0", style="italic")

axA.set_xlim(-1, 101)
axA.set_ylim(0.6, 12.9)
axA.axis("off")

# ================= Panel B =================
axB = fig.add_axes((0.035, 0.03, 0.93, 0.33))
axB.set_facecolor("#12141a")

axB.text(50, 9.0, "THE INSERTION CHECK  (spine_chain.py, plan §6C.4)",
         ha="center", fontsize=15, color="white", weight="bold")

# A -> N -> B
NODES = [
    (14, "SCENE A\nletos-manor", C["spine"]),
    (50, "NEW SCENE N\nsarahs-inn", C["bridge"]),
    (86, "SCENE B\ngrovley", C["spine"]),
]
for cx, label, fc in NODES:
    axB.add_patch(FancyBboxPatch((cx - 10, 4.6), 20, 2.6,
                                 boxstyle="round,pad=0.14",
                                 facecolor=fc, edgecolor="white",
                                 linewidth=1.7, alpha=0.94))
    axB.text(cx, 5.9, label, ha="center", va="center", fontsize=11.5,
             color="white", weight="bold")

for a, b in ((14, 50), (50, 86)):
    axB.add_patch(FancyArrowPatch((a + 10, 5.9), (b - 10, 5.9),
                                  arrowstyle="-|>", mutation_scale=19,
                                  linewidth=2.0, color=C["contract"]))

# checks
CHECKS = [
    (32, "CHECK 1\nA.continuity_out\nmust satisfy\nN.continuity_in", C["contract"]),
    (68, "CHECK 2  (load-bearing)\nN.continuity_out\nmust satisfy\nB.continuity_in", C["contract"]),
]
for cx, label, fc in CHECKS:
    axB.add_patch(FancyBboxPatch((cx - 13, 1.1), 26, 2.9,
                                 boxstyle="round,pad=0.14",
                                 facecolor="#2a2a12", edgecolor=fc,
                                 linewidth=1.9, alpha=0.96))
    axB.text(cx, 2.55, label, ha="center", va="center", fontsize=9.6,
             color="#e8d070", weight="bold")
    axB.add_patch(FancyArrowPatch((cx, 4.0), (cx, 5.55),
                                  arrowstyle="-|>", mutation_scale=15,
                                  linewidth=1.6, color=fc, linestyle="--"))

axB.text(50, 0.1,
         "If N reveals something B still assumes is hidden, the story breaks silently — "
         "nothing downstream would catch it. That is why CHECK 2 exists.",
         ha="center", fontsize=10.2, color="#ff9090", style="italic")

legend = [
    Patch(facecolor=C["spine"], label="Existing spine (plot)"),
    Patch(facecolor=C["ambient"], label="Ambient (elastic, shrinks)"),
    Patch(facecolor=C["bridge"], label="New bridge scene"),
    Patch(facecolor=C["contract"], label="Continuity contract (seam)"),
]
axB.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, -0.02),
           fontsize=10, facecolor="#1c1f28", edgecolor="#555",
           labelcolor="white", ncol=4)

axB.set_xlim(-1, 101)
axB.set_ylim(-1.4, 9.6)
axB.axis("off")

out = "docs/generator_densification_model.png"
plt.savefig(out, dpi=140, facecolor=fig.get_facecolor(), bbox_inches="tight")
print(f"wrote {out}")
