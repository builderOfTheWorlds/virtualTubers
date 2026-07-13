"""Generate a default AI portrait programmatically.

Creates a stylized face silhouette using PIL drawing —
no external image file needed. This is the fallback
when no custom portrait is provided.
"""

from __future__ import annotations

from PIL import Image, ImageDraw


def generate_default_portrait(size: int = 256) -> Image.Image:
    """Generate a stylized AI face silhouette.

    Returns a grayscale PIL Image of a minimal, geometric face.
    """
    img = Image.new("L", (size, size), 20)  # dark background
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    s = size  # scale reference

    # Head outline — oval
    head_bbox = [
        int(cx - s * 0.32), int(cy - s * 0.42),
        int(cx + s * 0.32), int(cy + s * 0.28),
    ]
    draw.ellipse(head_bbox, fill=180, outline=200, width=2)

    # Neck
    neck_w = int(s * 0.12)
    draw.rectangle(
        [cx - neck_w, int(cy + s * 0.25), cx + neck_w, int(cy + s * 0.4)],
        fill=160,
    )

    # Shoulders
    draw.arc(
        [int(cx - s * 0.45), int(cy + s * 0.3), cx, int(cy + s * 0.5)],
        180, 0, fill=150, width=3,
    )
    draw.arc(
        [cx, int(cy + s * 0.3), int(cx + s * 0.45), int(cy + s * 0.5)],
        180, 0, fill=150, width=3,
    )

    # Eyes — geometric, slightly glowing
    eye_y = int(cy - s * 0.12)
    eye_w = int(s * 0.07)
    eye_h = int(s * 0.03)
    for eye_x in [int(cx - s * 0.13), int(cx + s * 0.13)]:
        # Eye socket (dark)
        draw.ellipse(
            [eye_x - eye_w - 3, eye_y - eye_h - 3,
             eye_x + eye_w + 3, eye_y + eye_h + 3],
            fill=80,
        )
        # Eye (bright)
        draw.ellipse(
            [eye_x - eye_w, eye_y - eye_h,
             eye_x + eye_w, eye_y + eye_h],
            fill=240,
        )
        # Pupil
        draw.ellipse(
            [eye_x - 3, eye_y - 3, eye_x + 3, eye_y + 3],
            fill=40,
        )

    # Nose — subtle line
    draw.line(
        [cx, int(cy - s * 0.04), cx, int(cy + s * 0.06)],
        fill=140, width=1,
    )

    # Mouth — horizontal line
    mouth_y = int(cy + s * 0.12)
    mouth_w = int(s * 0.1)
    draw.line(
        [cx - mouth_w, mouth_y, cx + mouth_w, mouth_y],
        fill=120, width=2,
    )

    # Circuit lines on forehead (AI aesthetic)
    for offset in [-1, 0, 1]:
        y = int(cy - s * 0.3) + offset * int(s * 0.03)
        draw.line(
            [int(cx - s * 0.15), y, int(cx + s * 0.15), y],
            fill=100, width=1,
        )

    # Side accent lines
    for side in [-1, 1]:
        x = int(cx + side * s * 0.28)
        draw.line(
            [x, int(cy - s * 0.2), x, int(cy + s * 0.15)],
            fill=100, width=1,
        )

    return img
