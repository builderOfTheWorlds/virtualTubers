"""Pure-Python sixel encoder for pixel-perfect terminal graphics.

Sixel is a bitmap graphics format that many modern terminals support
(VTE/GNOME Terminal ≥0.72, foot, WezTerm, mlterm, xterm -ti vt340).

Each sixel "row" encodes **6 vertical pixels** per character cell column,
giving full pixel-level resolution limited only by terminal pixel
dimensions — far beyond any Unicode character approach.

When running inside tmux, output is wrapped in DCS passthrough sequences
so the graphics reach the outer terminal.
"""

from __future__ import annotations

import io
import os
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as PILImage

# --------------------------------------------------------------------------- #
# Terminal / tmux detection
# --------------------------------------------------------------------------- #

def _in_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def _wrap_tmux_passthrough(payload: str) -> str:
    """Wrap a DCS sequence in tmux passthrough.

    tmux ≥3.4 supports ``allow-passthrough on`` which forwards DCS
    sequences to the outer terminal.
    """
    # \033Ptmux;\033<payload>\033\\
    # Every \033 inside payload must be doubled
    escaped = payload.replace("\033", "\033\033")
    return f"\033Ptmux;{escaped}\033\\"


# --------------------------------------------------------------------------- #
# Color quantization
# --------------------------------------------------------------------------- #

def _quantize(image: "PILImage.Image", max_colors: int = 256) -> "PILImage.Image":
    """Quantize an RGB image to a palette of at most *max_colors*."""
    return image.quantize(colors=max_colors, method=2, dither=1)


# --------------------------------------------------------------------------- #
# Sixel encoder
# --------------------------------------------------------------------------- #

def encode_sixel(
    image: "PILImage.Image",
    max_colors: int = 256,
    transparency: bool = False,
) -> str:
    """Encode a PIL Image as a sixel escape sequence string.

    Args:
        image: PIL Image (any mode — will be converted to RGB).
        max_colors: Maximum palette size (2–256).
        transparency: If True, palette index 0 is treated as transparent.

    Returns:
        Complete sixel escape sequence ready to print().
    """
    rgb = image.convert("RGB")
    width, height = rgb.size

    # Quantize to paletted image
    quant = _quantize(rgb, max_colors)
    palette_data = quant.getpalette()  # flat [R,G,B, R,G,B, ...]
    if palette_data is None:
        raise ValueError("Quantization produced no palette")

    n_colors = min(max_colors, len(palette_data) // 3)
    pixels = quant.load()

    buf = io.StringIO()

    # DCS introducer: P q
    # Parameters: "Pan;Pad;Ph;Pv" — aspect 1:1, use actual pixel dimensions
    buf.write(f"\033Pq\"1;1;{width};{height}")

    # Define palette: #index;2;R%;G%;B%  (values 0–100)
    for i in range(n_colors):
        r = int(palette_data[i * 3 + 0] / 255 * 100)
        g = int(palette_data[i * 3 + 1] / 255 * 100)
        b = int(palette_data[i * 3 + 2] / 255 * 100)
        buf.write(f"#{i};2;{r};{g};{b}")

    # Encode pixel data in 6-row bands
    for band_y in range(0, height, 6):
        # For each color used in this band, emit a sixel row
        band_height = min(6, height - band_y)

        # Build a map: color_index -> list of sixel chars across width
        # A sixel char = 0x3F + 6-bit bitmap (bit 0 = top row, bit 5 = bottom)
        color_columns: dict[int, list[int]] = {}

        for x in range(width):
            for dy in range(band_height):
                y = band_y + dy
                ci = pixels[x, y]
                if ci not in color_columns:
                    color_columns[ci] = [0] * width
                color_columns[ci][x] |= (1 << dy)

        first_color = True
        for ci, columns in color_columns.items():
            if transparency and ci == 0:
                continue

            buf.write(f"#{ci}")

            # RLE encode the sixel data for this color
            x = 0
            while x < width:
                val = columns[x]
                sixel_char = chr(0x3F + val)

                # Count run length
                run = 1
                while x + run < width and columns[x + run] == val:
                    run += 1

                if run >= 4:
                    buf.write(f"!{run}{sixel_char}")
                else:
                    buf.write(sixel_char * run)

                x += run

            # $ = carriage return (back to column 0, same band)
            buf.write("$")

        # - = newline (advance to next 6-pixel band)
        buf.write("-")

    # String terminator
    buf.write("\033\\")

    payload = buf.getvalue()

    if _in_tmux():
        payload = _wrap_tmux_passthrough(payload)

    return payload


# --------------------------------------------------------------------------- #
# High-level frame generation
# --------------------------------------------------------------------------- #

def image_to_sixel(
    image: "PILImage.Image",
    pixel_width: int | None = None,
    pixel_height: int | None = None,
    max_colors: int = 256,
) -> str:
    """Convert a PIL Image to a sixel string, optionally resizing to fit.

    Args:
        image: Source PIL Image.
        pixel_width: Target width in terminal pixels. ``None`` = use original.
        pixel_height: Target height in terminal pixels. ``None`` = use original.
        max_colors: Palette size (2–256).

    Returns:
        Sixel escape sequence string.
    """
    from PIL import Image as PILImageModule, ImageEnhance, ImageFilter

    img = image.convert("RGB")

    if pixel_width or pixel_height:
        orig_w, orig_h = img.size
        pw = pixel_width or int(orig_w * (pixel_height / orig_h))
        ph = pixel_height or int(orig_h * (pixel_width / orig_w))
        img = img.resize((pw, ph), PILImageModule.Resampling.LANCZOS)

    # Light sharpen for clarity at terminal scale
    img = img.filter(ImageFilter.SHARPEN)
    img = ImageEnhance.Contrast(img).enhance(1.2)

    return encode_sixel(img, max_colors=max_colors)


def generate_sixel_state_frames(
    base_image: "PILImage.Image",
    pixel_width: int,
    pixel_height: int,
    max_colors: int = 128,
) -> dict[str, list[str]]:
    """Generate state-animated sixel frames from a portrait image.

    Same state effects as the ASCII converter (idle pulse, thinking
    scanline, speaking mouth distortion, etc.) but rendered at full
    pixel resolution via sixel.

    Returns:
        Dict mapping state name -> list of sixel escape strings.
    """
    from PIL import Image as PILImageModule, ImageDraw, ImageEnhance, ImageFilter

    rgb = base_image.convert("RGB")
    rgb = rgb.resize((pixel_width, pixel_height), PILImageModule.Resampling.LANCZOS)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.3)

    frames: dict[str, list[str]] = {}

    # === IDLE: brightness pulse ===
    idle_frames = []
    for brightness in [1.0, 1.03, 1.06]:
        modified = ImageEnhance.Brightness(rgb).enhance(brightness)
        idle_frames.append(encode_sixel(modified, max_colors=max_colors))
    frames["idle"] = idle_frames

    # === THINKING: scanline sweep ===
    think_frames = []
    band_h = max(4, pixel_height // 12)
    for i in range(6):
        modified = rgb.copy()
        draw = ImageDraw.Draw(modified)
        scan_y = int(i / 6 * pixel_height)
        draw.rectangle(
            [0, scan_y, pixel_width, min(scan_y + band_h, pixel_height)],
            fill=(0, 200, 255),  # cyan scanline
        )
        think_frames.append(encode_sixel(modified, max_colors=max_colors))
    frames["thinking"] = think_frames

    # === SPEAKING: mouth area distortion ===
    speak_frames = []
    for offset in [0, 4, 8, 4]:
        modified = rgb.copy()
        px = modified.load()
        mouth_start = int(pixel_height * 0.6)
        mouth_end = int(pixel_height * 0.75)
        for y in range(mouth_start, min(mouth_end, pixel_height)):
            for x in range(pixel_width):
                src_y = y - offset
                if 0 <= src_y < pixel_height:
                    px[x, y] = px[x, src_y]
                else:
                    px[x, y] = (20, 20, 20)
        speak_frames.append(encode_sixel(modified, max_colors=max_colors))
    frames["speaking"] = speak_frames

    # === LISTENING: brightened ===
    listen_frames = []
    for brightness in [1.1, 1.15, 1.1]:
        modified = ImageEnhance.Brightness(rgb).enhance(brightness)
        modified = ImageEnhance.Contrast(modified).enhance(1.1)
        listen_frames.append(encode_sixel(modified, max_colors=max_colors))
    frames["listening"] = listen_frames

    # === ERROR: red tint + glitch ===
    error_frames = []
    for intensity in [0.15, 0.3]:
        modified = rgb.copy()
        # Red overlay
        red_layer = PILImageModule.new("RGB", modified.size, (255, 0, 0))
        modified = PILImageModule.blend(modified, red_layer, intensity)
        # Horizontal glitch: shift random bands
        px = modified.load()
        shift = int(intensity * 20)
        for y in range(0, pixel_height, 7):
            for x in range(pixel_width - shift):
                px[x, y] = px[min(x + shift, pixel_width - 1), y]
        error_frames.append(encode_sixel(modified, max_colors=max_colors))
    frames["error"] = error_frames

    return frames


# --------------------------------------------------------------------------- #
# Capability detection
# --------------------------------------------------------------------------- #

def terminal_supports_sixel() -> bool:
    """Best-effort check for sixel support.

    Checks:
    1. VTE-based terminals (GNOME Terminal ≥44 / VTE ≥0.72)
    2. Known sixel-capable $TERM_PROGRAM values
    3. $TERM containing "sixel"
    """
    term = os.environ.get("TERM", "")
    term_program = os.environ.get("TERM_PROGRAM", "")

    # Direct sixel terminals
    if "sixel" in term.lower():
        return True

    # Known sixel-capable programs
    sixel_programs = {"foot", "mlterm", "wezterm", "contour", "ctx"}
    if term_program.lower() in sixel_programs:
        return True

    # VTE detection — check tmux client termfeatures or VTE_VERSION
    vte_version = os.environ.get("VTE_VERSION", "")
    if vte_version:
        try:
            if int(vte_version) >= 7200:  # VTE 0.72+
                return True
        except ValueError:
            pass

    # Inside tmux, check if outer terminal is VTE
    if _in_tmux():
        # tmux reports VTE in client termtype
        # The features string from tmux list-clients contains "VTE"
        # We can't easily query this from Python, so check env
        # The avatar-start.sh runs inside tmux so VTE_VERSION may be inherited
        import subprocess
        try:
            result = subprocess.run(
                ["tmux", "list-clients", "-F", "#{client_termtype}"],
                capture_output=True, text=True, timeout=2,
            )
            if "VTE" in result.stdout.upper():
                return True
        except Exception:
            pass

    return False


def get_terminal_cell_size() -> tuple[int, int] | None:
    """Query terminal cell size in pixels via TIOCGWINSZ ioctl.

    Returns:
        (cell_width_px, cell_height_px) or None if unavailable.
    """
    import fcntl
    import termios

    try:
        # TIOCGWINSZ returns: rows, cols, xpixel, ypixel
        data = fcntl.ioctl(1, termios.TIOCGWINSZ, b"\x00" * 8)
        rows, cols, xpixel, ypixel = struct.unpack("HHHH", data)
        if xpixel > 0 and ypixel > 0 and cols > 0 and rows > 0:
            return (xpixel // cols, ypixel // rows)
    except Exception:
        pass
    return None


def get_terminal_pixel_size() -> tuple[int, int] | None:
    """Query total terminal pixel dimensions via TIOCGWINSZ ioctl.

    Returns:
        (total_width_px, total_height_px) or None if unavailable.
    """
    import fcntl
    import termios

    try:
        data = fcntl.ioctl(1, termios.TIOCGWINSZ, b"\x00" * 8)
        rows, cols, xpixel, ypixel = struct.unpack("HHHH", data)
        if xpixel > 0 and ypixel > 0:
            return (xpixel, ypixel)
    except Exception:
        pass
    return None
