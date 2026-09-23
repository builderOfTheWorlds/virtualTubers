#!/usr/bin/env python3
"""
character_schema.py
The flat 0..1 slider schema an AI agent edits to generate a character's
head — the ONLY surface an agent touches (see docs/avatar_3d_design.md §3).

Deliberately dependency-free (no numpy, no termgl): this module is pure
validation/normalization so it can be imported by the agent-facing preview
tool, by tests, and by the avatar pane process alike. Geometry lives in
head_mesh.py; the mapping from a slider value to an actual scale/offset is
that module's business, not this one's.

Design rule (docs/avatar_3d_design.md §3): every parameter must stay
backend-generic — a value here describes an *appearance* ("how wide is the
jaw") and never a backend's internal transform, so the same dict can drive
the procedural numpy builder (Backend A) or Blender shape keys (Backend B).
"""
import logging

log = logging.getLogger(__name__)
TRACE = 5


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


# termgl's named 8-color palette. Duplicated here rather than imported from
# render3d_common._ANSI_COLOR on purpose: that module imports termgl, which
# only exists under the /opt/render3d Python 3.11 venv, and this module must
# stay importable everywhere (tests, tooling, the system python3.10 app).
ACCENT_COLORS = ("BLACK", "RED", "GREEN", "YELLOW", "BLUE", "PURPLE", "CYAN", "WHITE")

#: slider name -> default. Every one of these is a 0..1 float; out-of-range
#: values are CLAMPED, never rejected, because an agent iterating on sliders
#: will overshoot and a hard failure would stall its loop mid-iteration.
SLIDER_DEFAULTS = {
    "head_width": 0.5,
    "head_taper": 0.3,
    "eye_size": 0.5,
    "eye_spacing": 0.5,
    "jaw_width": 0.5,
    "nose_length": 0.4,
    "ear_size": 0.5,
    "build": 0.5,
}

#: Non-slider (enum) parameters.
ENUM_DEFAULTS = {
    "accent_color": "YELLOW",
}

PARAM_DEFAULTS = {**SLIDER_DEFAULTS, **ENUM_DEFAULTS}


class CharacterParamError(ValueError):
    """Raised only for structurally invalid input (not a dict, unknown key,
    non-numeric slider, unknown accent color) — never for a merely
    out-of-range slider, which is clamped instead."""


def normalize_params(raw, strict=True):
    """Return a complete, clamped params dict from a partial/raw one.

    Missing keys take their default, so an agent can send `{"eye_size": 0.9}`
    and get a full character back. Slider values are clamped to 0..1.
    `strict=False` drops unknown keys with a warning instead of raising —
    used on the config-read path so a typo'd YAML key degrades the face
    rather than killing the avatar pane process (matching this project's
    "a pane must never crash the container" rule, see
    avatar_providers/__init__.py).
    """
    _trace("normalize_params(raw=%r, strict=%s)", raw, strict)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise CharacterParamError(f"character params must be a dict, got {type(raw).__name__}")

    params = dict(PARAM_DEFAULTS)

    for key, value in raw.items():
        if key not in PARAM_DEFAULTS:
            if strict:
                raise CharacterParamError(
                    f"unknown character parameter {key!r} "
                    f"(known: {sorted(PARAM_DEFAULTS)})"
                )
            log.warning("ignoring unknown character parameter %r", key)
            continue

        if key in ENUM_DEFAULTS:
            params[key] = _normalize_accent_color(key, value, strict)
        else:
            params[key] = _normalize_slider(key, value, strict)

    _trace("normalize_params -> %r", params)
    return params


def _normalize_slider(key, value, strict):
    """Coerce to float and clamp to 0..1."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        if strict:
            raise CharacterParamError(f"slider {key!r} must be a number, got {value!r}")
        log.warning("slider %r has non-numeric value %r — using default", key, value)
        return SLIDER_DEFAULTS[key]

    if number != number:  # NaN: float('nan') != itself. min/max would propagate it.
        if strict:
            raise CharacterParamError(f"slider {key!r} must be a real number, got NaN")
        log.warning("slider %r is NaN — using default", key)
        return SLIDER_DEFAULTS[key]

    clamped = max(0.0, min(1.0, number))
    if clamped != number:
        log.debug("slider %r clamped %s -> %s", key, number, clamped)
    return clamped


def _normalize_accent_color(key, value, strict):
    """Uppercase and validate against termgl's named palette."""
    name = str(value).upper()
    if name not in ACCENT_COLORS:
        if strict:
            raise CharacterParamError(
                f"{key!r} must be one of {list(ACCENT_COLORS)}, got {value!r}"
            )
        log.warning("%r has unknown color %r — using default", key, value)
        return ENUM_DEFAULTS[key]
    return name


#: Named starting points — one per roster slot, each a DISTINCT SILHOUETTE.
#:
#: The cast is identified by SHAPE first. Heads are seen small on stream (a
#: ~200-550px tall pane), where tint survives downscaling but fine facial
#: detail does not, so every preset is pushed to a different corner of the
#: slider space rather than nudged a few hundredths off the defaults. The
#: three sliders that dominate the outline at that size carry the load:
#:
#:   head_width  — overall skull mass, narrow spike vs. broad slab
#:   head_taper  — cranium narrowing above the brow, column vs. cone
#:   jaw_width   — flare below the cheekbone, pointed chin vs. heavy jaw
#:
#: eye_size / eye_spacing / nose_length / ear_size then separate faces that
#: happen to share an outline (e.g. chadwick and max1 are both broad, but
#: max1 is squarer, wider-set and short-nosed). `build` describes body mass
#: for renderers that draw shoulders; the codec head itself ignores it.
#:
#: accent_color is now a SECONDARY cue: it reinforces an identity the shape
#: already establishes, and the cast stays readable in a greyscale capture
#: or for a colour-blind viewer. Each character owns one palette entry, and
#: all eight entries of ACCENT_COLORS are spoken for.
#:
#: `chadwick` is the flagship (roster tuber_1 / the coder worker, see
#: config/workers/tuber_0.yaml's `roster:`) and the one wired live in
#: config/workers/coder.yaml; leave its numbers alone — other docs and the
#: design notes in docs/character_generator.md reference them.
PRESETS = {
    "chadwick": {
        "head_width": 0.72,
        "head_taper": 0.18,
        "eye_size": 0.62,
        "eye_spacing": 0.58,
        "jaw_width": 0.78,
        "nose_length": 0.35,
        "ear_size": 0.68,
        "build": 0.70,
        "accent_color": "YELLOW",
    },
    # NYX-1 — the narrowest, most steeply tapered skull on the roster: an
    # inverted teardrop that runs from a pinched crown to a small pointed
    # jaw. Oversized, maximally wide-set eyes and almost no ears, so it
    # stays a smooth thin outline with nothing sticking out.
    "nyx1": {
        "head_width": 0.00,
        "head_taper": 1.00,
        "eye_size": 0.95,
        "eye_spacing": 1.00,
        "jaw_width": 0.20,
        "nose_length": 0.28,
        "ear_size": 0.02,
        "build": 0.20,
        "accent_color": "CYAN",
    },
    # OKO-2 — broad and completely untapered: a wide dome that stays wide
    # all the way to a narrow chin. The biggest ears and a near-maximum
    # nose give it the strongest profile silhouette, while tiny close-set
    # eyes keep the front of the face sparse.
    "oko2": {
        "head_width": 0.88,
        "head_taper": 0.00,
        "eye_size": 0.20,
        "eye_spacing": 0.06,
        "jaw_width": 0.30,
        "nose_length": 0.95,
        "ear_size": 1.00,
        "build": 0.60,
        "accent_color": "GREEN",
    },
    # ADA-3 — a straight-sided narrow column with the sharpest chin
    # (jaw_width 0): no flare anywhere below the cheekbone. Deliberately
    # featureless — the smallest eyes and shortest nose — so it reads as a
    # blank wedge next to the more sculpted faces.
    "ada3": {
        "head_width": 0.40,
        "head_taper": 0.05,
        "eye_size": 0.08,
        "eye_spacing": 0.14,
        "jaw_width": 0.00,
        "nose_length": 0.05,
        "ear_size": 0.24,
        "build": 0.34,
        "accent_color": "PURPLE",
    },
    # TESS-3 — the diamond: a fully tapered cranium narrowing to a point
    # above the brow, sitting over the widest possible jaw flare. Huge eyes
    # and big ears. Top-heavy skull over a bottom-heavy face, the opposite
    # weight distribution from nyx1.
    "tess3": {
        "head_width": 0.42,
        "head_taper": 1.00,
        "eye_size": 1.00,
        "eye_spacing": 0.34,
        "jaw_width": 1.00,
        "nose_length": 0.55,
        "ear_size": 0.86,
        "build": 0.45,
        "accent_color": "RED",
    },
    # MAX-1 — the slab: maximum width AND maximum jaw with zero taper, the
    # largest overall mass. A stub nose, small ears and the widest-set eyes
    # keep the face flat and blocky, distinct from chadwick's narrower,
    # slightly tapered wedge.
    "max1": {
        "head_width": 1.00,
        "head_taper": 0.00,
        "eye_size": 0.34,
        "eye_spacing": 0.92,
        "jaw_width": 1.00,
        "nose_length": 0.14,
        "ear_size": 0.08,
        "build": 0.95,
        "accent_color": "WHITE",
    },
    # gm0 — the Game Master / host. Nearly the narrowest skull, only
    # lightly tapered, but carrying the longest nose and largest ears on
    # the roster: a lean beaked head whose identity lives in profile, which
    # suits a narrator who is usually shown side-on to the cast.
    "gm0": {
        "head_width": 0.12,
        "head_taper": 0.35,
        "eye_size": 0.66,
        "eye_spacing": 0.44,
        "jaw_width": 0.62,
        "nose_length": 1.00,
        "ear_size": 1.00,
        "build": 0.15,
        "accent_color": "BLUE",
    },
    # iris7 — the wedge: a broad untapered crown running down to the second
    # narrowest jaw, so the outline is wide at the top and pointed at the
    # bottom — the inverse of tess3. Mid-size eyes set wide, a long nose and
    # mid ears place it between the extremes without duplicating any of them.
    "iris7": {
        "head_width": 0.72,
        "head_taper": 0.00,
        "eye_size": 0.44,
        "eye_spacing": 0.76,
        "jaw_width": 0.10,
        "nose_length": 0.70,
        "ear_size": 0.50,
        "build": 0.52,
        "accent_color": "BLACK",
    },
}


def load_preset(name):
    """Return a normalized copy of a named preset."""
    _trace("load_preset(name=%r)", name)
    if name not in PRESETS:
        raise CharacterParamError(f"unknown preset {name!r} (known: {sorted(PRESETS)})")
    return normalize_params(PRESETS[name])


def resolve_params(raw, strict=False):
    """Config-path entry point: accept a params dict, a preset NAME (string),
    or None, and return a full normalized params dict.

    Accepting a bare string means a worker config can say
    `character_params: chadwick` instead of restating nine sliders, while an
    agent iterating on a face still passes an explicit dict.
    """
    _trace("resolve_params(raw=%r, strict=%s)", raw, strict)
    if isinstance(raw, str):
        return load_preset(raw)
    if isinstance(raw, dict) and isinstance(raw.get("preset"), str):
        # {"preset": "chadwick", "eye_size": 0.9} — preset as a base, with
        # explicit sliders layered on top. Lets an agent iterate from a
        # known-good face instead of from the neutral defaults.
        overrides = {k: v for k, v in raw.items() if k != "preset"}
        base = load_preset(raw["preset"])
        # Normalize the overrides ALONE, then copy across only the keys the
        # caller actually supplied. Merging a full normalize_params(overrides)
        # would splat PARAM_DEFAULTS over every slider the caller left out,
        # silently discarding the preset it asked to start from.
        normalized = normalize_params(overrides, strict=strict)
        for key in overrides:
            if key in normalized:
                base[key] = normalized[key]
        return base
    return normalize_params(raw, strict=strict)
