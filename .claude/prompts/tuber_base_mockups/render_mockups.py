#!/usr/bin/env python3
"""
render_mockups.py — throwaway prototype renderer for the tuber_base Stats
(radar chart) and Knowledge (node/edge graph) panes. NOT wired into the
real pane scripts; purely for picking a visual style before touching
app/radar_pane.py / app/knowledge_graph_pane.py.

Usage: python3 render_mockups.py [variant ...]
No args = render everything.
"""
import math
import sys


# ── tiny character-grid canvas + Bresenham line, shared by all variants ────
class Canvas:
    def __init__(self, w, h, fill=" "):
        self.w, self.h = w, h
        self.grid = [[fill] * w for _ in range(h)]

    def set(self, x, y, ch):
        x, y = round(x), round(y)
        if 0 <= x < self.w and 0 <= y < self.h:
            self.grid[y][x] = ch

    def text(self, x, y, s):
        for i, ch in enumerate(s):
            self.set(x + i, y, ch)

    def line(self, x0, y0, x1, y1, ch):
        x0, y0, x1, y1 = round(x0), round(y0), round(x1), round(y1)
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.set(x0, y0, ch)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def render(self):
        return "\n".join("".join(row) for row in self.grid)


# ── sample metrics, same shape as the real METRIC_SPECS in radar_pane.py ───
SAMPLE_METRICS = [
    ("tok/s", 0.62),
    ("latency", 0.35),
    ("context", 0.71),
    ("err%", 0.08),
    ("uptime%", 0.95),
    ("msgs", 0.44),
]

ASPECT = 0.5  # monospace chars are ~2x taller than wide; compress Y


# ── RADAR VARIANT A: hexagon polygon on a rings backdrop ───────────────────
def radar_variant_a(worker_id="coder", metrics=SAMPLE_METRICS, w=37, h=20):
    cx, cy = w / 2, h / 2
    max_r = min(cx, cy) - 3
    n = len(metrics)
    c = Canvas(w, h)

    # background rings at 25/50/75/100%
    for frac, ch in [(0.25, "."), (0.5, "."), (0.75, "."), (1.0, ".")]:
        r = max_r * frac
        for deg in range(0, 360, 4):
            a = math.radians(deg)
            c.set(cx + r * math.sin(a), cy - r * math.cos(a) * ASPECT, ch)

    # axis spokes + polygon vertices
    pts = []
    for i, (label, frac) in enumerate(metrics):
        a = 2 * math.pi * i / n
        vx, vy = math.sin(a), -math.cos(a)
        # spoke to max radius
        c.line(cx, cy, cx + vx * max_r, cy + vy * max_r * ASPECT, ":")
        r = max_r * max(0.03, frac)
        px, py = cx + vx * r, cy + vy * r * ASPECT
        pts.append((px, py))
        # label beyond the spoke tip
        lx, ly = cx + vx * (max_r + 1.5), cy + vy * (max_r + 1.2) * ASPECT
        lx -= len(label) / 2 if abs(vx) < 0.3 else (0 if vx > 0 else len(label) - 1)
        c.text(lx, ly, label)

    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        c.line(x0, y0, x1, y1, "#")
    c.set(cx, cy, "+")

    header = f"[{worker_id}] stats (live)"
    return header + "\n" + c.render()


# ── RADAR VARIANT B: same polygon, braille-density canvas (2x4 sub-cells) ──
BRAILLE_OFFSET = 0x2800
DOT_MAP = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


class BrailleCanvas:
    def __init__(self, cols, rows):
        # cols/rows are CHARACTER cells; actual dot grid is 2x wide, 4x tall
        self.cols, self.rows = cols, rows
        self.cells = {}

    def set(self, x, y):
        x, y = round(x), round(y)
        cx, cy = x // 2, y // 4
        if not (0 <= cx < self.cols and 0 <= cy < self.rows):
            return
        dx, dy = x % 2, y % 4
        bit = DOT_MAP[dy][dx]
        self.cells[(cx, cy)] = self.cells.get((cx, cy), 0) | bit

    def line(self, x0, y0, x1, y1):
        # supersample in dot-space (2x/4x resolution vs the char grid)
        x0, y0, x1, y1 = x0 * 2, y0 * 4, x1 * 2, y1 * 4
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for s in range(steps + 1):
            t = s / steps if steps else 0
            self.set(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)

    def render(self):
        lines = []
        for cy in range(self.rows):
            row = []
            for cx in range(self.cols):
                bits = self.cells.get((cx, cy), 0)
                row.append(chr(BRAILLE_OFFSET + bits) if bits else " ")
            lines.append("".join(row))
        return "\n".join(lines)


def radar_variant_b(worker_id="coder", metrics=SAMPLE_METRICS, cols=34, rows=16):
    cx, cy = cols, rows * 2  # dot-space center (2x/4x sub-res of char grid)
    max_r = min(cx, cy) - 6
    n = len(metrics)
    bc = BrailleCanvas(cols, rows)

    for frac in (0.33, 0.66, 1.0):
        r = max_r * frac
        prev = None
        for deg in range(0, 361, 6):
            a = math.radians(deg)
            pt = (cx / 2 + r * math.sin(a) / 2, cy / 4 + r * math.cos(a) * ASPECT / 4)
            if prev:
                bc.line(prev[0], prev[1], pt[0], pt[1])
            prev = pt

    pts = []
    for i, (label, frac) in enumerate(metrics):
        a = 2 * math.pi * i / n
        vx, vy = math.sin(a), -math.cos(a)
        r = max_r * max(0.03, frac)
        px = cx / 2 + vx * r / 2
        py = cy / 4 + vy * r * ASPECT / 4
        pts.append((px, py))
        bc.line(cx / 2, cy / 4, px, py)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        bc.line(x0, y0, x1, y1)

    body = bc.render()
    label_row = "  ".join(f"{l}" for l, _ in metrics)
    header = f"[{worker_id}] stats (live) — braille"
    return header + "\n" + body + "\n" + label_row


# ── RADAR VARIANT C: current bar-chart, unchanged (baseline for comparison)─
def radar_variant_c_baseline(worker_id="coder", metrics=SAMPLE_METRICS):
    lines = [f"[{worker_id}] stats (live) — current bar baseline"]
    for label, frac in metrics:
        filled = round(frac * 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"{label:<8}{bar} {frac*100:.0f}%")
    return "\n".join(lines)


# ── KNOWLEDGE VARIANT A: circular layout, straight edges ───────────────────
SAMPLE_GRAPH = {
    "nodes": ["Kael", "Ashiorid", "Malvakar", "Riddle", "Vault", "Ring"],
    "edges": [("Kael", "Ashiorid"), ("Ashiorid", "Malvakar"), ("Malvakar", "Riddle"),
              ("Riddle", "Vault"), ("Vault", "Ring"), ("Ring", "Kael"),
              ("Kael", "Malvakar")],
}


def knowledge_variant_a(graph=SAMPLE_GRAPH, w=37, h=26):
    nodes = graph["nodes"]
    n = len(nodes)
    cx, cy = w / 2, h / 2
    r = min(cx, cy) - 4
    pos = {}
    for i, name in enumerate(nodes):
        a = 2 * math.pi * i / n - math.pi / 2
        pos[name] = (cx + r * math.cos(a), cy + r * math.sin(a) * ASPECT)

    c = Canvas(w, h)
    for a, b in graph["edges"]:
        x0, y0 = pos[a]
        x1, y1 = pos[b]
        c.line(x0, y0, x1, y1, "-")
    for name, (x, y) in pos.items():
        label = f"({name[:6]})"
        c.text(x - len(label) / 2, y, label)

    header = "Knowledge Graph (placeholder) — circular layout"
    return header + "\n" + c.render()


# ── KNOWLEDGE VARIANT B: layered tree (root at top, generations below) ─────
SAMPLE_TREE = {
    "root": "Ashiorid",
    "layers": [
        ["Ashiorid"],
        ["Malvakar", "Kael"],
        ["Riddle", "Vault", "Ring"],
    ],
    "edges": [("Ashiorid", "Malvakar"), ("Ashiorid", "Kael"),
              ("Malvakar", "Riddle"), ("Malvakar", "Vault"), ("Kael", "Ring")],
}


def knowledge_variant_b(tree=SAMPLE_TREE, w=37, h=20):
    layers = tree["layers"]
    row_h = h // len(layers)
    pos = {}
    c = Canvas(w, h)
    for li, layer in enumerate(layers):
        y = row_h * li + 1
        step = w / (len(layer) + 1)
        for i, name in enumerate(layer):
            x = step * (i + 1)
            pos[name] = (x, y)
    for a, b in tree["edges"]:
        x0, y0 = pos[a]
        x1, y1 = pos[b]
        c.line(x0, y0, x1, y1, "|" if x0 == x1 else "/")
    for name, (x, y) in pos.items():
        label = f"[{name[:8]}]"
        c.text(max(0, x - len(label) / 2), y, label)

    header = "Knowledge Graph (placeholder) — layered tree layout"
    return header + "\n" + c.render()


VARIANTS = {
    "radar-a": lambda: radar_variant_a(),
    "radar-b": lambda: radar_variant_b(),
    "radar-c": lambda: radar_variant_c_baseline(),
    "knowledge-a": lambda: knowledge_variant_a(),
    "knowledge-b": lambda: knowledge_variant_b(),
}


def main(argv):
    names = argv or list(VARIANTS.keys())
    for name in names:
        if name not in VARIANTS:
            print(f"unknown variant: {name}", file=sys.stderr)
            continue
        print(f"\n===== {name} =====")
        print(VARIANTS[name]())


if __name__ == "__main__":
    main(sys.argv[1:])
