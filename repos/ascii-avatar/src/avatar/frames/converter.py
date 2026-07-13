"""Convert images to high-fidelity ASCII art.

Rendering modes (lowest → highest fidelity):
- "density": single-char luminance mapping (legacy, widest compat)
- "halfblock": Unicode half-block chars (▀▄█) — 2 vertical pixels/cell,
  monochrome with cyan tint.  ~2× vertical resolution vs density.
- "halfblock_rgb": Same geometry as halfblock but preserves full RGB color
  from the source image instead of tinting grayscale.
- "braille": Unicode Braille patterns (⠀–⣿) — each char encodes a 2×4
  dot matrix, giving 2× horizontal and 4× vertical sub-pixel resolution.
  Combined with 24-bit ANSI color per cell this is the highest-fidelity
  mode that works in any Unicode terminal.
"""

from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

# --------------------------------------------------------------------------- #
# Character ramps
# --------------------------------------------------------------------------- #

# Density ramp: darkest -> lightest
DENSITY_CHARS = "█▓▒░│║┃|!:·. "

# Half-block mode uses ▀ (upper half), ▄ (lower half), █ (full), and space.
# Color is applied via ANSI escapes — chars are just structural.

# Braille dot offsets — each bit maps to a position in the 2×4 cell:
#   col0  col1
#   0x01  0x08   row 0
#   0x02  0x10   row 1
#   0x04  0x20   row 2
#   0x40  0x80   row 3
_BRAILLE_BASE = 0x2800
_BRAILLE_MAP = [
    [0x01, 0x08],
    [0x02, 0x10],
    [0x04, 0x20],
    [0x40, 0x80],
]

# --------------------------------------------------------------------------- #
# ANSI helpers
# --------------------------------------------------------------------------- #

_RESET = "\033[0m"


def _fg256(r: int, g: int, b: int) -> str:
    """24-bit ANSI foreground."""
    return f"\033[38;2;{r};{g};{b}m"


def _bg256(r: int, g: int, b: int) -> str:
    """24-bit ANSI background."""
    return f"\033[48;2;{r};{g};{b}m"


def _tint_lum(lum: int, tint: tuple[int, int, int]) -> tuple[int, int, int]:
    """Apply a color tint to a grayscale luminance value."""
    r = int(lum * tint[0] / 255)
    g = int(lum * tint[1] / 255)
    b = int(lum * tint[2] / 255)
    return (r, g, b)


# --------------------------------------------------------------------------- #
# GITS color palette — maps luminance bands to cyberpunk colors
# --------------------------------------------------------------------------- #

# Ghost in the Shell palette: cyan-teal highlights, violet midtones,
# deep purple shadows.  Hot magenta for accent/glitch frames.
GITS_PALETTE = {
    "cyan":    (0, 200, 180),    # bright highlights
    "teal":    (0, 160, 140),    # upper midtones
    "violet":  (100, 0, 200),    # midtones
    "purple":  (40, 0, 100),     # shadows
    "magenta": (255, 0, 170),    # accent / glitch
    "red":     (200, 20, 20),    # error state
}


def _gits_color(lum: int, accent: str = "cyan") -> tuple[int, int, int]:
    """Map a luminance value to GITS palette color.

    Brightness bands:
      220+ → cyan-teal (highlights)
      160-220 → teal (upper mid)
      100-160 → blend teal→violet
      40-100 → violet→purple
      0-40 → deep purple (near-black)
    """
    if accent == "red":
        # Error state: red-orange palette
        if lum > 200:
            return (min(255, lum), int(lum * 0.15), int(lum * 0.05))
        elif lum > 120:
            t = (lum - 120) / 80
            return (int(180 * t + 80), int(20 * t), int(60 * (1 - t)))
        else:
            t = lum / 120
            return (int(80 * t + 20), 0, int(60 * t))
    elif accent == "magenta":
        # Glitch accent: magenta-shifted
        if lum > 180:
            return (min(255, int(lum * 0.9)), int(lum * 0.1), min(255, int(lum * 0.7)))
        elif lum > 100:
            t = (lum - 100) / 80
            return (int(200 * t + 40), 0, int(170 * t + 30))
        else:
            t = lum / 100
            return (int(40 * t + 10), 0, int(30 * t + 10))

    # Default: cyan-teal-violet-purple gradient
    if lum > 200:
        # Bright highlights: cyan-teal
        t = (lum - 200) / 55
        r = int(0 * (1 - t))
        g = int(200 * t + 160 * (1 - t))
        b = int(180 * t + 140 * (1 - t))
        return (r, g, b)
    elif lum > 140:
        # Upper midtones: teal blending to violet
        t = (lum - 140) / 60
        r = int(0 * t + 60 * (1 - t))
        g = int(160 * t + 20 * (1 - t))
        b = int(140 * t + 160 * (1 - t))
        return (r, g, b)
    elif lum > 70:
        # Midtones: violet
        t = (lum - 70) / 70
        r = int(60 * t + 30 * (1 - t))
        g = int(20 * t + 0 * (1 - t))
        b = int(160 * t + 80 * (1 - t))
        return (r, g, b)
    elif lum > 25:
        # Shadows: deep purple
        t = (lum - 25) / 45
        r = int(30 * t + 8 * (1 - t))
        g = 0
        b = int(80 * t + 20 * (1 - t))
        return (r, g, b)
    else:
        # Near-black
        t = lum / 25
        return (int(8 * t), 0, int(20 * t))


# --------------------------------------------------------------------------- #
# PIL image effects toolkit — all operate on PIL Images pre-conversion
# --------------------------------------------------------------------------- #

def _apply_scanline(img: Image.Image, offset: int = 0, intensity: float = 0.3) -> Image.Image:
    """Rolling CRT scanline overlay. Darkens every Nth row, offset shifts."""
    arr = np.array(img, dtype=np.float32)
    h = arr.shape[0]
    spacing = 4  # scanline every 4 pixel rows
    for y in range(h):
        if (y + offset) % spacing < 1:
            arr[y] *= (1.0 - intensity)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode=img.mode)


def _apply_chromatic_aberration(img: Image.Image, magnitude: int = 3) -> Image.Image:
    """RGB channel split — red left, blue right."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    r, g, b = img.split()
    r = ImageChops.offset(r, -magnitude, 0)
    b = ImageChops.offset(b, magnitude, 0)
    return Image.merge("RGB", (r, g, b))


def _apply_row_displacement(img: Image.Image, num_rows: int = 5, max_shift: int = 8) -> Image.Image:
    """Horizontal row displacement — data corruption glitch."""
    arr = np.array(img)
    h = arr.shape[0]
    rows = random.sample(range(h), min(num_rows, h))
    for row_idx in rows:
        shift = random.randint(-max_shift, max_shift)
        arr[row_idx] = np.roll(arr[row_idx], shift, axis=0)
    return Image.fromarray(arr, mode=img.mode)


def _apply_noise_bands(img: Image.Image, num_bands: int = 3, band_height: int = 2,
                       intensity: int = 40) -> Image.Image:
    """Inject bright/dark horizontal noise bands — single-frame static."""
    arr = np.array(img, dtype=np.int16)
    h = arr.shape[0]
    for _ in range(num_bands):
        y = random.randint(0, max(0, h - band_height))
        offset = random.choice([-intensity, intensity])
        arr[y:y + band_height] += offset
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode=img.mode)


def _apply_brightness_pulse(img: Image.Image, phase: float) -> Image.Image:
    """Sine-wave brightness modulation. phase in [0, 2*pi]."""
    factor = 1.0 + 0.15 * math.sin(phase)
    return ImageEnhance.Brightness(img).enhance(factor)


def _apply_flicker(img: Image.Image, intensity: float = 0.35) -> Image.Image:
    """Holographic flicker — sudden brightness drop."""
    return ImageEnhance.Brightness(img).enhance(1.0 - intensity)


def _apply_eye_blink(img: Image.Image, blink_phase: int, eye_y_range: tuple[float, float] = (0.25, 0.40)) -> Image.Image:
    """Simulate eye blink by darkening the eye region.

    blink_phase: 0=open, 1=half, 2=closed, 3=half (opening)
    """
    if blink_phase == 0:
        return img  # eyes open, no change
    arr = np.array(img, dtype=np.float32)
    h = arr.shape[0]
    y_start = int(h * eye_y_range[0])
    y_end = int(h * eye_y_range[1])
    # Darken the eye region progressively
    darken = {1: 0.6, 2: 0.15, 3: 0.6}[blink_phase]
    arr[y_start:y_end] *= darken
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode=img.mode)


# --------------------------------------------------------------------------- #
# Core converters
# --------------------------------------------------------------------------- #

def image_to_ascii(
    image: Image.Image,
    width: int = 40,
    height: int = 20,
    charset: str = "density",
    invert: bool = False,
) -> str:
    """Convert a PIL Image to ASCII art string.

    Args:
        image: Source PIL Image (any mode).
        width: Output width in characters.
        height: Output height in lines.
        charset: "density" | "halfblock" | "halfblock_rgb" | "braille".
        invert: If True, invert luminance.

    Returns:
        Multi-line string (may contain ANSI escapes).
    """
    if charset == "braille":
        return _braille_convert(image, width, height, invert)
    if charset == "braille_rgb":
        return _braille_rgb_convert(image, width, height, invert)
    if charset == "halfblock_rgb":
        return _halfblock_rgb_convert(image, width, height, invert)
    if charset == "halfblock":
        return _halfblock_convert(image, width, height, invert)
    return _density_convert(image, width, height, invert)


def _density_convert(
    image: Image.Image,
    width: int,
    height: int,
    invert: bool,
) -> str:
    """Legacy single-char density mapping."""
    chars = DENSITY_CHARS
    gray = image.convert("L")
    gray = gray.resize((width * 2, height), Image.Resampling.LANCZOS)

    pixels = gray.load()
    lines = []

    for y in range(height):
        line = ""
        for x in range(0, width * 2, 2):
            lum = (pixels[x, y] + pixels[x + 1, y]) / 2
            if invert:
                lum = 255 - lum
            idx = int(lum / 256 * len(chars))
            idx = min(idx, len(chars) - 1)
            line += chars[idx]
        lines.append(line)

    return "\n".join(lines)


def _halfblock_convert(
    image: Image.Image,
    width: int,
    height: int,
    invert: bool,
    tint: tuple[int, int, int] = (0, 212, 255),  # cyan
) -> str:
    """Half-block rendering: 2 vertical pixels per character cell.

    Each cell uses ▀ with fg=top_pixel, bg=bottom_pixel, giving twice
    the vertical resolution of single-char approaches.
    """
    # We need height*2 pixel rows to fill height character rows
    pixel_rows = height * 2
    gray = image.convert("L")

    # Sharpen before downscale to preserve edges
    gray = gray.filter(ImageFilter.SHARPEN)
    gray = ImageEnhance.Contrast(gray).enhance(1.8)

    gray = gray.resize((width, pixel_rows), Image.Resampling.LANCZOS)

    # Second sharpen pass at target resolution
    gray = gray.filter(ImageFilter.SHARPEN)

    pixels = gray.load()
    lines = []

    for row in range(height):
        line = ""
        y_top = row * 2
        y_bot = row * 2 + 1

        for x in range(width):
            lum_top = pixels[x, y_top]
            lum_bot = pixels[x, y_bot]

            if invert:
                lum_top = 255 - lum_top
                lum_bot = 255 - lum_bot

            rt, gt, bt = _tint_lum(lum_top, tint)
            rb, gb, bb = _tint_lum(lum_bot, tint)

            # Both very dark -> space with dark bg
            if lum_top < 8 and lum_bot < 8:
                line += _bg256(0, 0, 0) + " "
            # Both nearly same -> full block with averaged color
            elif abs(lum_top - lum_bot) < 12:
                avg_r = (rt + rb) // 2
                avg_g = (gt + gb) // 2
                avg_b = (bt + bb) // 2
                line += _fg256(avg_r, avg_g, avg_b) + "█"
            else:
                # ▀ = upper half filled: fg = top, bg = bottom
                line += _fg256(rt, gt, bt) + _bg256(rb, gb, bb) + "▀"

        line += _RESET
        lines.append(line)

    return "\n".join(lines)


def _halfblock_rgb_convert(
    image: Image.Image,
    width: int,
    height: int,
    invert: bool,
) -> str:
    """Half-block rendering with full RGB color preservation.

    Same geometry as _halfblock_convert (2 vertical pixels per cell) but
    keeps the original image colors instead of converting to grayscale +
    tint.  Produces dramatically richer output for color source images.
    """
    pixel_rows = height * 2
    rgb = image.convert("RGB")

    # Sharpen before downscale
    rgb = rgb.filter(ImageFilter.SHARPEN)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.4)
    rgb = rgb.resize((width, pixel_rows), Image.Resampling.LANCZOS)
    rgb = rgb.filter(ImageFilter.SHARPEN)

    pixels = rgb.load()
    lines: list[str] = []

    for row in range(height):
        parts: list[str] = []
        y_top = row * 2
        y_bot = row * 2 + 1

        for x in range(width):
            rt, gt, bt = pixels[x, y_top]
            rb, gb, bb = pixels[x, y_bot]

            if invert:
                rt, gt, bt = 255 - rt, 255 - gt, 255 - bt
                rb, gb, bb = 255 - rb, 255 - gb, 255 - bb

            lum_top = (rt + gt + bt) // 3
            lum_bot = (rb + gb + bb) // 3

            if lum_top < 8 and lum_bot < 8:
                parts.append(_bg256(0, 0, 0) + " ")
            elif abs(lum_top - lum_bot) < 12:
                ar = (rt + rb) // 2
                ag = (gt + gb) // 2
                ab = (bt + bb) // 2
                parts.append(_fg256(ar, ag, ab) + "█")
            else:
                parts.append(_fg256(rt, gt, bt) + _bg256(rb, gb, bb) + "▀")

        parts.append(_RESET)
        lines.append("".join(parts))

    return "\n".join(lines)


def _braille_convert(
    image: Image.Image,
    width: int,
    height: int,
    invert: bool,
    tint: tuple[int, int, int] = (0, 212, 255),  # cyan (legacy, ignored when gits=True)
    threshold: int | None = None,
    gits: bool = True,
    color_accent: str = "cyan",
) -> str:
    """Braille-dot rendering: 2x4 sub-pixels per character cell.

    Each Unicode Braille character (U+2800-U+28FF) encodes an 8-dot
    pattern in a 2-column x 4-row grid.  This gives:
      - Horizontal resolution: width x 2 dots
      - Vertical resolution:   height x 4 dots

    When *gits* is True (default), uses the Ghost in the Shell color
    palette instead of flat cyan tint.  *color_accent* selects palette
    variant: "cyan" (default), "magenta" (glitch), "red" (error).

    If *threshold* is None, an adaptive Otsu-like threshold is computed
    per-frame for optimal contrast.
    """
    # Sub-pixel grid dimensions
    dot_w = width * 2
    dot_h = height * 4

    gray = image.convert("L")
    gray = gray.filter(ImageFilter.SHARPEN)
    gray = ImageEnhance.Contrast(gray).enhance(2.0)
    gray = gray.resize((dot_w, dot_h), Image.Resampling.LANCZOS)
    gray = gray.filter(ImageFilter.DETAIL)

    pixels = gray.load()

    # Auto-threshold: use mean luminance with a slight bias toward detail
    if threshold is None:
        total = sum(pixels[x, y] for y in range(dot_h) for x in range(dot_w))
        threshold = int(total / (dot_w * dot_h) * 0.85)

    lines: list[str] = []

    for row in range(height):
        parts: list[str] = []
        base_y = row * 4

        for col in range(width):
            base_x = col * 2
            code = 0
            on_lum_sum = 0
            on_count = 0

            for dy in range(4):
                for dx in range(2):
                    px = base_x + dx
                    py = base_y + dy
                    if px < dot_w and py < dot_h:
                        lum = pixels[px, py]
                        if invert:
                            lum = 255 - lum
                        if lum > threshold:
                            code |= _BRAILLE_MAP[dy][dx]
                            on_lum_sum += lum
                            on_count += 1

            if on_count > 0 and code != 0:
                avg_lum = on_lum_sum // on_count
                if gits:
                    r, g, b = _gits_color(avg_lum, accent=color_accent)
                else:
                    r, g, b = _tint_lum(avg_lum, tint)
                parts.append(_fg256(r, g, b) + chr(_BRAILLE_BASE + code))
            else:
                parts.append(" ")

        parts.append(_RESET)
        lines.append("".join(parts))

    return "\n".join(lines)


def _braille_rgb_convert(
    image: Image.Image,
    width: int,
    height: int,
    invert: bool,
    threshold: int | None = None,
) -> str:
    """Braille-dot rendering with full RGB color per cell.

    Same dot-matrix geometry as _braille_convert but preserves the
    source image's colors.  The foreground color of each cell is the
    average RGB of the "on" dots.
    """
    dot_w = width * 2
    dot_h = height * 4

    rgb = image.convert("RGB")
    rgb = rgb.filter(ImageFilter.SHARPEN)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.6)
    rgb = rgb.resize((dot_w, dot_h), Image.Resampling.LANCZOS)
    rgb = rgb.filter(ImageFilter.DETAIL)

    gray = image.convert("L")
    gray = gray.filter(ImageFilter.SHARPEN)
    gray = ImageEnhance.Contrast(gray).enhance(2.0)
    gray = gray.resize((dot_w, dot_h), Image.Resampling.LANCZOS)

    px_rgb = rgb.load()
    px_gray = gray.load()

    if threshold is None:
        total = sum(px_gray[x, y] for y in range(dot_h) for x in range(dot_w))
        threshold = int(total / (dot_w * dot_h) * 0.85)

    lines: list[str] = []

    for row in range(height):
        parts: list[str] = []
        base_y = row * 4

        for col in range(width):
            base_x = col * 2
            code = 0
            r_sum = g_sum = b_sum = 0
            on_count = 0

            for dy in range(4):
                for dx in range(2):
                    px = base_x + dx
                    py = base_y + dy
                    if px < dot_w and py < dot_h:
                        lum = px_gray[px, py]
                        if invert:
                            lum = 255 - lum
                        if lum > threshold:
                            code |= _BRAILLE_MAP[dy][dx]
                            r, g, b = px_rgb[px, py]
                            if invert:
                                r, g, b = 255 - r, 255 - g, 255 - b
                            r_sum += r
                            g_sum += g
                            b_sum += b
                            on_count += 1

            if on_count > 0 and code != 0:
                parts.append(
                    _fg256(r_sum // on_count, g_sum // on_count, b_sum // on_count)
                    + chr(_BRAILLE_BASE + code)
                )
            else:
                parts.append(" ")

        parts.append(_RESET)
        lines.append("".join(parts))

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# File loading
# --------------------------------------------------------------------------- #

def load_and_convert(
    path: str,
    width: int = 40,
    height: int = 20,
    charset: str = "density",
    invert: bool = False,
    contrast: float = 1.5,
    brightness: float = 1.0,
) -> str:
    """Load an image file and convert to ASCII art."""
    img = Image.open(path)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    return image_to_ascii(img, width, height, charset, invert)


# --------------------------------------------------------------------------- #
# State frame generation
# --------------------------------------------------------------------------- #

def _braille_from_pil(
    image: Image.Image,
    width: int,
    height: int,
    charset: str,
    color_accent: str = "cyan",
) -> str:
    """Convert a PIL image to braille/ascii with GITS palette.

    Wraps image_to_ascii but injects GITS coloring for braille modes.
    """
    if charset == "braille":
        return _braille_convert(image, width, height, invert=True,
                                gits=True, color_accent=color_accent)
    # For non-braille charsets, use the existing path
    return image_to_ascii(image, width, height, charset, invert=True)


def generate_state_frames(
    base_image: Image.Image,
    width: int = 40,
    height: int = 20,
    charset: str = "density",
) -> dict[str, list[str]]:
    """Generate all avatar state frames from a single base portrait.

    Ghost in the Shell aesthetic: layered effects composited at the PIL
    level before braille conversion.  Each state has 8-24 frames with
    multiple concurrent animation layers.

    Animation layers per state:
    - idle: sine brightness pulse + rolling scanlines + periodic blink
    - thinking: fast scanline sweep + brightness flicker + chromatic aberration
    - speaking: mouth distortion + brightness pulse + subtle scanlines
    - listening: bright/contrast boost + fast pulse + wide scanlines
    - error: heavy glitch (row displacement + chromatic aberration + noise
      bands + red palette)

    Additionally generates special overlay frames:
    - blink: eye-region darkening frames (for idle micro-events)
    - glitch: chromatic aberration + displacement bursts (for idle micro-events)
    - flicker: sudden brightness drops (for idle micro-events)
    """
    gray = base_image.convert("L")
    gray = ImageEnhance.Contrast(gray).enhance(1.6)
    # Keep an RGB copy for chromatic aberration effects
    rgb = base_image.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(1.4)

    frames: dict[str, list[str]] = {}
    num_idle = 24  # smooth sine cycle

    # === IDLE: breathing brightness pulse + rolling scanlines ===
    # 24 frames = ~19s cycle at 0.8s/frame, smooth sine wave
    idle_frames = []
    for i in range(num_idle):
        phase = (2 * math.pi * i) / num_idle
        modified = _apply_brightness_pulse(gray, phase)
        # Rolling scanline — offset advances each frame
        modified = _apply_scanline(modified, offset=i * 2, intensity=0.2)
        idle_frames.append(_braille_from_pil(modified, width, height, charset))
    frames["idle"] = idle_frames

    # === THINKING: fast scanline sweep + brightness flicker ===
    # 12 frames at 0.15s = 1.8s cycle
    think_frames = []
    for i in range(12):
        # Aggressive scanline sweep from top to bottom
        modified = _apply_scanline(gray, offset=i * 4, intensity=0.4)
        # Slight brightness oscillation — thinking "pulses"
        phase = (2 * math.pi * i) / 12
        bright = 1.0 + 0.08 * math.sin(phase * 3)  # faster oscillation
        modified = ImageEnhance.Brightness(modified).enhance(bright)
        # Every 4th frame: subtle chromatic aberration
        if i % 4 == 2:
            modified_rgb = _apply_chromatic_aberration(rgb, magnitude=2)
            modified_gray = modified_rgb.convert("L")
            modified_gray = ImageEnhance.Contrast(modified_gray).enhance(1.6)
            modified = _apply_scanline(modified_gray, offset=i * 4, intensity=0.35)
        think_frames.append(_braille_from_pil(modified, width, height, charset))
    frames["thinking"] = think_frames

    # === SPEAKING: mouth distortion + pulse + scanlines ===
    # 8 frames at 0.1s = 0.8s cycle
    speak_frames = []
    mouth_offsets = [0, 2, 5, 8, 5, 2, 0, 1]
    for i, offset in enumerate(mouth_offsets):
        modified = gray.copy()
        if offset > 0:
            pixels = modified.load()
            mouth_start = int(modified.size[1] * 0.58)
            mouth_end = int(modified.size[1] * 0.78)
            for y in range(mouth_start, min(mouth_end, modified.size[1])):
                for x in range(modified.size[0]):
                    src_y = y - offset
                    if 0 <= src_y < modified.size[1]:
                        pixels[x, y] = pixels[x, src_y]
                    else:
                        pixels[x, y] = 30
        # Subtle brightness variation while speaking
        phase = (2 * math.pi * i) / len(mouth_offsets)
        modified = ImageEnhance.Brightness(modified).enhance(1.0 + 0.05 * math.sin(phase))
        # Light scanlines always present
        modified = _apply_scanline(modified, offset=i * 3, intensity=0.15)
        speak_frames.append(_braille_from_pil(modified, width, height, charset))
    frames["speaking"] = speak_frames

    # === LISTENING: bright + contrast + wide scanlines ===
    # 8 frames at 0.4s = 3.2s cycle
    listen_frames = []
    for i in range(8):
        phase = (2 * math.pi * i) / 8
        bright = 1.15 + 0.1 * math.sin(phase)
        modified = ImageEnhance.Brightness(gray).enhance(bright)
        modified = ImageEnhance.Contrast(modified).enhance(1.3)
        # Wide, slow scanlines — alert but steady
        modified = _apply_scanline(modified, offset=i * 3, intensity=0.25)
        listen_frames.append(_braille_from_pil(modified, width, height, charset))
    frames["listening"] = listen_frames

    # === ERROR: heavy glitch with red palette ===
    # 8 frames at 0.2s = 1.6s cycle — chaotic
    error_frames = []
    for i in range(8):
        # Start from RGB for chromatic aberration
        modified_rgb = _apply_chromatic_aberration(rgb, magnitude=4 + random.randint(0, 6))
        # Row displacement — corrupt data
        modified_rgb = _apply_row_displacement(modified_rgb,
                                                num_rows=3 + random.randint(0, 5),
                                                max_shift=6 + random.randint(0, 8))
        # Noise bands — static bursts
        modified_rgb = _apply_noise_bands(modified_rgb, num_bands=2 + random.randint(0, 3),
                                          intensity=50 + random.randint(0, 30))
        modified = modified_rgb.convert("L")
        modified = ImageEnhance.Contrast(modified).enhance(1.8)
        # Heavy scanlines
        modified = _apply_scanline(modified, offset=i * 5 + random.randint(0, 3), intensity=0.45)
        # Random brightness jitter
        modified = ImageEnhance.Brightness(modified).enhance(0.7 + random.random() * 0.5)
        error_frames.append(_braille_from_pil(modified, width, height, charset,
                                              color_accent="red"))
    frames["error"] = error_frames

    # === SPECIAL OVERLAYS: blink frames (for micro-event injection) ===
    # These are idle-like frames with eye region darkened
    blink_frames = []
    base_idle = _apply_scanline(gray, offset=0, intensity=0.2)
    for blink_phase in [1, 2, 2, 3]:  # half→closed→closed→half
        modified = _apply_eye_blink(base_idle, blink_phase)
        blink_frames.append(_braille_from_pil(modified, width, height, charset))
    frames["blink"] = blink_frames

    # === SPECIAL OVERLAYS: glitch burst frames (for micro-event injection) ===
    glitch_frames = []
    for i in range(6):
        modified_rgb = _apply_chromatic_aberration(rgb, magnitude=3 + i)
        modified_rgb = _apply_row_displacement(modified_rgb,
                                                num_rows=2 + i,
                                                max_shift=4 + i * 2)
        if i >= 3:
            modified_rgb = _apply_noise_bands(modified_rgb, num_bands=2, intensity=40)
        modified = modified_rgb.convert("L")
        modified = ImageEnhance.Contrast(modified).enhance(1.6)
        modified = _apply_scanline(modified, offset=random.randint(0, 20), intensity=0.3)
        glitch_frames.append(_braille_from_pil(modified, width, height, charset,
                                               color_accent="magenta"))
    frames["glitch"] = glitch_frames

    # === SPECIAL OVERLAYS: flicker frames (brightness drop) ===
    flicker_frames = []
    for intensity in [0.25, 0.35, 0.25]:
        modified = _apply_flicker(gray, intensity)
        modified = _apply_scanline(modified, offset=random.randint(0, 10), intensity=0.35)
        flicker_frames.append(_braille_from_pil(modified, width, height, charset))
    frames["flicker"] = flicker_frames

    return frames
