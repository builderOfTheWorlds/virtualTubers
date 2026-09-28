"""Render the roundtable monetization strategy map (roundtable_monetization_playbook.md v1.1) to PNG."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

BG, EDGE, TXT = "#0f172a", "#94a3b8", "white"
NODES = {
    "GEN": (1.2, 3.0, "3-layer generator\narc -> segments -> slots\n= the schedule (D4)", "#334155"),
    "AIR": (3.6, 3.0, "OB-30 day runner\nOB-33 off-hours\nAIRING = BLOCKER", "#b91c1c"),
    "S1": (6.0, 4.4, "S1 Metadata hooks\nnow_airing, break slots\ncoverage check", "#1e3a8a"),
    "S4": (6.0, 1.6, "S4 Chat (free)\n!a/!b fork votes, !coffee\n!client, POV !ask", "#1e3a8a"),
    "S2": (8.4, 4.4, "S2 Packaging\nSoftware & Game Dev\nAI + REPLAY/LIVE", "#1e3a8a"),
    "S3": (10.8, 4.4, "S3 9-channel funnel\n8 POV cams -> main\nYouTube, clips, raids", "#1e3a8a"),
    "AFF": (10.8, 1.6, "AFFILIATE\navg 3 viewers\n(check dashboard)", "#15803d"),
    "S5": (13.2, 4.4, "S5 Paid levers\nBits complications\nsub pipeline, points", "#a16207"),
    "S6": (13.2, 1.6, "S6 Ads\nonly at generated\nbreak slots", "#a16207"),
    "S7": (15.6, 3.0, "S7 Sunday review\nviewers per slot kind\n-> next week's arc", "#475569"),
}
EDGES = [("GEN", "AIR"), ("AIR", "S1"), ("AIR", "S4"), ("S1", "S2"), ("S2", "S3"),
         ("S3", "AFF"), ("S4", "AFF"), ("AFF", "S5"), ("AFF", "S6"), ("S5", "S7"), ("S6", "S7")]
W, H = 2.25, 1.1


def _anchor(name: str, other: str) -> tuple[float, float]:
    """Attach point on `name`'s box facing `other` (side if horizontal offset dominates, else top/bottom)."""
    x, y = NODES[name][:2]
    ox, oy = NODES[other][:2]
    if abs(ox - x) >= 0.5:
        return (x + W / 2, y) if ox > x else (x - W / 2, y)
    return (x, y + H / 2) if oy > y else (x, y - H / 2)


def main() -> Path:
    fig, ax = plt.subplots(figsize=(18, 6.5), facecolor=BG)
    ax.set_facecolor(BG)
    ax.set_xlim(-0.2, 17.0)
    ax.set_ylim(0.1, 5.9)
    ax.axis("off")
    for x, y, label, color in NODES.values():
        ax.add_patch(FancyBboxPatch((x - W / 2, y - H / 2), W, H, boxstyle="round,pad=0.05",
                                    fc=color, ec=EDGE, lw=1.2))
        ax.text(x, y, label, ha="center", va="center", color=TXT, fontsize=9.5, family="DejaVu Sans")
    for a, b in EDGES:
        ax.add_patch(FancyArrowPatch(_anchor(a, b), _anchor(b, a), arrowstyle="-|>",
                                     mutation_scale=14, color=EDGE, lw=1.3))
    s7x, s7y = NODES["S7"][:2]
    gx, gy = NODES["GEN"][:2]
    loop_y = 0.55
    ax.plot([s7x, s7x, gx], [s7y - H / 2, loop_y, loop_y], color=EDGE, lw=1.1, ls="--")
    ax.add_patch(FancyArrowPatch((gx, loop_y), (gx, gy - H / 2), arrowstyle="-|>",
                                 mutation_scale=14, color=EDGE, lw=1.1, ls="--"))
    ax.text(8.4, 0.3, "weekly loop: viewer metrics become generator guidance for next week's arc",
            color=EDGE, ha="center", fontsize=9)
    ax.text(8.4, 5.7, "Roundtable monetization v1.1 — airing (red) -> qualify (blue) -> earn (amber) -> measure",
            color=TXT, ha="center", fontsize=13, weight="bold")
    out = Path(__file__).with_name("roundtable_monetization_map.png")
    fig.savefig(out, dpi=120, facecolor=BG, bbox_inches="tight")
    return out


if __name__ == "__main__":
    print(main())
