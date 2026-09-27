"""
mood_map.py — GEMS scene mood -> concrete musical parameters.

Why these parameters: the emotion-in-music literature converges on a small
set of structural cues (Gabrielsson & Lindström 2001/2010 review of 100+
studies; Juslin & Lindström 2010):

  * AROUSAL (calm <-> energetic): tempo (the single strongest cue),
    loudness, articulation (legato <-> staccato), pitch register, timbral
    brightness, rhythmic density.
  * VALENCE (sad <-> happy): mode and harmonic complexity. CMERS
    (Livingstone et al. 2010, Computer Music Journal 34(1)) showed that
    performance-only changes move arousal but NOT valence — score-level
    changes (mode, harmony) are needed for that. So every mood here sets a
    mode, not just a tempo.

Moods use the GEMS vocabulary already on campaign scenes
(app/campaign/pack.py MOODS), placed on Russell's valence/arousal plane
(Eerola & Vuoskoski 2011: two dimensions capture most perceived music
emotion). Numbers are a first, by-ear tuning — adjust freely.
"""
import logging
from dataclasses import asdict, dataclass, fields, replace

log = logging.getLogger("music.mood_map")

#: Scale intervals (semitones from tonic). 7-note modes only, so a motif's
#: scale degrees map 1:1 in every mood.
MODES = {
    "lydian": (0, 2, 4, 6, 7, 9, 11),       # brightest — wonder
    "ionian": (0, 2, 4, 5, 7, 9, 11),       # major
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),   # heroic/bold major
    "dorian": (0, 2, 3, 5, 7, 9, 10),       # bittersweet minor
    "aeolian": (0, 2, 3, 5, 7, 8, 10),      # natural minor — sadness
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),     # darkest common mode — dread
}

#: Layer names, in mix order.
LAYERS = ("pad", "drone", "bass", "arp", "lead", "pulse")


@dataclass(frozen=True)
class MoodParams:
    mood: str
    valence: float        # -1..1
    arousal: float        # -1..1
    mode: str             # key into MODES
    tempo_bpm: float
    transpose: int        # semitones applied to the whole key
    brightness: float     # 0..1 — harmonic richness / filter openness
    density: float        # 0..1 — probability an arp step sounds
    arp_rate: int         # arp steps per beat (1, 2, 4)
    motif_rate: float     # 0.5 = augmentation (slower motif), 2 = diminution
    gate: float           # 0..1 — note length fraction (legato 1, staccato ~0.4)
    chord_ext: int        # 0 triad, 1 seventh, 2 add9, 3 dissonant cluster
    reverb: float         # 0..1 send
    pad: float
    drone: float
    bass: float
    arp: float
    lead: float
    pulse: float

    def to_dict(self):
        return asdict(self)


def _p(mood, v, a, mode, bpm, tr, bright, dens, arp_rate, motif_rate, gate,
       ext, rev, pad, drone, bass, arp, lead, pulse):
    return MoodParams(mood, v, a, mode, bpm, tr, bright, dens, arp_rate,
                      motif_rate, gate, ext, rev, pad, drone, bass, arp, lead, pulse)


#:                 mood                V     A     mode             bpm  tr  brt  dens arp mot  gate ext rev  pad  drn  bass arp  lead pulse
_TABLE = {m.mood: m for m in (
    _p("neutral",            0.0,  0.0, "dorian",          80,  0, .50, .35, 2, 1.0, .80, 1, .50, .80, .10, .50, .40, .50, .15),
    _p("wonder",             0.5,  0.3, "lydian",          84,  2, .75, .45, 2, 1.0, .80, 2, .60, .80, .20, .40, .60, .70, .00),
    _p("transcendence",      0.6, -0.2, "ionian",          66,  5, .60, .25, 1, 0.5, 1.0, 2, .80, 1.0, .40, .30, .35, .60, .00),
    _p("tenderness",         0.6, -0.5, "ionian",          72,  0, .40, .30, 2, 0.5, .90, 1, .50, .80, .00, .40, .45, .60, .00),
    _p("nostalgia",          0.1, -0.4, "dorian",          70, -2, .35, .30, 2, 0.5, .90, 1, .60, .80, .20, .40, .40, .60, .00),
    _p("peacefulness",       0.4, -0.7, "ionian",          60,  0, .30, .15, 1, 0.5, 1.0, 2, .70, 1.0, .30, .30, .20, .35, .00),
    _p("power",              0.3,  0.8, "mixolydian",     116, -5, .85, .70, 4, 1.0, .55, 0, .35, .70, .30, .90, .50, .80, .80),
    _p("joyful_activation",  0.8,  0.7, "ionian",         124,  4, .90, .75, 4, 1.0, .45, 1, .35, .60, .00, .70, .80, .80, .70),
    _p("tension",           -0.4,  0.6, "phrygian",       100, -3, .55, .60, 4, 1.0, .40, 3, .45, .60, .70, .80, .60, .50, .50),
    _p("sadness",           -0.7, -0.6, "aeolian",         58, -3, .25, .20, 1, 0.5, 1.0, 1, .70, .90, .20, .45, .30, .70, .00),
)}

#: Every mood the engine accepts. GEMS 9 (== campaign.pack.MOODS) + neutral.
MOODS = tuple(_TABLE)

#: Ekman-6 facial emotions (app/emotion.py) -> closest GEMS mood, so a
#: character's per-line emotion can drive the score if no scene mood exists.
EMOTION_TO_MOOD = {
    "neutral": "neutral", "happy": "joyful_activation", "sad": "sadness",
    "angry": "power", "afraid": "tension", "surprised": "wonder",
    "disgusted": "tension",
}

_DISCRETE = {"mood", "mode", "arp_rate", "chord_ext", "motif_rate", "transpose"}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def normalize_mood(value):
    """Coerce to a MOODS member; unknown -> 'neutral' (never raises — mood
    strings come from YAML, Redis and LLM replies)."""
    if isinstance(value, str) and value.strip().lower() in _TABLE:
        return value.strip().lower()
    if value not in (None, ""):
        log.debug("normalize_mood unknown=%r -> neutral", value)
    return "neutral"


def mood_for_emotion(emotion):
    return EMOTION_TO_MOOD.get(str(emotion or "").strip().lower(), "neutral")


def apply_intensity(params, intensity):
    """Scale a mood's energy without changing its identity. intensity 0.5 is
    the table value; 0 is sparse and gentle, 1 is full and driving."""
    f = _clamp(float(intensity)) - 0.5
    return replace(
        params,
        tempo_bpm=params.tempo_bpm * (1 + 0.12 * f),
        brightness=_clamp(params.brightness + 0.25 * f),
        density=_clamp(params.density * (1 + f)),
        arp=_clamp(params.arp * (1 + f)),
        pulse=_clamp(params.pulse * (1 + 1.2 * f)),
        lead=_clamp(params.lead * (1 + 0.5 * f)),
        drone=_clamp(params.drone * (1 - 0.5 * f)),
    )


def mood_params(mood, intensity=0.5):
    """The MoodParams for `mood` (normalized) at `intensity`."""
    return apply_intensity(_TABLE[normalize_mood(mood)], intensity)


def interpolate(a, b, t):
    """Blend two MoodParams. Continuous fields lerp; discrete ones (mode,
    rates, chord extension, key) switch to `b` as soon as t > 0 — the engine
    calls this on bar boundaries, so a mode change always lands on a
    downbeat while tempo/brightness/levels glide over the transition."""
    t = _clamp(t)
    if t <= 0:
        return a
    out = {}
    for f in fields(MoodParams):
        va, vb = getattr(a, f.name), getattr(b, f.name)
        out[f.name] = vb if f.name in _DISCRETE else va + (vb - va) * t
    return MoodParams(**out)


def blend_moods(moods, intensity=0.5):
    """A scene may carry several GEMS moods; average their continuous
    parameters, taking discrete ones (mode etc.) from the first."""
    valid = [normalize_mood(m) for m in (moods or [])]
    valid = [m for m in valid if m != "neutral"] or ["neutral"]
    result = mood_params(valid[0], intensity)
    for i, m in enumerate(valid[1:], start=2):
        result = interpolate(result, mood_params(m, intensity), 1.0 / i)
        result = replace(result, mood=valid[0], mode=mood_params(valid[0]).mode,
                         arp_rate=mood_params(valid[0]).arp_rate,
                         chord_ext=mood_params(valid[0]).chord_ext,
                         motif_rate=mood_params(valid[0]).motif_rate,
                         transpose=mood_params(valid[0]).transpose)
    log.debug("blend_moods moods=%s -> %s", moods, valid)
    return result
