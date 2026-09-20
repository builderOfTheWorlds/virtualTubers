"""Render the live Agent D&D architecture diagram.

Companion to .claude/prompts/agent_dnd_architecture.md (DRAFT v1.0,
2026-09-20). House style matches scripts/render_architecture_graph.py:
manual top-to-bottom layout, dark theme, group-colored rounded boxes,
curved labeled arrows.

Run:
    .venv/bin/python scripts/render_agent_dnd_graph.py            # render
    .venv/bin/python scripts/render_agent_dnd_graph.py --check    # validate
--check asserts no label overlaps another label or a node box, and exits
non-zero on any violation. The geometry in `edges` / `notes` is tuned
against this checker — run it after hand edits before trusting a render.

Out: docs/agent_dnd_architecture.png
"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

# ---- nodes: name -> (x, y, group) ----------------------------------------
OPER  = "Operator\n(control-panel :8091 · message-api :8090)"
BENCH = "BENCHMARK — W0 gate, deferred §13\nprefill/decode tok/s @ 8k-16k ctx · line latency\n" \
        "time-per-round · concurrency · KV-cache GB\n→ picks Plan A / B / C + freezes num_ctx + deadlines"
GEN   = "offline 3-layer generator (GM's PREP, §9.1)\nplan_arc · plan_segment · take library ·\n" \
        "spine chains · ambient bank — NOT the live dialogue engine"
GM    = "worker-gm  (tuber_0)\n├ TURN ARBITER — app/turns.py (NEW)\n│    single ordering authority · scene FSM\n" \
        "├ GM AGENT — hermes3:70b (40 GB)\n│    full pack · all lore · all sheets · canon contract"
CHAR  = "worker-tuber_1 … tuber_6  (one agent each)\n├ CHARACTER AGENT — gemma4:12b ×4 + qwen3.8:27b ×2 (Plan A)\n│    scoped knowledge only: sheet + unlocked lore +\n" \
        "│    committed transcript (no neighbor peeks)\n└ channel — live tmux: Sheet · Inbox · Reasoning · Tool"
KAFKA = "Kafka · vtuber.messages (one topic)\nscene_direction · turn_assignment · character_reply\n" \
        "retake · gm_overrule · adjudication · scene_resolve\n" \
        "TRANSPORT + AUDIT LOG ONLY — never the source of order.\n" \
        "message-logger archives every turn (incl. overrule trail)"
REDIS = "world-state (Redis · shared named vol)\nscene_id · round · whose-turn\n" \
        "committed-transcript ptr · state_delta (§5.4)"
APG   = "App Postgres (192.168.1.120:5432)\nreplay_episodes ← record at scene_resolve; validator\n" \
        "runs at record time (§7.3) · messages archive\nlegacy replay path = FALLBACK + ARCHIVE (§9.2)"

nodes = {
    GEN:    (2.6, 11.3, "gen"),
    BENCH:  (9.0, 11.3, "bridge"),
    OPER:   (16.2, 11.3, "actor"),
    GM:     (2.6, 8.4, "gm"),
    CHAR:   (9.0, 8.4, "char"),
    REDIS:  (2.6, 5.4, "infra"),
    KAFKA:  (9.0, 5.4, "infra"),
    APG:    (9.0, 2.4, "infra"),
}

def P(n):
    return nodes[n][:2]

def box_dims(n):
    lines = n.count("\n")
    return 5.8, max(0.62, 0.55 + 0.34 * lines)

# ---- edges ----------------------------------------------------------------
# (from, to, label, off, dy, dx, rad, dashed) — label center =
# midpoint(off) + (dx, dy). All centers sit in verified clear bands;
# re-run --check after edits.
edges = [
    (GEN, GM, "scene contracts · spine scenes · carry  (read via load_pack, pack rows)",
        0.5, 0.0, 0.0, 0.0, False),
    (OPER, BENCH, "W0",
        0.5, 0.2, -0.4, -0.3, True),
    (BENCH, GM, "picks Plan A/B/C, brain roster; freezes num_ctx + deadlines (§13→§8)",
        0.5, 0.1, 2.8, 0.1, True),
    (OPER, KAFKA, "operator_override: accept_all · skip · abort (§7.4)",
        0.5, 0.0, 1.4, 0.15, True),
    (GM, KAFKA, "publishes the §4.2 turn protocol (all 7 bus message types)",
        0.5, 0.05, 0.0, 0.0, False),
    (KAFKA, CHAR, "delivers turn_assignment to ONE seat — transcript BY VALUE (§4.3)",
        0.473, 0.0, 1.5, 0.2, False),
    (CHAR, KAFKA, "character_reply → committed only when arbiter validates (§5.3)",
        0.5, -0.25, 7.6, -0.25, False),
    (GM, REDIS, "scene state r/w (§5.4)",
        0.5, -0.4, -1.45, 0.0, False),
    (KAFKA, APG, "scene_resolve → message-api (sole writer) → replay_episodes record\n"
                 "validator runs at record time (§7.3)",
        0.56, 0.0, 0.0, 0.0, False),
    (APG, CHAR, "(fallback) recorded episode → legacy replay path, unchanged (§7.1 · §9.2)",
        0.5, -4.5, 0.0, 0.5, True),
]

# free-floating notes: (text, x, y)
notes = [
    ("ARBITER LOOPS ITSELF — GM adjudicates vs scene_contract → retake (≤2, specific reason)\n"
     "→ gm_overrule commits a canon line  ·  the scene NEVER stalls (§5.2)",
     2.4, 3.81),
]

# ---- collision checker ------------------------------------------------------
def est_rect(cx, cy, text):
    w = max(len(line) for line in text.split("\n")) * 0.056 + 0.34
    h = len(text.split("\n")) * 0.165 + 0.22
    return (cx - w/2, cy - h/2, w, h)

def overlaps(a, b, pad=0.06):
    return not (a[0] + a[2] < b[0] - pad or b[0] + b[2] < a[0] - pad or
                a[1] + a[3] < b[1] - pad or b[1] + b[3] < a[1] - pad)

def label_rect(e):
    a, b, lbl, off, dy, dx, rad, dash = e
    xa, ya = P(a)
    xb, yb = P(b)
    return est_rect(xa + (xb - xa) * off + dx, ya + (yb - ya) * off + dy, lbl)

def rect_of_node(n):
    cx, cy, _ = nodes[n]
    w, h = box_dims(n)
    return (cx - w/2, cy - h/2, w, h)

if "--check" in sys.argv:
    rects = [(e[0].split("\n")[0][:12] + "→" + e[1].split("\n")[0][:12], label_rect(e))
             for e in edges]
    rects += [("note:" + t[:12], est_rect(x, y, t)) for t, x, y in notes]
    bad = 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if overlaps(rects[i][1], rects[j][1]):
                print("LABEL-LABEL:", rects[i][0], "vs", rects[j][0]); bad += 1
    for name, r in rects:
        if r[0] < -0.8 or r[0] + r[2] > 19.4 or r[1] < -0.3 or r[1] + r[3] > 12.5:
            print("OFF-CANVAS:", name); bad += 1
        for n in nodes:
            if overlaps(r, rect_of_node(n)):
                print("LABEL-BOX:", name, "in", n.split("\n")[0][:24]); bad += 1
    if bad:
        print(f"{bad} overlap(s) — fix the geometry in `edges` / `notes`"); sys.exit(1)
    print("OK — no label/label, label/box overlaps; all on canvas")
    sys.exit(0)

# ---- render ------------------------------------------------------------------
group_colors = {
    "actor":  "#555555", "gm": "#8a4a1f", "char": "#1f5fa8",
    "infra":  "#8a6d1f", "gen": "#5a3a8a", "bridge": "#a83232",
}

fig, ax = plt.subplots(figsize=(22, 13.5))
ax.set_facecolor("#12121a"); fig.patch.set_facecolor("#12121a")

def draw_label(cx, cy, text, ec="#555566", lc="#d8d8d8"):
    ax.text(cx, cy, text, fontsize=6.8, color=lc, ha="center", va="center",
            zorder=2, bbox=dict(boxstyle="round,pad=0.16", fc="#191924",
                                ec=ec, lw=0.7, alpha=0.94))

for a, b, lbl, off, dy, dx, rad, dash in edges:
    arrow = FancyArrowPatch(
        P(a), P(b), connectionstyle=f"arc3,rad={rad}",
        arrowstyle="-|>", mutation_scale=14, color="#999999", linewidth=1.1,
        alpha=0.85, zorder=1, linestyle=(0, (5, 4)) if dash else "solid",
        shrinkA=24, shrinkB=24)
    ax.add_patch(arrow)
    mx = P(a)[0] + (P(b)[0] - P(a)[0]) * off + dx
    my = P(a)[1] + (P(b)[1] - P(a)[1]) * off + dy
    draw_label(mx, my, lbl)

for t, x, y in notes:
    draw_label(x, y, t, ec="#8a4a1f", lc="#e8e0d0")

for n, (x, y, grp) in nodes.items():
    w, h = box_dims(n)
    box = FancyBboxPatch((x - w/2, y - h/2), w, h,
                         boxstyle="round,pad=0.02,rounding_size=0.08",
                         linewidth=1.4, edgecolor="white",
                         facecolor=group_colors[grp], zorder=3)
    ax.add_patch(box)
    ax.text(x, y, n, ha="center", va="center", fontsize=7.2, color="white",
            fontweight="bold", zorder=4, linespacing=1.45)

legend_elems = [
    Line2D([0], [0], marker="s", color="w", markerfacecolor=c, markersize=13,
           label=l, linewidth=0)
    for l, c in [
        ("Operator / gate", group_colors["actor"]),
        ("GM agent + turn arbiter (tuber_0) — NEW authority", group_colors["gm"]),
        ("Character agents × 6 (one brain each) — NEW", group_colors["char"]),
        ("Shared infra (bus / state / store) — REUSED", group_colors["infra"]),
        ("Offline generator — survives as GM PREP", group_colors["gen"]),
        ("Benchmark — deferred (decides §8 allocation)", group_colors["bridge"]),
    ]
]
ax.legend(handles=legend_elems, loc="lower center", ncol=3, fontsize=9.5,
          facecolor="#1c1c26", edgecolor="white", labelcolor="white",
          bbox_to_anchor=(0.5, -0.03))

ax.set_title("virtualTubers — Live Agent D&D  (design doc v1.0 · solid = required flow · dashed = gate / fallback)",
             fontsize=15, color="white", pad=14)
ax.set_xlim(-0.8, 19.4)
ax.set_ylim(-0.3, 12.5)
ax.axis("off")
plt.tight_layout()
out = "/home/secus/codeProjects/virtualTubers/docs/agent_dnd_architecture.png"
plt.savefig(out, dpi=150, facecolor=fig.get_facecolor())
print("saved:", out)
