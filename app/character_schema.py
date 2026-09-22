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


#: Named starting points. `chadwick` is the flagship character (roster
#: tuber_1 / the coder worker, see config/workers/tuber_0.yaml's `roster:`)
#: and is the one wired live in config/workers/coder.yaml — a wide, square,
#: heavy-jawed head with big ears and a short nose, so its silhouette is
#: distinguishable from the other 7 slots at 55x24.
#:
#: The rest (nyx1/oko2/ada3/tess3/max1) are the other 5 `tuber_base`
#: workers, moved onto the codec_avatar renderer alongside Chadwick
#: (docs/character_generator.md, 2026-09-22). Quick pass: default-shaped
#: sliders (PARAM_DEFAULTS) with a distinct accent_color per character so
#: they're visually distinguishable on stream immediately; unique
#: silhouettes (like chadwick's) are follow-up work, not done here.
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
    "nyx1": {**SLIDER_DEFAULTS, "accent_color": "CYAN"},     # coder-native
    "oko2": {**SLIDER_DEFAULTS, "accent_color": "GREEN"},    # coder-opencode
    "ada3": {**SLIDER_DEFAULTS, "accent_color": "PURPLE"},   # coder-aider
    "tess3": {**SLIDER_DEFAULTS, "accent_color": "RED"},     # tester
    "max1": {**SLIDER_DEFAULTS, "accent_color": "WHITE"},    # manager
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
