"""
theme.py — a campaign's central musical theme, as DATA.

The theme is written in SCALE DEGREES, not absolute pitches. That is the
whole trick behind "one theme, many emotions": when the mood swaps the mode
(major -> aeolian -> phrygian ...) the same degree sequence is re-voiced in
the new mode, so the melodic contour and rhythm — what listeners actually
recognize — survive while the emotional colour changes (CMERS's finding
that mode/harmony carry valence; Livingstone et al. 2010).

YAML shape (campaigns/<name>/music/theme.yaml):

    name: ashiorid
    tonic: D            # key centre (C, C#, Db, ... B)
    base_octave: 3      # octave of the pad/chord register
    beats_per_bar: 4
    seed: 1234          # makes every rendering reproducible
    motif:              # [degree, beats] or [degree, beats, octave_shift]
      - [1, 1]          # degree 1 = tonic; 0 = rest
      - [5, 1]
      - [6, 0.5, 0]
    progression: [1, 6, 3, 7]   # one chord (root scale degree) per bar
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("music.theme")

PITCH_CLASSES = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
    "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10,
    "B": 11,
}


class ThemeError(ValueError):
    """A theme file/dict is malformed. User-facing: the message names the
    offending field so a campaign author can fix the YAML."""


@dataclass(frozen=True)
class MotifNote:
    degree: int          # 1-based scale degree; 0 = rest; may exceed 7
    beats: float         # duration at motif_rate 1.0
    octave: int = 0      # extra octave shift for this note


@dataclass(frozen=True)
class Theme:
    name: str
    tonic_pc: int
    base_octave: int
    beats_per_bar: int
    seed: int
    motif: tuple
    progression: tuple
    description: str = ""
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def motif_beats(self):
        return sum(n.beats for n in self.motif)


def _parse_note(raw, index):
    if not isinstance(raw, (list, tuple)) or len(raw) not in (2, 3):
        raise ThemeError(f"motif[{index}] must be [degree, beats] or "
                         f"[degree, beats, octave], got {raw!r}")
    try:
        degree = int(raw[0])
        beats = float(raw[1])
        octave = int(raw[2]) if len(raw) == 3 else 0
    except (TypeError, ValueError) as exc:
        raise ThemeError(f"motif[{index}] has a non-numeric value: {raw!r}") from exc
    if beats <= 0:
        raise ThemeError(f"motif[{index}] beats must be > 0, got {beats}")
    if not -14 <= degree <= 21:
        raise ThemeError(f"motif[{index}] degree {degree} out of range -14..21")
    return MotifNote(degree=degree, beats=beats, octave=octave)


def theme_from_dict(data):
    """Validate and build a Theme. Raises ThemeError naming the bad field."""
    log.debug("theme_from_dict keys=%s", sorted(data) if isinstance(data, dict) else type(data))
    if not isinstance(data, dict):
        raise ThemeError("theme must be a mapping")
    name = str(data.get("name") or "").strip()
    if not name:
        raise ThemeError("theme.name is required")

    tonic = str(data.get("tonic", "C")).strip().upper()
    if tonic not in PITCH_CLASSES:
        raise ThemeError(f"theme.tonic {data.get('tonic')!r} is not a note name")

    try:
        base_octave = int(data.get("base_octave", 3))
        beats_per_bar = int(data.get("beats_per_bar", 4))
        seed = int(data.get("seed", 0))
    except (TypeError, ValueError) as exc:
        raise ThemeError(f"theme numeric field invalid: {exc}") from exc
    if not 1 <= base_octave <= 5:
        raise ThemeError(f"theme.base_octave must be 1..5, got {base_octave}")
    if not 2 <= beats_per_bar <= 7:
        raise ThemeError(f"theme.beats_per_bar must be 2..7, got {beats_per_bar}")

    motif_raw = data.get("motif")
    if not isinstance(motif_raw, list) or not motif_raw:
        raise ThemeError("theme.motif must be a non-empty list")
    motif = tuple(_parse_note(n, i) for i, n in enumerate(motif_raw))
    if all(n.degree == 0 for n in motif):
        raise ThemeError("theme.motif is all rests")

    prog_raw = data.get("progression")
    if not isinstance(prog_raw, list) or not prog_raw:
        raise ThemeError("theme.progression must be a non-empty list of degrees")
    try:
        progression = tuple(int(d) for d in prog_raw)
    except (TypeError, ValueError) as exc:
        raise ThemeError(f"theme.progression must be integers: {prog_raw!r}") from exc
    if any(not 1 <= d <= 7 for d in progression):
        raise ThemeError(f"theme.progression degrees must be 1..7: {prog_raw!r}")

    known = {"name", "tonic", "base_octave", "beats_per_bar", "seed", "motif",
             "progression", "description"}
    return Theme(
        name=name, tonic_pc=PITCH_CLASSES[tonic], base_octave=base_octave,
        beats_per_bar=beats_per_bar, seed=seed, motif=motif,
        progression=progression,
        description=str(data.get("description") or ""),
        extra={k: v for k, v in data.items() if k not in known},
    )


def theme_to_dict(theme):
    """JSON-safe dict (for Postgres) that round-trips through theme_from_dict."""
    names = {v: k for k, v in PITCH_CLASSES.items() if len(k) == 1 or k.endswith("#")}
    return {
        "name": theme.name,
        "tonic": names[theme.tonic_pc].capitalize(),
        "base_octave": theme.base_octave,
        "beats_per_bar": theme.beats_per_bar,
        "seed": theme.seed,
        "motif": [[n.degree, n.beats, n.octave] for n in theme.motif],
        "progression": list(theme.progression),
        "description": theme.description,
        **theme.extra,
    }


def load_theme(path):
    """Load and validate a theme YAML file. Raises ThemeError / OSError."""
    path = Path(path)
    log.debug("load_theme path=%s", path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        log.error("load_theme yaml_error path=%s err=%s", path, exc)
        raise ThemeError(f"{path}: invalid YAML: {exc}") from exc
    theme = theme_from_dict(data)
    log.info("theme loaded name=%s path=%s motif_notes=%d bars=%d",
             theme.name, path, len(theme.motif), len(theme.progression))
    return theme
