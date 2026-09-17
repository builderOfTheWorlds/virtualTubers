"""Render a visual architecture/data-flow graph for virtualTubers.

Manual layout (not force-directed) so the diagram reads top-to-bottom like
the Mermaid version (docs/architecture_flow_diagram.md), but as a PNG for
people who don't have Mermaid.

2026-09 code-audit corrections applied (trust code, not docs):
- Redis: on/off flags + log-filter excludes (NOT world-state — that's a file)
- message-logger archives messages + voiced_narration(text) + coding_backend_runs
- message-api is the sole writer of replay_episodes; bridge routes POST /replays
- Every worker reads episodes (Rerun Theater), not just the GM
- GM's TILE_RELAY_DIR relay is intra-container (its own 8 tiles); cross-worker
  duet shows use the Kafka replay_invite/ready/cue/end protocol
"""
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

G = nx.DiGraph()

# ---- node definitions: name -> (x, y, group) ------------------------------
# groups: live, worker, infra, gen, bridge
OPER = "Operator"
CP = "control-panel\n:8091 · /replays upload UI"
MAPI = "message-api\n:8090 · sole Kafka publisher\nsole INSERT of replay_episodes"
TP = "twitch-presence"
KAFKA = "Kafka\nvtuber.messages (one topic)"
REDIS = "Redis\non/off flags + log-filter\n(reads fail-open)"
MLOG = "message-logger\nsole bus archive"
LSHIP = "log-shipper\n(docker.sock logs)"
APG = "App Postgres\nvirtualtubers @ 192.168.1.120:5432\nmessages · replay_episodes ·\nvoiced_narration · container_logs ·\ncoding_backend_runs"
CODER = "worker-coder\n(+ -native/-opencode/-aider)"
MGR = "worker-manager"
TEST = "worker-tester"
GM = "worker-gm (tuber_0)\ndirector · LLM+TTS all speakers\nhosts ALL 8 tiles locally"
STREAM = "per-worker streaming stack\nXvfb + tmux + PulseAudio + ffmpeg\n→ 6 Twitch channels"
CAMPM = "campaign-manager\n:8082 (job GUI)"
GEN = "3layer-generator\n:8001 · Arc→Segment→Dialogue"
PACKS = "campaigns/*.yaml\n(pack input)"
GPG = "Gen Postgres\ngeneration @ 127.0.0.1:5455"
OUT = "output/ dialogue\nlibrary"
BRIDGE = "MANUAL upload paths (human-run)\nbuild_campaign_episode · build_generated\nbuild_roundtable_test · build_replay_library\n+ control-panel /replays/upload"

nodes = {
    OPER:   (1.2, 10.2, "actor"),
    CP:     (2.8, 8.6, "live"),
    MAPI:   (6.2, 8.6, "live"),
    TP:     (9.6, 8.6, "live"),
    LSHIP:  (12.8, 8.6, "live"),

    MLOG:   (2.8, 6.6, "live"),
    KAFKA:  (6.2, 6.6, "infra"),
    REDIS:  (9.8, 6.6, "infra"),

    CODER:  (1.4, 4.6, "worker"),
    MGR:    (3.8, 4.6, "worker"),
    TEST:   (6.2, 4.6, "worker"),
    GM:     (9.0, 4.6, "worker"),

    APG:    (3.6, 2.4, "infra"),
    STREAM: (8.6, 2.4, "live"),

    CAMPM:  (15.2, 8.6, "gen"),
    PACKS:  (17.8, 8.6, "gen"),
    GEN:    (15.2, 6.6, "gen"),
    GPG:    (17.8, 6.6, "gen"),
    OUT:    (15.2, 4.6, "gen"),

    BRIDGE: (3.6, 0.7, "bridge"),
}

edges = [
    (OPER, CP, "instructions", None, None),
    (OPER, MAPI, "curl POST /messages", 0.75, None),
    (CP, MAPI, "HTTP", 0.5, None),
    (TP, MAPI, "viewer_joined (REST) → any worker may\nauto-pick a random episode (agent.py:712)", 0.5, 0.42),
    (MAPI, KAFKA, "publish · sole producer", 0.5),
    (KAFKA, CODER, "consume / produce", 0.38),
    (KAFKA, MGR, "consume / produce", 0.45),
    (KAFKA, TEST, "consume / produce + duet relay\nreplay_invite·ready·cue·end (agent.py:955-1049)", 0.5),
    (KAFKA, GM, "consume / produce", 0.62),
    (KAFKA, MLOG, "consume", 0.5),
    (MAPI, APG, "sole INSERT · replay\n(episode_store.py:55)", 1.0, -0.7),
    (MLOG, APG, "messages · voiced_narration(text) · coding_backend_runs", 0.35),
    (LSHIP, APG, "container_logs (+ retention)", 0.3),
    (MAPI, REDIS, "set flags + log-filter excludes", 0.5),
    (GM, REDIS, "flag r/w (all workers)", 0.55),
    (MLOG, REDIS, "reads log-filter excludes", 0.45),
    (CODER, APG, "narration cache r/w · episode READ", 0.6),
    (MGR, APG, "narration cache r/w · episode READ", 0.62),
    (TEST, APG, "narration cache r/w · episode READ", 0.64),
    (GM, APG, "narration cache r/w · episode READ", 0.66),
    (GM, GM, "TILE_RELAY_DIR → its OWN 8 tiles (local)", 0.0),
    (CODER, STREAM, "tmux capture", 0.55),
    (GM, STREAM, "tmux capture · hosts 8 seats", 0.6),
    (CAMPM, GEN, "submit / watch job (HTTP)", 0.5),
    (PACKS, GEN, "reads pack", 0.55),
    (GEN, GPG, "job state", 0.5),
    (GEN, OUT, "writes", 0.5),
    (OUT, BRIDGE, "run artifacts (or authored pack)", 0.25),
    (BRIDGE, MAPI, "POST /replays (urllib / curl) — all manual routes", 0.12),
]

for n, (x, y, grp) in nodes.items():
    G.add_node(n, pos=(x, y), group=grp)
for a, b, lbl, *rest in edges:
    off = rest[0] if rest and rest[0] is not None else 0.5
    dy = rest[1] if len(rest) > 1 else None
    G.add_edge(a, b, label=lbl, off=off, dy=dy)

group_colors = {
    "actor":  "#555555",
    "live":   "#2f6f4f",
    "worker": "#1f5fa8",
    "infra":  "#8a6d1f",
    "gen":    "#5a3a8a",
    "bridge": "#a83232",
}
group_text = "white"

fig, ax = plt.subplots(figsize=(21, 12.5))
ax.set_facecolor("#12121a")
fig.patch.set_facecolor("#12121a")

pos = nx.get_node_attributes(G, "pos")

# draw edges as curved arrows with labels
for a, b, data in G.edges(data=True):
    xa, ya = pos[a]
    xb, yb = pos[b]
    off = data.get("off", 0.5) or 0.5
    if a == b:  # self-loop (GM local tile relay)
        rad = 0.9
    else:
        same_col = abs(xa - xb) < 0.3
        rad = 0.10 if not same_col else 0.18
    arrow = FancyArrowPatch(
        (xa, ya), (xb, yb),
        connectionstyle=f"arc3,rad={rad}",
        arrowstyle="-|>", mutation_scale=14,
        color="#999999", linewidth=1.1, alpha=0.85, zorder=1,
        shrinkA=30, shrinkB=30,
    )
    ax.add_patch(arrow)
    # label position: off=0.5 midpoint, <0.5 toward source, >0.5 toward target
    mx = xa + (xb - xa) * off
    my = ya + (yb - ya) * off
    if a == b:
        mx, my = xa, ya + 0.95
    elif data.get("dy") is not None:
        my += data["dy"]
    ax.text(mx, my, data["label"], fontsize=6.3, color="#cfcfcf",
            ha="center", va="center", zorder=2,
            bbox=dict(boxstyle="round,pad=0.12", fc="#12121a", ec="none", alpha=0.75))

# draw nodes as rounded boxes
for n, (x, y) in pos.items():
    grp = G.nodes[n]["group"]
    color = group_colors[grp]
    w, h = 2.05, 0.62
    box = FancyBboxPatch((x - w/2, y - h/2), w, h,
                         boxstyle="round,pad=0.02,rounding_size=0.08",
                         linewidth=1.4, edgecolor="white", facecolor=color, zorder=3)
    ax.add_patch(box)
    lines = n.count("\n")
    ax.text(x, y, n, ha="center", va="center",
            fontsize=8.6 if lines <= 1 else 7.4, color=group_text,
            fontweight="bold", zorder=4)

legend_elems = [
    Line2D([0], [0], marker="s", color="w", markerfacecolor=c, markersize=14, label=lbl, linewidth=0)
    for lbl, c in [
        ("Actor", group_colors["actor"]),
        ("Live-stream service", group_colors["live"]),
        ("Worker (Kafka producer/consumer)", group_colors["worker"]),
        ("Shared infra (bus / db / flags)", group_colors["infra"]),
        ("Offline generator stack (isolated)", group_colors["gen"]),
        ("Manual upload paths (human-run)", group_colors["bridge"]),
    ]
]
ax.legend(handles=legend_elems, loc="lower center", ncol=3, fontsize=10,
          facecolor="#1c1c26", edgecolor="white", labelcolor="white",
          bbox_to_anchor=(0.5, -0.05))

ax.set_title("virtualTubers — Component & Data Flow (code-verified, 2026-09)",
             fontsize=16, color="white", pad=14)
ax.set_xlim(-0.8, 19.2)
ax.set_ylim(-0.2, 11.0)
ax.axis("off")

plt.tight_layout()
out_path = "/home/secus/codeProjects/virtualTubers/docs/architecture_flow_diagram.png"
plt.savefig(out_path, dpi=170, facecolor=fig.get_facecolor())
print("saved:", out_path)
