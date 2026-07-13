"""ASCII art frame set loader.

Supports multiple modes:
- "cyberpunk": hand-crafted ASCII art frames (legacy)
- "layered2d": procedurally generated 2.5D layered avatar frames
- "portrait": image-to-ASCII converted frames from a portrait image
- "portrait:<path>": custom image as avatar source

Charset hierarchy (lowest → highest fidelity):
  density → halfblock → halfblock_rgb → braille → braille_rgb → sixel

When charset="sixel" (or "auto" resolves to sixel), frames are
pixel-perfect images encoded via the sixel graphics protocol —
full source resolution, no character approximation at all.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

FRAME_RATES = {
    "idle": 0.8,
    "thinking": 0.15,
    "speaking": 0.1,
    "listening": 0.4,
    "error": 0.2,
}

# When "auto", sixel is used if the terminal supports it, else braille.
DEFAULT_CHARSET = "auto"


def _detect_terminal_size() -> tuple[int, int]:
    """Return (columns, rows) for the avatar pane.

    Falls back to sensible defaults if detection fails.
    Reserve 1 row for the status bar.
    """
    try:
        cols, rows = shutil.get_terminal_size(fallback=(60, 35))
    except Exception:
        cols, rows = 60, 35
    # Reserve 2 rows for status bar + margin
    return cols, max(rows - 2, 10)


def _resolve_charset(charset: str) -> str:
    """Resolve 'auto' to the best charset for the current terminal."""
    if charset != "auto":
        return charset
    try:
        from avatar.frames.sixel import terminal_supports_sixel
        if terminal_supports_sixel():
            log.info("Auto-detected sixel support — using pixel graphics")
            return "sixel"
    except Exception:
        pass
    log.info("Sixel not available — falling back to braille")
    return "braille"


def load_frame_set(
    name: str,
    width: int | None = None,
    height: int | None = None,
    charset: str | None = None,
) -> tuple[dict[str, list[str]], dict[str, float]]:
    """Load a frame set by name.

    Args:
        name: "cyberpunk", "layered2d", "portrait", or "portrait:/path/to/image.png"
        width: ASCII art width in characters.  ``None`` = auto-detect.
        height: ASCII art height in lines.  ``None`` = auto-detect.
        charset: Override rendering charset.  ``None`` = DEFAULT_CHARSET.
            Use "auto" to pick the highest-fidelity mode the terminal supports.
            Use "sixel" to force pixel graphics.

    Returns:
        (frames_dict, rates_dict)
    """
    if name == "cyberpunk":
        from avatar.frames.cyberpunk import FRAMES, FRAME_RATES as RATES
        return FRAMES, RATES

    if name == "layered2d":
        return _load_layered2d_frames(width, height)

    if name == "musetalk":
        return _load_musetalk_frames(width, height)

    if name.startswith("portrait"):
        return _load_portrait_frames(name, width, height, charset)

    raise KeyError(f"Unknown frame set: {name}")


def _load_musetalk_frames(
    width: int | None,
    height: int | None,
) -> tuple[dict[str, list[str]], dict[str, float]]:
    """Load pre-rendered MuseTalk frames with GITS color grade.

    Converts PNG frames to braille strings at startup, caches result.
    """
    import pickle
    from PIL import Image
    from avatar.frames.converter import _braille_convert

    if width is None or height is None:
        auto_w, auto_h = _detect_terminal_size()
        width = width or auto_w
        height = height or auto_h

    gits_dir = Path(__file__).parent.parent.parent.parent / "assets" / "gits_frames"
    cache_dir = Path.home() / ".cache" / "ascii-avatar" / "musetalk"
    cache_file = cache_dir / f"{width}x{height}.pkl"

    # Try cache
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                cached = pickle.load(f)
            log.info("Loaded MuseTalk frames from cache (%dx%d)", width, height)
            return cached, FRAME_RATES
        except Exception:
            pass

    log.info("Building MuseTalk braille frames (%dx%d)...", width, height)

    frames: dict[str, list[str]] = {}
    states = ["idle", "speaking", "thinking", "listening", "error"]

    for state in states:
        state_dir = gits_dir / state
        if not state_dir.exists():
            log.warning("Missing MuseTalk state dir: %s", state_dir)
            frames[state] = []
            continue

        pngs = sorted(state_dir.glob("*.png"))
        state_frames = []
        for png_path in pngs:
            img = Image.open(png_path)
            braille_str = _braille_convert(
                img, width, height,
                invert=False, gits=False,
                tint=(0, 220, 100),
            )
            state_frames.append(braille_str)

        frames[state] = state_frames
        log.info("  %s: %d frames", state, len(state_frames))

    # Micro-events: reuse idle frames for blink/glitch/flicker
    idle_frames = frames.get("idle", [])
    if idle_frames:
        frames["blink"] = idle_frames[:4] if len(idle_frames) >= 4 else idle_frames
        frames["glitch"] = frames.get("error", idle_frames)[:6]
        frames["flicker"] = idle_frames[:3]

    # Cache
    cache_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(frames, f)
    log.info("Cached MuseTalk frames to %s", cache_file)

    return frames, FRAME_RATES


def _load_layered2d_frames(
    width: int | None,
    height: int | None,
) -> tuple[dict[str, list[str]], dict[str, float]]:
    """Generate layered 2.5D avatar frames via FrameAtlasBuilder.

    Uses sixel if the terminal supports it, otherwise falls back to braille.
    """
    from avatar.frames.layered import FrameAtlasBuilder

    # Detect terminal character dimensions
    if width is None or height is None:
        auto_w, auto_h = _detect_terminal_size()
        width = width or auto_w
        height = height or auto_h

    # Always use braille for layered2d — sixel passthrough is unreliable in tmux
    charset = "braille"

    log.info("Layered2D: charset=%s, terminal=%dx%d chars", charset, width, height)

    builder = FrameAtlasBuilder(
        charset=charset,
        char_width=width,
        char_height=height,
        pixel_width=width * 8,
        pixel_height=height * 16,
    )
    return builder.build()


def _load_portrait_frames(
    name: str,
    width: int | None,
    height: int | None,
    charset: str | None,
) -> tuple[dict[str, list[str]], dict[str, float]]:
    """Load portrait-based frames from an image or generate default."""
    from PIL import Image

    # Force braille — sixel passthrough unreliable in tmux
    charset = "braille"

    # Parse custom image path: "portrait:/path/to/image.png"
    if ":" in name and name != "portrait":
        image_path = name.split(":", 1)[1]
        path = Path(image_path)
        # Try relative to project root first
        if not path.is_absolute():
            project_root = Path(__file__).parent.parent.parent.parent
            project_path = (project_root / path).resolve()
            if project_path.exists():
                path = project_path
            else:
                path = path.resolve()
        else:
            path = path.resolve()
        # Restrict to home directory to prevent arbitrary file reads
        home = Path.home().resolve()
        if not str(path).startswith(str(home)):
            raise ValueError(f"Portrait path must be under home directory: {path}")
        if not path.exists():
            raise FileNotFoundError(f"Portrait image not found: {path}")
        base_image = Image.open(path)
    else:
        from avatar.frames.portrait import generate_default_portrait
        base_image = generate_default_portrait()

    # --- Sixel path: pixel-perfect frames ---
    if charset == "sixel":
        return _load_sixel_frames(base_image, width, height)

    # --- Character-based path ---
    from avatar.frames.converter import generate_state_frames

    if width is None or height is None:
        auto_w, auto_h = _detect_terminal_size()
        width = width or auto_w
        height = height or auto_h

    frames = generate_state_frames(base_image, width, height, charset=charset)
    return frames, FRAME_RATES


def _load_sixel_frames(
    base_image: "Image.Image",
    width: int | None,
    height: int | None,
) -> tuple[dict[str, list[str]], dict[str, float]]:
    """Generate sixel pixel-graphics frames.

    Determines pixel dimensions from terminal ioctl or falls back to
    character-cell estimation.
    """
    from avatar.frames.sixel import (
        generate_sixel_state_frames,
        get_terminal_cell_size,
        get_terminal_pixel_size,
    )

    # Try to get actual pixel dimensions of the avatar pane
    pixel_size = get_terminal_pixel_size()
    cell_size = get_terminal_cell_size()

    if pixel_size and pixel_size[0] > 0:
        px_w, px_h = pixel_size
        # Leave room for status bar (1 row of cells)
        if cell_size:
            px_h -= cell_size[1] * 2
        px_h = max(px_h, 100)
        log.info("Sixel: using terminal pixel size %d×%d", px_w, px_h)
    else:
        # Estimate from character cells — assume 8×16 px per cell
        if width is None or height is None:
            auto_w, auto_h = _detect_terminal_size()
            width = width or auto_w
            height = height or auto_h
        cell_w = 8
        cell_h = 16
        px_w = width * cell_w
        px_h = height * cell_h
        log.info("Sixel: estimated pixel size %d×%d (%d×%d cells)", px_w, px_h, width, height)

    # Maintain aspect ratio of source image
    src_w, src_h = base_image.size
    aspect = src_w / src_h
    if px_w / px_h > aspect:
        # Terminal is wider than image — fit to height
        px_w = int(px_h * aspect)
    else:
        # Terminal is taller — fit to width
        px_h = int(px_w / aspect)

    frames = generate_sixel_state_frames(
        base_image,
        pixel_width=px_w,
        pixel_height=px_h,
        max_colors=128,
    )
    return frames, FRAME_RATES
