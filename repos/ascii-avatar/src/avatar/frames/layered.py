"""Layered 2.5D avatar compositing system.

Decomposes the avatar face into depth-ordered transparent PNG layers.
Pre-composites layer combinations into sixel frames at startup.
At runtime, the avatar indexes into pre-baked frames for zero-CPU animation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance

log = logging.getLogger(__name__)

CANVAS_SIZE = (512, 512)

ASSETS_DIR = Path(__file__).parent.parent.parent.parent / "assets" / "layers"

CACHE_DIR = Path.home() / ".cache" / "ascii-avatar" / "frames"

# Parallax horizontal pixel offsets per layer at max head rotation (±15 deg).
# Deeper layers shift more to create depth illusion.
PARALLAX_OFFSETS: dict[str, int] = {
    "background": 12,
    "hair": 8,
    "face": 5,
    "eyes": 2,
    "eyebrows": 2,
    "nose": 1,
    "mouth": 1,
    "overlay": 0,
}

# Layer definitions: ordered bottom-to-top, each with named variants.
LAYER_DEFS: dict[str, dict[str, Any]] = {
    "background": {
        "depth": 0,
        "variants": [
            {"name": "dim", "file": "background/bg_dim.png"},
            {"name": "pulse", "file": "background/bg_pulse.png"},
            {"name": "error", "file": "background/bg_error.png"},
        ],
    },
    "hair": {
        "depth": 1,
        "variants": [
            {"name": "center", "file": "hair/hair_center.png"},
            {"name": "left", "file": "hair/hair_left.png"},
            {"name": "right", "file": "hair/hair_right.png"},
        ],
    },
    "face": {
        "depth": 2,
        "variants": [
            {"name": "center", "file": "face/face_center.png"},
            {"name": "left15", "file": "face/face_left15.png"},
            {"name": "right15", "file": "face/face_right15.png"},
            {"name": "up10", "file": "face/face_up10.png"},
            {"name": "down10", "file": "face/face_down10.png"},
        ],
    },
    "eyes": {
        "depth": 3,
        "variants": [
            {"name": "center_open", "file": "eyes/eyes_center_open.png"},
            {"name": "center_half", "file": "eyes/eyes_center_half.png"},
            {"name": "center_closed", "file": "eyes/eyes_center_closed.png"},
            {"name": "left_open", "file": "eyes/eyes_left_open.png"},
            {"name": "left_half", "file": "eyes/eyes_left_half.png"},
            {"name": "left_closed", "file": "eyes/eyes_left_closed.png"},
            {"name": "right_open", "file": "eyes/eyes_right_open.png"},
            {"name": "right_half", "file": "eyes/eyes_right_half.png"},
            {"name": "right_closed", "file": "eyes/eyes_right_closed.png"},
            {"name": "up_open", "file": "eyes/eyes_up_open.png"},
            {"name": "up_half", "file": "eyes/eyes_up_half.png"},
            {"name": "up_closed", "file": "eyes/eyes_up_closed.png"},
            {"name": "down_open", "file": "eyes/eyes_down_open.png"},
            {"name": "down_half", "file": "eyes/eyes_down_half.png"},
            {"name": "down_closed", "file": "eyes/eyes_down_closed.png"},
        ],
    },
    "eyebrows": {
        "depth": 4,
        "variants": [
            {"name": "neutral", "file": "eyebrows/brows_neutral.png"},
            {"name": "raised", "file": "eyebrows/brows_raised.png"},
            {"name": "furrowed", "file": "eyebrows/brows_furrowed.png"},
            {"name": "asymmetric", "file": "eyebrows/brows_asymmetric.png"},
        ],
    },
    "nose": {
        "depth": 5,
        "variants": [
            {"name": "center", "file": "nose/nose_center.png"},
            {"name": "left", "file": "nose/nose_left.png"},
            {"name": "right", "file": "nose/nose_right.png"},
        ],
    },
    "mouth": {
        "depth": 6,
        "variants": [
            {"name": "closed", "file": "mouth/mouth_closed.png"},
            {"name": "slight", "file": "mouth/mouth_slight.png"},
            {"name": "open", "file": "mouth/mouth_open.png"},
            {"name": "wide", "file": "mouth/mouth_wide.png"},
            {"name": "smile", "file": "mouth/mouth_smile.png"},
            {"name": "glitch", "file": "mouth/mouth_glitch.png"},
        ],
    },
    "overlay": {
        "depth": 7,
        "variants": [
            {"name": "scanline_light", "file": "overlay/scanline_light.png"},
            {"name": "scanline_heavy", "file": "overlay/scanline_heavy.png"},
            {"name": "crt_bloom", "file": "overlay/crt_bloom.png"},
            {"name": "holo_flicker", "file": "overlay/holo_flicker.png"},
            {"name": "chrom_aberr", "file": "overlay/chrom_aberr.png"},
            {"name": "glitch_corrupt", "file": "overlay/glitch_corrupt.png"},
            {"name": "noise_bands", "file": "overlay/noise_bands.png"},
            {"name": "red_tint", "file": "overlay/red_tint.png"},
        ],
    },
}


def _head_angle_to_name(angle: str) -> dict[str, str]:
    """Map a head angle keyword to face/hair/nose variant names."""
    mapping = {
        "center": {"face": "center", "hair": "center", "nose": "center"},
        "left": {"face": "left15", "hair": "left", "nose": "left"},
        "right": {"face": "right15", "hair": "right", "nose": "right"},
        "up": {"face": "up10", "hair": "center", "nose": "center"},
        "down": {"face": "down10", "hair": "center", "nose": "center"},
    }
    return mapping[angle]


def _build_combo(
    head: str,
    eyes: str,
    brows: str,
    mouth: str,
    overlay: str,
    bg: str = "dim",
) -> dict[str, str]:
    """Build a layer combination dict from semantic parameters."""
    angle = _head_angle_to_name(head)
    return {
        "background": bg,
        "hair": angle["hair"],
        "face": angle["face"],
        "eyes": eyes,
        "eyebrows": brows,
        "nose": angle["nose"],
        "mouth": mouth,
        "overlay": overlay,
    }


# State-to-frame mapping: each state is a list of layer combination dicts.
# Each combo is one pre-rendered frame.

# --- Idle: ~30 frames ---
_idle_frames = []
# Base idle loop: center, open eyes, breathing glow
for overlay in ["scanline_light", "crt_bloom", "scanline_light"]:
    _idle_frames.append(_build_combo("center", "center_open", "neutral", "closed", overlay))
# Blink cycle
_idle_frames.append(_build_combo("center", "center_half", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "center_closed", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "center_half", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "center_open", "neutral", "closed", "scanline_light"))
# Glance left
_idle_frames.append(_build_combo("center", "left_open", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "left_open", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "center_open", "neutral", "closed", "scanline_light"))
# Subtle head drift left
for overlay in ["scanline_light", "crt_bloom", "scanline_light"]:
    _idle_frames.append(_build_combo("left", "center_open", "neutral", "closed", overlay))
# Return center
for overlay in ["scanline_light", "crt_bloom"]:
    _idle_frames.append(_build_combo("center", "center_open", "neutral", "closed", overlay))
# Glance right
_idle_frames.append(_build_combo("center", "right_open", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "right_open", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "center_open", "neutral", "closed", "scanline_light"))
# Head drift right
for overlay in ["scanline_light", "crt_bloom", "scanline_light"]:
    _idle_frames.append(_build_combo("right", "center_open", "neutral", "closed", overlay))
# Settle back center
for overlay in ["scanline_light", "crt_bloom", "scanline_light"]:
    _idle_frames.append(_build_combo("center", "center_open", "neutral", "closed", overlay))
# Second blink
_idle_frames.append(_build_combo("center", "center_half", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "center_closed", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "center_half", "neutral", "closed", "scanline_light"))
_idle_frames.append(_build_combo("center", "center_open", "neutral", "closed", "crt_bloom"))

# --- Thinking: ~20 frames ---
_thinking_frames = []
# Slow drift with furrowed brows, half-closed eyes, chromatic aberration
for head in ["left", "left", "center", "center", "right", "right", "center", "center"]:
    _thinking_frames.append(_build_combo(head, "center_half", "furrowed", "closed", "chrom_aberr", bg="pulse"))
# Pupil wander
for eyes in ["left_half", "up_half", "right_half", "center_half"]:
    _thinking_frames.append(_build_combo("center", eyes, "furrowed", "closed", "scanline_heavy", bg="pulse"))
# Scanline sweep phases
for overlay in ["scanline_heavy", "chrom_aberr", "scanline_heavy", "crt_bloom"]:
    _thinking_frames.append(_build_combo("center", "center_half", "furrowed", "closed", overlay, bg="pulse"))
# More drift
for head in ["left", "center", "right", "center"]:
    _thinking_frames.append(_build_combo(head, "left_half", "furrowed", "closed", "chrom_aberr", bg="pulse"))

# --- Speaking: ~24 frames ---
_speaking_frames = []
# Mouth cycle with head nod, 4 mouth shapes × 2 head positions × 3 overlays
for head in ["center", "up"]:
    for mouth in ["closed", "slight", "open", "wide", "open", "slight"]:
        for overlay in ["scanline_light", "crt_bloom"]:
            _speaking_frames.append(_build_combo(head, "center_open", "neutral", mouth, overlay))

# --- Listening: ~12 frames ---
_listening_frames = []
# Attentive: wide eyes, raised brows, slight smile, head slightly tilted
for head in ["left", "center", "center", "right"]:
    for overlay in ["crt_bloom", "scanline_light", "crt_bloom"]:
        _listening_frames.append(_build_combo(head, "center_open", "raised", "smile", overlay))

# --- Error: ~16 frames ---
_error_frames = []
# Jitter with glitch overlays, dead eyes, furrowed brows
for head in ["left", "right", "center", "up", "down", "left", "right", "center"]:
    _error_frames.append(_build_combo(head, "center_closed", "furrowed", "glitch", "glitch_corrupt", bg="error"))
    _error_frames.append(_build_combo(head, "down_open", "furrowed", "glitch", "noise_bands", bg="error"))

# --- Micro-events ---
_blink_frames = [
    _build_combo("center", "center_open", "neutral", "closed", "scanline_light"),
    _build_combo("center", "center_half", "neutral", "closed", "scanline_light"),
    _build_combo("center", "center_closed", "neutral", "closed", "scanline_light"),
    _build_combo("center", "center_half", "neutral", "closed", "scanline_light"),
]

_glitch_frames = [
    _build_combo("center", "center_open", "neutral", "closed", "glitch_corrupt"),
    _build_combo("left", "center_open", "neutral", "closed", "noise_bands"),
    _build_combo("right", "center_open", "neutral", "closed", "glitch_corrupt"),
    _build_combo("center", "center_half", "neutral", "closed", "red_tint"),
    _build_combo("center", "center_open", "neutral", "closed", "noise_bands"),
    _build_combo("center", "center_open", "neutral", "closed", "glitch_corrupt"),
]

_flicker_frames = [
    _build_combo("center", "center_open", "neutral", "closed", "holo_flicker"),
    _build_combo("center", "center_half", "neutral", "closed", "holo_flicker"),
    _build_combo("center", "center_open", "neutral", "closed", "scanline_light"),
]

STATE_FRAME_MAP: dict[str, list[dict[str, str]]] = {
    "idle": _idle_frames,
    "thinking": _thinking_frames,
    "speaking": _speaking_frames,
    "listening": _listening_frames,
    "error": _error_frames,
    "blink": _blink_frames,
    "glitch": _glitch_frames,
    "flicker": _flicker_frames,
}

# Parallax multipliers for each head angle direction.
# Negative = shift left, positive = shift right, zero = no shift.
_ANGLE_MULTIPLIERS: dict[str, float] = {
    "center": 0.0,
    "left": -1.0,
    "right": 1.0,
    "up": 0.0,
    "down": 0.0,
}


def _apply_gits_color_grade(img: Image.Image) -> Image.Image:
    """Apply Ghost in the Shell inspired color grade.

    Pixel-by-pixel mapping:
    - highlights (luminance >= 192) → cyan tint
    - midtones (64 <= luminance < 192) → violet tint
    - shadows (luminance < 64) → deep purple tint
    """
    rgb = img.convert("RGB")
    pixels = rgb.load()
    width, height = rgb.size
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            lum = int(0.299 * r + 0.587 * g + 0.114 * b)
            if lum >= 192:
                # Highlights: cyan (boost G and B, reduce R)
                r2 = max(0, int(r * 0.7))
                g2 = min(255, int(g * 1.1))
                b2 = min(255, int(b * 1.2))
            elif lum >= 64:
                # Midtones: violet (boost R and B, reduce G)
                r2 = min(255, int(r * 1.1))
                g2 = max(0, int(g * 0.8))
                b2 = min(255, int(b * 1.2))
            else:
                # Shadows: deep purple (strong R and B boost, crush G)
                r2 = min(255, int(r * 1.2 + 20))
                g2 = max(0, int(g * 0.6))
                b2 = min(255, int(b * 1.3 + 30))
            pixels[x, y] = (r2, g2, b2)
    return rgb


class LayerCompositor:
    """Composites avatar layers into a single image with parallax and color grading."""

    def __init__(self, assets_dir: Path, canvas_size: tuple[int, int] = CANVAS_SIZE) -> None:
        self.assets_dir = Path(assets_dir)
        self.canvas_size = canvas_size
        self.layers: dict[str, dict[str, Image.Image]] = {}
        self._load_all_layers()

    def _load_all_layers(self) -> None:
        """Load all layer variant PNGs from assets_dir, resized to canvas_size."""
        for layer_name, layer_def in LAYER_DEFS.items():
            self.layers[layer_name] = {}
            for variant in layer_def["variants"]:
                variant_name = variant["name"]
                file_path = self.assets_dir / variant["file"]
                img = Image.open(file_path).convert("RGBA")
                img = img.resize(self.canvas_size, Image.LANCZOS)
                self.layers[layer_name][variant_name] = img

    def composite(self, combo: dict[str, str], head_angle: str) -> Image.Image:
        """Composite layers bottom-to-top with parallax, apply GITS color grade.

        Args:
            combo: mapping of layer name -> variant name (e.g. from STATE_FRAME_MAP)
            head_angle: one of 'center', 'left', 'right', 'up', 'down'

        Returns:
            RGB Image at self.canvas_size
        """
        canvas = Image.new("RGB", self.canvas_size, (10, 10, 15))
        multiplier = _ANGLE_MULTIPLIERS.get(head_angle, 0.0)

        for layer_name in LAYER_DEFS:
            variant_name = combo.get(layer_name)
            if variant_name is None:
                continue
            layer_img = self.layers[layer_name][variant_name]
            offset_x = int(PARALLAX_OFFSETS[layer_name] * multiplier)
            canvas.paste(layer_img, (offset_x, 0), mask=layer_img.split()[3])

        graded = _apply_gits_color_grade(canvas)
        return graded


# Maps face variant names to head angle keywords used by LayerCompositor.composite().
_FACE_TO_ANGLE: dict[str, str] = {
    "center": "center",
    "left15": "left",
    "right15": "right",
    "up10": "up",
    "down10": "down",
}

# Animation frame rates (frames per second) for each avatar state.
FRAME_RATES: dict[str, float] = {
    "idle": 0.8,
    "thinking": 0.15,
    "speaking": 0.1,
    "listening": 0.4,
    "error": 0.2,
}


class FrameAtlasBuilder:
    """Builds and caches a full frame atlas for all avatar states.

    Supports both sixel (pixel-perfect) and braille (Unicode) encoding.
    """

    def __init__(
        self,
        assets_dir: Path | None = None,
        cache_dir: Path | None = None,
        pixel_width: int = 512,
        pixel_height: int = 512,
        max_colors: int = 128,
        charset: str = "braille",
        char_width: int = 60,
        char_height: int = 33,
    ) -> None:
        self.assets_dir = Path(assets_dir) if assets_dir is not None else ASSETS_DIR
        self.cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
        self.pixel_width = pixel_width
        self.pixel_height = pixel_height
        self.max_colors = max_colors
        self.charset = charset  # "sixel" or "braille"
        self.char_width = char_width
        self.char_height = char_height

    def _cache_key(self) -> str:
        """Compute a SHA-256 cache key from dimensions and all layer file contents."""
        h = hashlib.sha256()
        h.update(f"{self.charset}:{self.pixel_width}x{self.pixel_height}".encode())
        h.update(f":{self.char_width}x{self.char_height}:{self.max_colors}".encode())
        for layer_name, layer_def in sorted(LAYER_DEFS.items()):
            for variant in layer_def["variants"]:
                file_path = self.assets_dir / variant["file"]
                try:
                    h.update(file_path.read_bytes())
                except FileNotFoundError:
                    h.update(str(file_path).encode())
        return h.hexdigest()

    def _cache_path(self) -> Path:
        key = self._cache_key()
        return self.cache_dir / key[:16] / "atlas.pkl"

    def _load_cache(self) -> dict | None:
        path = self._cache_path()
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def _save_cache(self, frames: dict) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(frames, f)

    def _encode_frame(self, img: Image.Image) -> str:
        """Encode a composited PIL Image to the target charset string."""
        if self.charset == "sixel":
            from avatar.frames.sixel import encode_sixel
            return encode_sixel(img, max_colors=self.max_colors)
        else:
            from avatar.frames.converter import _braille_convert
            return _braille_convert(
                img, self.char_width, self.char_height,
                invert=True, gits=True, color_accent="cyan",
            )

    def build(self) -> tuple[dict[str, list[str]], dict[str, float]]:
        """Build or load the frame atlas.

        Returns:
            (frames, FRAME_RATES) where frames maps state name -> list of encoded strings.
        """
        cached = self._load_cache()
        if cached is not None:
            return cached, FRAME_RATES

        log.info("Building layered2d frame atlas (%s, %dx%d)...",
                 self.charset, self.char_width, self.char_height)

        compositor = LayerCompositor(self.assets_dir)
        frames: dict[str, list[str]] = {}

        for state, combos in STATE_FRAME_MAP.items():
            state_frames: list[str] = []
            for combo in combos:
                face_variant = combo.get("face", "center")
                head_angle = _FACE_TO_ANGLE.get(face_variant, "center")

                img = compositor.composite(combo, head_angle)
                encoded = self._encode_frame(img)
                state_frames.append(encoded)
            frames[state] = state_frames
            log.info("  %s: %d frames", state, len(state_frames))

        self._save_cache(frames)
        log.info("Frame atlas cached.")
        return frames, FRAME_RATES
