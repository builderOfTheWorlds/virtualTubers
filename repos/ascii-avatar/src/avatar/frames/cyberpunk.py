"""Cyberpunk ASCII avatar frame set — high fidelity edition.

A terminal-native AI face with:
- Detailed eyes with iris rings, eyelid curves, and blink animation
- Expressive eyebrows that change per state
- Cybernetic implant panel (right temple) with scrolling data
- Shaped nose bridge and nostrils
- Lip-contour mouth with 5 shapes (closed, slight, open, wide, glitch)
- Jawline contour and hair/visor detail
- 256-color ANSI accents (magenta implant, green circuit traces)

Frame builder architecture: components compose into full frames,
ensuring pixel-perfect alignment and easy animation expansion.
"""

from __future__ import annotations

import re

# ── ANSI color codes ─────────────────────────────────────────
C = "\033[36m"    # cyan — primary face color
R = "\033[31m"    # red — error state
D = "\033[2m"     # dim — background/shadow
B = "\033[1m"     # bold — highlights
X = "\033[0m"     # reset
M = "\033[35m"    # magenta — implant / accent
G = "\033[32m"    # green — circuit traces
W = "\033[97m"    # bright white — specular highlights

_ANSI = re.compile(r"\033\[[0-9;]*m")


# ── Alignment helpers ────────────────────────────────────────

def _vlen(s: str) -> int:
    """Visible length after stripping ANSI escape codes."""
    return len(_ANSI.sub("", s))


def _row(content: str, color: str = C) -> str:
    """Build a bordered row, auto-padded to exactly 38 visible chars."""
    vl = _vlen(content)
    if vl > 38:
        raise ValueError(
            f"Row content too wide ({vl} visible chars): "
            f"{_ANSI.sub('', content)!r}"
        )
    return f"{color}│{X}{content}{' ' * (38 - vl)}{color}│{X}"


def _border(top: bool = True, color: str = C) -> str:
    ch = "┌" if top else "└"
    end = "┐" if top else "┘"
    return f"{color}{ch}{'─' * 38}{end}{X}"


# ── CIRCUIT NOISE PATTERNS ───────────────────────────────────
# Three density levels for breathing animation

_CIRCUITS = [
    # light (idle trough)
    f" {D}░▒▓▒░ ▒░{G}┄{D}▓░░  ░░░  ░▓{G}┄{D}░▒ ░▓ ░▒▓▒{X} ",
    # medium (idle mid)
    f" {D}▒▓█▓▒ ▓▒{G}┅{D}█▒▒  ▒▒▒  █▓{G}┅{D}▓▒ ▓█ ▓▒█▓{X} ",
    # heavy (idle peak / listening)
    f" {D}▓█▓█▓ █▓{G}╍{D}█▓▓  ▓▓▓  █▓{G}╍{D}█▓ █▓ █▓█▓{X} ",
]


# ── HAIR / VISOR ─────────────────────────────────────────────

def _hair(color: str = C) -> str:
    return f"  {color}▄{D}▓{color}████████████████████████{D}▓{color}▄{X}      "


# ── FOREHEAD ─────────────────────────────────────────────────

def _forehead(imp_glow: bool = False, color: str = C) -> str:
    g = f"{M}▓{B}▒{X}{M}▓{X}" if imp_glow else f"{D}░▒░{X}"
    return f"  {D}█{color}▓{D}▒░{X}                    {D}░▒{color}▓{D}█{X} {g}  "


# ── EYEBROWS ─────────────────────────────────────────────────
# Three styles: neutral (relaxed arch), raised (alert/listening),
# furrowed (error/angry)

_BROW_STYLES = {
    "neutral":  lambda c: f"    {c}▄▀▀▀▀▄{X}        {c}▄▀▀▀▀▄{X}",
    "raised":   lambda c: f"   {c}▄▀▀▀▀▀▀▄{X}      {c}▄▀▀▀▀▀▀▄{X}",
    "furrowed": lambda c: f"    {c}▀▄▄▄▄▀{X}        {c}▀▄▄▄▄▀{X}",
}

# Implant header — cycles through 3 phases
_IMP_TOP = [
    f"{M}╔═{G}╤{M}══╗{X}",
    f"{M}╔{G}╤{M}═{G}╤{M}═╗{X}",
    f"{M}╔══{G}╤{M}═╗{X}",
]


def _eyebrows(style: str = "neutral", color: str = C, imp_phase: int = 0) -> str:
    brow_fn = _BROW_STYLES.get(style, _BROW_STYLES["neutral"])
    brow = brow_fn(color)
    return brow + f" {_IMP_TOP[imp_phase % 3]}"


# ── EYES ─────────────────────────────────────────────────────
# States: open, half, closed, wide (listening), dead (error)

# Implant body rows — scrolling data display
_IMP_BODY = [
    [f"{M}║{D}▒▓{G}│{D}░▒{M}║{X}", f"{M}║{D}░▒{G}│{D}▓░{M}║{X}", f"{M}║{D}▓░{G}│{D}▒▓{M}║{X}"],
    [f"{M}╠═{G}═╪═{M}═╣{X}", f"{M}╠{G}═╪═╪{M}═╣{X}", f"{M}╠══{G}╪{M}══╣{X}"],
    [f"{M}║{D}░▓{G}│{D}▒░{M}║{X}", f"{M}║{D}▓▒{G}│{D}░▓{M}║{X}", f"{M}║{D}▒░{G}│{D}▓▒{M}║{X}"],
]

_IMP_BOT = [
    f"{M}╚═{G}╧{M}══╝{X}",
    f"{M}╚{G}╧{M}═{G}╧{M}═╝{X}",
    f"{M}╚══{G}╧{M}═╝{X}",
]


def _eye_row(row_type: str, style: str = "open", pupil: str = "center",
             color: str = C, imp_phase: int = 0) -> str:
    """Build one of the three eye rows (upper/pupil/lower).

    row_type: "upper", "pupil", or "lower"
    """
    p = imp_phase % 3

    if row_type == "upper":
        if style == "closed":
            eyes = f"  {D}──────{X}          {D}──────{X}"
        else:
            eyes = f"  {color}╭━━━━━━╮{X}        {color}╭━━━━━━╮{X}"
        imp = _IMP_BODY[0][p]

    elif row_type == "pupil":
        if style == "closed":
            eyes = f"  {color}━━━━━━━━{X}        {color}━━━━━━━━{X}"
        elif style == "dead":
            eyes = (f"  {color}┃{X} {R}{B}X{X}  {R}{B}X{X} {color}┃{X}"
                    f"        {color}┃{X} {R}{B}X{X}  {R}{B}X{X} {color}┃{X}")
        elif style == "wide":
            # Listening — bright expanded iris
            iris = f"{W}◉{X}"
            eyes = (f"  {color}┃{X}{iris}{B}{C} ◉◉ {X}{iris}{color}┃{X}"
                    f"        {color}┃{X}{iris}{B}{C} ◉◉ {X}{iris}{color}┃{X}")
        elif style == "half":
            # Half-closed — eyelid descending
            eyes = (f"  {color}┃{X} {D}▄▄▄▄{X} {color}┃{X}"
                    f"        {color}┃{X} {D}▄▄▄▄{X} {color}┃{X}")
        elif style == "dim":
            # Thinking flicker — dim pupils
            eyes = (f"  {color}┃{X}{D}▓{X} {D}○○{X} {D}▓{X}{color}┃{X}"
                    f"        {color}┃{X}{D}▓{X} {D}○○{X} {D}▓{X}{color}┃{X}")
        else:
            # Normal open with iris ring
            pupils = {
                "center": f"{D}▓{X} {B}{C}◉◉{X} {D}▓{X}",
                "left":   f"{D}▓{X}{B}{C}◉◉{X}  {D}▓{X}",
                "right":  f"{D}▓{X}  {B}{C}◉◉{X}{D}▓{X}",
            }
            pu = pupils.get(pupil, pupils["center"])
            eyes = f"  {color}┃{X}{pu}{color}┃{X}        {color}┃{X}{pu}{color}┃{X}"
        imp = _IMP_BODY[1][p]

    else:  # lower
        if style == "closed":
            eyes = f"  {D}──────{X}          {D}──────{X}"
        else:
            eyes = f"  {color}╰━━━━━━╯{X}        {color}╰━━━━━━╯{X}"
        imp = _IMP_BODY[2][p]

    return eyes + f" {imp}"


# ── CHEEK ────────────────────────────────────────────────────

def _cheek(color: str = C, imp_phase: int = 0) -> str:
    imp = _IMP_BOT[imp_phase % 3]
    return f"    {D}╲{color}▄▄{D}╱{X}            {D}╲{color}▄▄{D}╱{X}   {imp}"


# ── NOSE ─────────────────────────────────────────────────────

def _nose_bridge(color: str = C) -> str:
    return f"         {D}╲{X}   {color}▄▄▄▄{X}   {D}╱{X}   {D}░▒▓█▓▒░{X}"


def _nose_tip(color: str = C) -> str:
    return f"          {D}╲{X} {color}▐{W}▓▓{X}{color}▌{X} {D}╱{X}   {D}░▓████▓░{X}"


def _nostrils(color: str = C) -> str:
    return f"           {color}╰{D}▄▄{color}╯{X}      {D}░▒▓▒▓▒░{X}"


# ── MOUTH ────────────────────────────────────────────────────
# 6 shapes: closed, slight, open, wide, glitch, error-wide

def _mouth_upper(color: str = C) -> str:
    return f"      {color}╭─────────────╮{X}    {D}░▒▓▒░{X}"


def _mouth_content(frame: int = 0, color: str = C) -> str:
    mouths = [
        # 0: closed — resting
        f"      {color}│{X}   {color}─────────{X}   {color}│{X}   {D}░▒▓▒░{X}",
        # 1: slight open
        f"      {color}│{X}   {color}─╌─╌─╌─{X}   {color}│{X}   {D}░▒▓▒░{X}",
        # 2: open
        f"      {color}│{X}  {color}╌{X}         {color}╌{X}  {color}│{X}   {D}░▒▓▒░{X}",
        # 3: wide — teeth visible
        f"      {color}│{X} {W}▄{B}═══════{X}{W}▄{X} {color}│{X}   {D}░▒▓▒░{X}",
        # 4: glitch
        f"      {color}│{X}   {R}─╫─╪─╫─{X}   {color}│{X}   {D}░▒▓▒░{X}",
    ]
    return mouths[frame % len(mouths)]


def _mouth_lower(color: str = C) -> str:
    return f"      {color}╰─────────────╯{X}    {D}░▒▓▒░{X}"


# ── CHIN / JAWLINE ───────────────────────────────────────────

def _chin(color: str = C) -> str:
    return f"     {D}╲{color}▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄{color}{D}╱{X}"


def _jawline(color: str = C) -> str:
    return f"       {D}▀▀▀▀▀▀▀▀▀▀▀▀▀▀{X}"


# ── NECK / SHOULDERS ─────────────────────────────────────────

def _neck() -> str:
    return f"        {D}▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄{X}"


# ═══════════════════════════════════════════════════════════
# FRAME BUILDER
# ═══════════════════════════════════════════════════════════

def _build_frame(
    circuit_d: int = 0,
    eyebrow: str = "neutral",
    eye: str = "open",
    pupil: str = "center",
    mouth: int = 0,
    imp_phase: int = 0,
    color: str = C,
    scanline: int | None = None,
) -> str:
    """Assemble a complete avatar frame from face components.

    Args:
        circuit_d:  Circuit noise density (0=light, 1=medium, 2=heavy).
        eyebrow:    Brow style: "neutral", "raised", or "furrowed".
        eye:        Eye state: "open", "half", "closed", "wide", "dead", "dim".
        pupil:      Pupil position: "center", "left", "right".
        mouth:      Mouth shape index (0-4).
        imp_phase:  Implant animation phase (0-2).
        color:      Primary color code (C=cyan, R=red).
        scanline:   If set, replace that row index with a glowing scan bar.
    """
    rows = [
        _CIRCUITS[circuit_d % 3],                                         # 0
        _hair(color),                                                      # 1
        _forehead(imp_phase > 0, color),                                   # 2
        _eyebrows(eyebrow, color, imp_phase),                              # 3
        _eye_row("upper", eye, pupil, color, imp_phase),                   # 4
        _eye_row("pupil", eye, pupil, color, imp_phase),                   # 5
        _eye_row("lower", eye, pupil, color, imp_phase),                   # 6
        _cheek(color, imp_phase),                                          # 7
        _nose_bridge(color),                                               # 8
        _nose_tip(color),                                                  # 9
        _nostrils(color),                                                  # 10
        _mouth_upper(color),                                               # 11
        _mouth_content(mouth, color),                                      # 12
        _mouth_lower(color),                                               # 13
        _chin(color),                                                      # 14
        _jawline(color),                                                   # 15
        _CIRCUITS[circuit_d % 3],                                          # 16
        _neck(),                                                           # 17
    ]

    lines = [_border(True, color)]
    for i, content in enumerate(rows):
        if scanline is not None and i == scanline:
            content = f" {color}▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓{X} "
        lines.append(_row(content, color))
    lines.append(_border(False, color))
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# IDLE — breathing circuit pulse, occasional blink + glance
# 10-frame cycle (~8s at 0.8s/frame)
# ═══════════════════════════════════════════════════════════

_IDLE_A = _build_frame(circuit_d=0, pupil="center", imp_phase=0)
_IDLE_B = _build_frame(circuit_d=1, pupil="center", imp_phase=1)
_IDLE_C = _build_frame(circuit_d=2, pupil="center", imp_phase=2)
_IDLE_GLANCE = _build_frame(circuit_d=1, pupil="right", imp_phase=1)
_IDLE_BLINK = _build_frame(circuit_d=1, eye="closed", imp_phase=1)

# Natural idle loop: breathe in → peak → breathe out → glance → blink
_IDLE_FRAMES = [
    _IDLE_A,       # light
    _IDLE_B,       # medium
    _IDLE_C,       # heavy (peak)
    _IDLE_B,       # medium (exhale)
    _IDLE_A,       # light
    _IDLE_B,       # medium
    _IDLE_GLANCE,  # subtle glance right
    _IDLE_B,       # back to center
    _IDLE_A,       # light
    _IDLE_BLINK,   # blink!
]

# ═══════════════════════════════════════════════════════════
# THINKING — scanline sweep, flickering eyes, pupil drift
# 6 frames for smoother scan
# ═══════════════════════════════════════════════════════════

_THINK_FRAMES = [
    _build_frame(circuit_d=1, eye="open", pupil="center", scanline=0,  imp_phase=0),
    _build_frame(circuit_d=1, eye="dim",  pupil="left",   scanline=3,  imp_phase=1),
    _build_frame(circuit_d=2, eye="open", pupil="center", scanline=6,  imp_phase=2),
    _build_frame(circuit_d=1, eye="dim",  pupil="right",  scanline=9,  imp_phase=0),
    _build_frame(circuit_d=2, eye="open", pupil="center", scanline=12, imp_phase=1),
    _build_frame(circuit_d=1, eye="half", pupil="center", scanline=15, imp_phase=2),
]

# ═══════════════════════════════════════════════════════════
# SPEAKING — mouth driven by MouthSync (4 shapes)
# Slight circuit activity, eyes steady
# ═══════════════════════════════════════════════════════════

_SPEAK_FRAMES = [
    _build_frame(circuit_d=1, mouth=0, imp_phase=0),  # closed
    _build_frame(circuit_d=1, mouth=1, imp_phase=1),  # slight
    _build_frame(circuit_d=1, mouth=2, imp_phase=2),  # open
    _build_frame(circuit_d=1, mouth=3, imp_phase=0),  # wide
]

# ═══════════════════════════════════════════════════════════
# LISTENING — raised brows, wide bright eyes, intense circuit
# 3 frames with pulsing brightness
# ═══════════════════════════════════════════════════════════

_LISTEN_FRAMES = [
    _build_frame(circuit_d=1, eyebrow="raised", eye="wide", imp_phase=0),
    _build_frame(circuit_d=2, eyebrow="raised", eye="wide", imp_phase=1),
    _build_frame(circuit_d=1, eyebrow="raised", eye="wide", imp_phase=2),
]

# ═══════════════════════════════════════════════════════════
# ERROR — red tint, furrowed brows, dead/glitching eyes
# Glitch mouth, scanline corruption
# ═══════════════════════════════════════════════════════════

_ERROR_FRAMES = [
    _build_frame(
        circuit_d=2, eyebrow="furrowed", eye="open", mouth=4,
        color=R, imp_phase=0, scanline=8,
    ),
    _build_frame(
        circuit_d=2, eyebrow="furrowed", eye="dead", mouth=4,
        color=R, imp_phase=2, scanline=5,
    ),
]


# ═══════════════════════════════════════════════════════════
# EXPORTS — consumed by load_frame_set()
# ═══════════════════════════════════════════════════════════

FRAMES: dict[str, list[str]] = {
    "idle": _IDLE_FRAMES,
    "thinking": _THINK_FRAMES,
    "speaking": _SPEAK_FRAMES,
    "listening": _LISTEN_FRAMES,
    "error": _ERROR_FRAMES,
}

FRAME_RATES: dict[str, float] = {
    "idle": 0.8,
    "thinking": 0.15,
    "speaking": 0.1,
    "listening": 0.4,
    "error": 0.2,
}


# ── Alignment validation (runs at import time) ──────────────

def _validate_frames() -> None:
    """Verify every frame line is exactly 40 visible chars wide."""
    for state, frames in FRAMES.items():
        for fi, frame in enumerate(frames):
            for li, line in enumerate(frame.split("\n")):
                vl = _vlen(line)
                if vl != 40:
                    raise ValueError(
                        f"Frame {state}[{fi}] line {li}: "
                        f"expected 40 visible chars, got {vl}: "
                        f"{_ANSI.sub('', line)!r}"
                    )


_validate_frames()
