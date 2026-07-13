"""HUD-style cyberpunk avatar layer generator.

Renders a Ghost in the Shell / tactical HUD face using PIL drawing.
Glowing wireframes, circuit traces, data readouts — not photorealistic.
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _blank(size):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _glow(img, radius=3, intensity=1.5):
    """Add bloom/glow effect to bright elements."""
    blurred = img.filter(ImageFilter.GaussianBlur(radius))
    arr = np.array(img, dtype=np.float32)
    blur_arr = np.array(blurred, dtype=np.float32)
    result = np.clip(arr + blur_arr * (intensity - 1.0), 0, 255).astype(np.uint8)
    return Image.fromarray(result, "RGBA")


# Color palette
CYAN = (0, 220, 200)
CYAN_BRIGHT = (0, 255, 240)
CYAN_DIM = (0, 120, 110)
TEAL = (0, 180, 160)
VIOLET = (100, 40, 180)
PURPLE = (60, 20, 120)
MAGENTA = (200, 0, 140)
RED = (220, 30, 30)
RED_DIM = (120, 15, 15)
WHITE = (240, 240, 240)
DARK = (8, 8, 15)


def _a(color, alpha=255):
    return (*color, alpha)


# ---------------------------------------------------------------------------
# Backgrounds
# ---------------------------------------------------------------------------

def generate_backgrounds(output_dir: Path, canvas_size=(512, 512)):
    output_dir.mkdir(parents=True, exist_ok=True)
    w, h = canvas_size

    for name, center_color, edge_color, grid_alpha in [
        ("bg_dim.png", (8, 12, 18), (4, 6, 10), 15),
        ("bg_pulse.png", (10, 18, 25), (4, 8, 12), 25),
        ("bg_error.png", (25, 6, 6), (12, 3, 3), 20),
    ]:
        img = Image.new("RGBA", canvas_size, (*edge_color, 255))
        draw = ImageDraw.Draw(img)

        # Radial gradient
        cx, cy = w // 2, h // 2
        for r in range(max(w, h) // 2, 0, -2):
            t = r / (max(w, h) // 2)
            c = tuple(int(center_color[i] * (1 - t) + edge_color[i] * t) for i in range(3))
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*c, 255))

        # Grid lines
        for x in range(0, w, 32):
            draw.line([(x, 0), (x, h)], fill=(0, 255, 200, grid_alpha), width=1)
        for y in range(0, h, 32):
            draw.line([(0, y), (w, y)], fill=(0, 255, 200, grid_alpha), width=1)

        img.save(output_dir / name)


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------

def generate_overlays(output_dir: Path, canvas_size=(512, 512)):
    output_dir.mkdir(parents=True, exist_ok=True)
    w, h = canvas_size

    # scanline_light
    img = _blank(canvas_size)
    arr = np.array(img)
    arr[::3, :] = (0, 0, 0, 25)
    Image.fromarray(arr, "RGBA").save(output_dir / "scanline_light.png")

    # scanline_heavy
    img = _blank(canvas_size)
    arr = np.array(img)
    arr[::2, :] = (0, 0, 0, 60)
    Image.fromarray(arr, "RGBA").save(output_dir / "scanline_heavy.png")

    # crt_bloom
    img = _blank(canvas_size)
    draw = ImageDraw.Draw(img)
    cx, cy = w // 2, h // 2
    for r in range(min(w, h) // 2, 0, -4):
        t = 1.0 - r / (min(w, h) // 2)
        a = int(t * t * 60)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 200, 200, a))
    img.save(output_dir / "crt_bloom.png")

    # holo_flicker
    img = _blank(canvas_size)
    draw = ImageDraw.Draw(img)
    rng = random.Random(13)
    for y in range(0, h, 8):
        if rng.random() > 0.4:
            a = rng.randint(10, 45)
            draw.rectangle([0, y, w, y + 3], fill=(0, 180, 200, a))
    img.save(output_dir / "holo_flicker.png")

    # chrom_aberr
    img = _blank(canvas_size)
    draw = ImageDraw.Draw(img)
    for y in range(0, h, 16):
        draw.rectangle([0, y, w, y + 2], fill=(200, 0, 60, 20))
        draw.rectangle([3, y + 8, w + 3, y + 10], fill=(0, 200, 200, 20))
    img.save(output_dir / "chrom_aberr.png")

    # glitch_corrupt
    img = _blank(canvas_size)
    draw = ImageDraw.Draw(img)
    rng = random.Random(42)
    for _ in range(35):
        bx = rng.randint(0, w - 60)
        by = rng.randint(0, h - 10)
        bw = rng.randint(20, 100)
        bh = rng.randint(2, 8)
        c = rng.choice([_a(RED, 80), _a(CYAN_BRIGHT, 60), _a(MAGENTA, 70)])
        draw.rectangle([bx, by, bx + bw, by + bh], fill=c)
    img.save(output_dir / "glitch_corrupt.png")

    # noise_bands
    img = _blank(canvas_size)
    arr = np.array(img)
    rng = np.random.default_rng(99)
    for y in range(0, h, 3):
        v = int(rng.integers(0, 70))
        a = int(rng.integers(10, 50))
        arr[y:y+2, :] = (v, v, v, a)
    Image.fromarray(arr, "RGBA").save(output_dir / "noise_bands.png")

    # red_tint
    Image.new("RGBA", canvas_size, _a(RED_DIM, 50)).save(output_dir / "red_tint.png")


# ---------------------------------------------------------------------------
# HUD Face Drawing
# ---------------------------------------------------------------------------

def _draw_hud_face_outline(draw, w, h, color=CYAN, alpha=200):
    """Draw the main face wireframe — oval with angular jaw."""
    cx, cy = w // 2, h // 2
    c = _a(color, alpha)
    dim = _a(CYAN_DIM, alpha // 2)

    # Face oval (upper half)
    face_w = int(w * 0.35)
    face_top = int(h * 0.08)
    face_mid = int(h * 0.50)

    # Upper face arc
    draw.arc([cx - face_w, face_top, cx + face_w, face_mid + int(h * 0.15)],
             start=180, end=360, fill=c, width=2)

    # Jawline — angular
    jaw_w = int(w * 0.33)
    jaw_y = int(h * 0.55)
    chin_w = int(w * 0.08)
    chin_y = int(h * 0.78)

    # Left jaw
    draw.line([(cx - face_w, face_mid - int(h * 0.03)), (cx - jaw_w, jaw_y)], fill=c, width=2)
    draw.line([(cx - jaw_w, jaw_y), (cx - chin_w, chin_y)], fill=c, width=2)
    # Chin
    draw.line([(cx - chin_w, chin_y), (cx, chin_y + 5)], fill=c, width=2)
    draw.line([(cx, chin_y + 5), (cx + chin_w, chin_y)], fill=c, width=2)
    # Right jaw
    draw.line([(cx + chin_w, chin_y), (cx + jaw_w, jaw_y)], fill=c, width=2)
    draw.line([(cx + jaw_w, jaw_y), (cx + face_w, face_mid - int(h * 0.03))], fill=c, width=2)

    # Cheekbone accent lines
    for side in [-1, 1]:
        cx2 = cx + side * int(w * 0.30)
        draw.line([(cx2, int(h * 0.35)), (cx2 + side * 5, int(h * 0.48))],
                  fill=_a(VIOLET, 140), width=2)

    # Forehead tech line
    imp_y = int(h * 0.14)
    imp_hw = int(w * 0.20)
    draw.line([(cx - imp_hw, imp_y), (cx + imp_hw, imp_y)], fill=_a(TEAL, 160), width=2)
    for dx in range(-imp_hw, imp_hw + 1, imp_hw // 3):
        draw.ellipse([cx + dx - 3, imp_y - 3, cx + dx + 3, imp_y + 3], fill=_a(CYAN_BRIGHT, 200))

    # Temple data streams
    for side in [-1, 1]:
        tx = cx + side * int(w * 0.37)
        for i in range(12):
            ty = int(h * 0.16) + i * 10
            alpha_v = max(30, 180 - i * 14)
            bw = random.randint(2, 6)
            draw.rectangle([tx - bw, ty, tx + bw, ty + 4], fill=_a(TEAL, alpha_v))

    # Neck lines
    neck_y = int(h * 0.82)
    neck_w = int(w * 0.12)
    draw.line([(cx - neck_w, neck_y), (cx - neck_w, int(h * 0.95))], fill=dim, width=1)
    draw.line([(cx + neck_w, neck_y), (cx + neck_w, int(h * 0.95))], fill=dim, width=1)
    # Cross bar
    draw.line([(cx - neck_w - 10, int(h * 0.88)), (cx + neck_w + 10, int(h * 0.88))],
              fill=dim, width=1)


def _draw_hud_data_readouts(draw, w, h):
    """Draw corner HUD data elements — timestamps, hex, bars."""
    dim = _a(CYAN_DIM, 100)
    bright = _a(TEAL, 140)

    # Top-left corner bracket
    draw.line([(10, 10), (10, 40)], fill=bright, width=1)
    draw.line([(10, 10), (40, 10)], fill=bright, width=1)

    # Top-right corner bracket
    draw.line([(w - 10, 10), (w - 10, 40)], fill=bright, width=1)
    draw.line([(w - 10, 10), (w - 40, 10)], fill=bright, width=1)

    # Bottom-left
    draw.line([(10, h - 10), (10, h - 40)], fill=bright, width=1)
    draw.line([(10, h - 10), (40, h - 10)], fill=bright, width=1)

    # Bottom-right
    draw.line([(w - 10, h - 10), (w - 10, h - 40)], fill=bright, width=1)
    draw.line([(w - 10, h - 10), (w - 40, h - 10)], fill=bright, width=1)

    # Side bars (signal strength style)
    for i in range(5):
        bh = 4 + i * 3
        by = h - 60 - bh
        draw.rectangle([20 + i * 8, by, 24 + i * 8, by + bh], fill=_a(TEAL, 80 + i * 30))

    # Right side bars
    for i in range(5):
        bh = 4 + i * 3
        by = h - 60 - bh
        draw.rectangle([w - 60 + i * 8, by, w - 56 + i * 8, by + bh], fill=_a(TEAL, 80 + i * 30))


# ---------------------------------------------------------------------------
# Face Layer
# ---------------------------------------------------------------------------

def generate_face_layers(reference, output_dir: Path, canvas_size=(512, 512)):
    output_dir = Path(output_dir)
    w, h = canvas_size
    random.seed(42)

    face_dir = output_dir / "face"
    face_dir.mkdir(parents=True, exist_ok=True)

    def _make_face(x_shift=0, y_shift=0, scale=1.0):
        img = _blank(canvas_size)
        draw = ImageDraw.Draw(img)
        # Shift all drawing by applying transform later
        _draw_hud_face_outline(draw, w, h)
        _draw_hud_data_readouts(draw, w, h)
        img = _glow(img, radius=4, intensity=1.3)

        if x_shift != 0 or y_shift != 0 or scale != 1.0:
            shifted = _blank(canvas_size)
            if scale != 1.0:
                sw = int(w * scale)
                sh = int(h * scale)
                img = img.resize((sw, sh), Image.LANCZOS)
                x_shift += (w - sw) // 2
                y_shift += (h - sh) // 2
            shifted.paste(img, (x_shift, y_shift), img)
            return shifted
        return img

    _make_face().save(face_dir / "face_center.png")
    _make_face(x_shift=-18, scale=0.96).save(face_dir / "face_left15.png")
    _make_face(x_shift=18, scale=0.96).save(face_dir / "face_right15.png")
    _make_face(y_shift=-10).save(face_dir / "face_up10.png")
    _make_face(y_shift=10).save(face_dir / "face_down10.png")

    # hair/ — abstract flowing lines above face
    hair_dir = output_dir / "hair"
    hair_dir.mkdir(parents=True, exist_ok=True)

    def _make_hair(x_shift=0):
        img = _blank(canvas_size)
        draw = ImageDraw.Draw(img)
        cx = w // 2 + x_shift
        # Flowing arc strands
        for i in range(8):
            spread = int(w * 0.35)
            start_x = cx - spread + i * (spread * 2 // 8)
            # Arc from top down sides
            points = []
            for t in range(20):
                tt = t / 19
                px = start_x + int(math.sin(tt * 2 + i * 0.7) * 15) + int(tt * (i - 4) * 12)
                py = int(h * 0.02) + int(tt * h * 0.40)
                points.append((px, py))
            if len(points) > 1:
                alpha_v = 120 - abs(i - 4) * 12
                draw.line(points, fill=_a(PURPLE, max(40, alpha_v)), width=2)

        # Highlight strands
        for i in [2, 5]:
            start_x = cx - int(w * 0.25) + i * int(w * 0.10)
            points = [(start_x + int(math.sin(t / 15 * 3 + i) * 10),
                       int(h * 0.04) + t * 2) for t in range(int(h * 0.18))]
            if len(points) > 1:
                draw.line(points, fill=_a(VIOLET, 100), width=1)

        return _glow(img, radius=3, intensity=1.2)

    _make_hair().save(hair_dir / "hair_center.png")
    _make_hair(x_shift=-10).save(hair_dir / "hair_left.png")
    _make_hair(x_shift=10).save(hair_dir / "hair_right.png")

    # nose/ — minimal vertical line + bridge
    nose_dir = output_dir / "nose"
    nose_dir.mkdir(parents=True, exist_ok=True)

    def _make_nose(x_shift=0):
        img = _blank(canvas_size)
        draw = ImageDraw.Draw(img)
        cx = w // 2 + x_shift
        ny1, ny2 = int(h * 0.44), int(h * 0.54)
        draw.line([(cx, ny1), (cx, ny2)], fill=_a(CYAN_DIM, 100), width=1)
        # Nostril dots
        draw.ellipse([cx - 8 - 2, int(h * 0.535), cx - 8 + 2, int(h * 0.545)],
                     fill=_a(CYAN_DIM, 80))
        draw.ellipse([cx + 8 - 2, int(h * 0.535), cx + 8 + 2, int(h * 0.545)],
                     fill=_a(CYAN_DIM, 80))
        return img

    _make_nose().save(nose_dir / "nose_center.png")
    _make_nose(x_shift=-3).save(nose_dir / "nose_left.png")
    _make_nose(x_shift=3).save(nose_dir / "nose_right.png")

    random.seed()


# ---------------------------------------------------------------------------
# Expression Layers — HUD style
# ---------------------------------------------------------------------------

def generate_expression_layers(output_dir: Path, canvas_size=(512, 512)):
    output_dir = Path(output_dir)
    w, h = canvas_size
    cx = w // 2

    # Eye positions
    eye_cy = int(h * 0.33)
    left_cx = int(w * 0.34)
    right_cx = int(w * 0.66)
    eye_rx = int(w * 0.09)
    eye_ry_open = int(h * 0.05)

    pupil_offsets = {
        "center": (0, 0),
        "left": (-int(eye_rx * 0.35), 0),
        "right": (int(eye_rx * 0.35), 0),
        "up": (0, -int(eye_ry_open * 0.35)),
        "down": (0, int(eye_ry_open * 0.35)),
    }

    # ---- eyes/ ----
    eyes_dir = output_dir / "eyes"
    eyes_dir.mkdir(parents=True, exist_ok=True)

    for direction, (px_off, py_off) in pupil_offsets.items():
        for state in ["open", "half", "closed"]:
            img = _blank(canvas_size)
            draw = ImageDraw.Draw(img)

            for ecx in [left_cx, right_cx]:
                if state == "closed":
                    # Glowing shut line
                    draw.line([(ecx - eye_rx, eye_cy), (ecx + eye_rx, eye_cy)],
                              fill=_a(CYAN_BRIGHT, 200), width=2)
                    # Lash ticks
                    for dx in range(-eye_rx + 6, eye_rx, 10):
                        draw.line([(ecx + dx, eye_cy), (ecx + dx, eye_cy + 4)],
                                  fill=_a(CYAN, 100), width=1)
                else:
                    ery = eye_ry_open if state == "open" else int(eye_ry_open * 0.55)
                    bbox = [ecx - eye_rx, eye_cy - ery, ecx + eye_rx, eye_cy + ery]

                    # Outer ring (targeting reticle style)
                    draw.ellipse(bbox, outline=_a(CYAN, 180), width=2)

                    # Cross-hair marks at cardinal points
                    for angle in [0, 90, 180, 270]:
                        rad = math.radians(angle)
                        x1 = ecx + int(math.cos(rad) * (eye_rx + 4))
                        y1 = eye_cy + int(math.sin(rad) * (ery + 4))
                        x2 = ecx + int(math.cos(rad) * (eye_rx + 10))
                        y2 = eye_cy + int(math.sin(rad) * (ery + 10))
                        draw.line([(x1, y1), (x2, y2)], fill=_a(CYAN_DIM, 120), width=1)

                    if state == "open":
                        # Inner iris ring
                        ir = int(min(eye_rx, ery) * 0.65)
                        draw.ellipse([ecx + px_off - ir, eye_cy + py_off - ir,
                                      ecx + px_off + ir, eye_cy + py_off + ir],
                                     outline=_a(CYAN_BRIGHT, 200), width=2)

                        # Inner iris detail ring
                        ir2 = int(ir * 0.6)
                        draw.ellipse([ecx + px_off - ir2, eye_cy + py_off - ir2,
                                      ecx + px_off + ir2, eye_cy + py_off + ir2],
                                     outline=_a(TEAL, 140), width=1)

                    # Pupil — bright center dot
                    pr = int(min(eye_rx, ery) * 0.25)
                    draw.ellipse([ecx + px_off - pr, eye_cy + py_off - pr,
                                  ecx + px_off + pr, eye_cy + py_off + pr],
                                 fill=_a(CYAN_BRIGHT, 255))

                    # Specular
                    hl = max(2, pr // 2)
                    draw.ellipse([ecx + px_off - hl - 2, eye_cy + py_off - pr,
                                  ecx + px_off + hl - 2, eye_cy + py_off - pr + hl * 2],
                                 fill=_a(WHITE, 220))

            img = _glow(img, radius=5, intensity=1.4)
            img.save(eyes_dir / f"eyes_{direction}_{state}.png")

    # ---- eyebrows/ ----
    brows_dir = output_dir / "eyebrows"
    brows_dir.mkdir(parents=True, exist_ok=True)

    brow_y = eye_cy - int(h * 0.08)
    brow_hw = int(w * 0.10)

    def _make_brows(fname, l_inner_dy, l_outer_dy, r_inner_dy, r_outer_dy):
        img = _blank(canvas_size)
        draw = ImageDraw.Draw(img)
        # Left brow
        draw.line([(left_cx - brow_hw, brow_y + l_outer_dy),
                    (left_cx + brow_hw, brow_y + l_inner_dy)],
                   fill=_a(TEAL, 200), width=3)
        # Right brow
        draw.line([(right_cx - brow_hw, brow_y + r_inner_dy),
                    (right_cx + brow_hw, brow_y + r_outer_dy)],
                   fill=_a(TEAL, 200), width=3)
        img = _glow(img, radius=4, intensity=1.3)
        img.save(brows_dir / fname)

    _make_brows("brows_neutral.png", 0, 0, 0, 0)
    lift = int(h * 0.03)
    _make_brows("brows_raised.png", -lift, -int(lift * 0.5), -int(lift * 0.5), -lift)
    furrow = int(h * 0.025)
    _make_brows("brows_furrowed.png", furrow, -int(furrow * 0.3), furrow, -int(furrow * 0.3))
    _make_brows("brows_asymmetric.png", -lift, -int(lift * 0.3), 0, int(furrow * 0.3))

    # ---- mouth/ ----
    mouth_dir = output_dir / "mouth"
    mouth_dir.mkdir(parents=True, exist_ok=True)

    mouth_cy = int(h * 0.63)
    mouth_hw = int(w * 0.11)

    # closed
    img = _blank(canvas_size)
    draw = ImageDraw.Draw(img)
    draw.line([(cx - mouth_hw, mouth_cy), (cx + mouth_hw, mouth_cy)],
              fill=_a(TEAL, 180), width=2)
    _glow(img, radius=3, intensity=1.2).save(mouth_dir / "mouth_closed.png")

    # slight, open, wide
    for fname, ry_frac in [("mouth_slight.png", 0.018), ("mouth_open.png", 0.04), ("mouth_wide.png", 0.065)]:
        img = _blank(canvas_size)
        draw = ImageDraw.Draw(img)
        mry = int(h * ry_frac)
        # Outer shape
        draw.ellipse([cx - mouth_hw, mouth_cy - mry, cx + mouth_hw, mouth_cy + mry],
                     outline=_a(TEAL, 180), width=2)
        # Dark interior
        draw.ellipse([cx - mouth_hw + 3, mouth_cy - mry + 3,
                      cx + mouth_hw - 3, mouth_cy + mry - 3],
                     fill=_a(DARK, 200))
        # Horizontal mid-line (teeth hint for wide)
        if ry_frac >= 0.06:
            draw.line([(cx - mouth_hw + 8, mouth_cy - int(mry * 0.2)),
                        (cx + mouth_hw - 8, mouth_cy - int(mry * 0.2))],
                       fill=_a(CYAN_DIM, 80), width=1)
        _glow(img, radius=3, intensity=1.2).save(mouth_dir / fname)

    # smile
    img = _blank(canvas_size)
    draw = ImageDraw.Draw(img)
    arc_ry = int(h * 0.03)
    draw.arc([cx - mouth_hw, mouth_cy - arc_ry, cx + mouth_hw, mouth_cy + arc_ry],
             start=5, end=175, fill=_a(TEAL, 200), width=2)
    _glow(img, radius=3, intensity=1.2).save(mouth_dir / "mouth_smile.png")

    # glitch
    img = _blank(canvas_size)
    draw = ImageDraw.Draw(img)
    rng = random.Random(77)
    points = [(cx - mouth_hw, mouth_cy)]
    step = mouth_hw * 2 // 10
    for i in range(10):
        x = cx - mouth_hw + (i + 1) * step
        y = mouth_cy + rng.randint(-int(h * 0.035), int(h * 0.035))
        points.append((x, y))
    for i in range(len(points) - 1):
        color = _a(RED, 220) if i % 2 == 0 else _a(CYAN_BRIGHT, 220)
        draw.line([points[i], points[i + 1]], fill=color, width=2)
    # Glitch blocks
    for _ in range(6):
        gx = rng.randint(cx - mouth_hw, cx + mouth_hw)
        gy = rng.randint(mouth_cy - int(h * 0.03), mouth_cy + int(h * 0.03))
        draw.rectangle([gx, gy, gx + rng.randint(8, 25), gy + 3], fill=_a(RED, 160))
    _glow(img, radius=2, intensity=1.1).save(mouth_dir / "mouth_glitch.png")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate HUD avatar layers")
    parser.add_argument("--output", type=Path, default=Path("assets/layers"))
    parser.add_argument("--canvas-size", type=int, default=512)
    parser.add_argument("--reference", type=Path, default=None)
    args = parser.parse_args()
    canvas = (args.canvas_size, args.canvas_size)

    print("Generating backgrounds...")
    generate_backgrounds(args.output / "background", canvas)
    print("Generating overlays...")
    generate_overlays(args.output / "overlay", canvas)
    print("Generating face layers...")
    generate_face_layers(None, args.output, canvas)
    print("Generating expression layers...")
    generate_expression_layers(args.output, canvas)
    print(f"Done. {sum(1 for _ in args.output.rglob('*.png'))} PNGs.")


if __name__ == "__main__":
    main()
