#!/usr/bin/env python3
"""
gaze.py
Who-looks-at-whom on the roundtable, and how the head turns to get there.

THE BEHAVIOUR (design ask):
  * while a character is speaking, EVERY other tile's head turns toward the
    speaker's tile;
  * the speaker looks at whoever it is talking TO — one addressee: it holds
    on that tile; several (or "everyone"): it sweeps slowly from one listener
    to the next across the length of its line;
  * all of it is anchored to the moment the voice line actually starts
    playing, so text, audio and head motion move together.

THE IPC. One small JSON "stage" file per roundtable container
(<relay-dir>/stage.json), written by the ONE tile process that is about to
play a voice line, at the instant its audio starts (tile_pane's
make_stage_writer, called from replay.Performer's on_voice_start hook —
i.e. AFTER the voice gate has granted it the seat, so a line that is waiting
its turn never steals everyone's attention early). Every tile's head driver
reads it each frame and derives its own gaze from it. Same "a state file is
the whole IPC" convention agent_state.py already uses.

WHY PURE. No display, no GL, no numpy: geometry + timing only, so it is
unit-testable on any box. The head renderer just receives two angles.

ANGLE CONVENTIONS (checked against pixel_raster.make_view_matrix): positive
yaw turns the face toward screen RIGHT; positive pitch tips it DOWN.
"""
import json
import math
import os
import re
import time

#: The roundtable grid (config/layouts/roundtable.yaml): an even 4x2 grid,
#: slot tuber_N at column N % 4, row N // 4. Keep in sync with that file —
#: tests/test_gaze.py pins the mapping against it.
GRID_COLUMNS = 4

#: Tile size in pixels at the deployed 1920x1080 capture (480x540 tiles).
#: Only the RATIO matters for angles, so a different capture resolution with
#: the same grid still aims correctly.
TILE_W_PX = 480
TILE_H_PX = 540

#: Virtual "distance to the table" in pixels. Larger = subtler turns. At 700:
#: one tile over ~34deg, two ~54deg, three ~64deg (clamped to MAX_YAW).
GAZE_DEPTH_PX = 700.0

#: Hard limits: past ~60deg the codec head is a pure profile and stops
#: reading as a face; pitch much past 20deg hides the eyes under the brow.
MAX_YAW_RAD = math.radians(60.0)
MAX_PITCH_RAD = math.radians(20.0)
#: Looking up/down across rows is dialled down relative to yaw — a full
#: atan pitch at the same depth reads as nodding, not looking.
PITCH_SCALE = 0.6

#: Resting pose when nobody is talking — the fixed 15deg profile
#: codec_avatar.FrameSource has always used (angle_deg default).
REST_YAW_RAD = math.radians(15.0)
REST_PITCH_RAD = 0.0

#: How long gazes linger on the last speaker after its line ends before
#: heads drift back to rest. Lines in a show are back-to-back, so in
#: practice the next line re-aims everyone well before this runs out.
STAGE_LINGER_S = 6.0

#: Sweeping speaker: minimum time spent on each listener. A short line
#: visits fewer listeners rather than whipping between them.
MIN_SWEEP_DWELL_S = 1.4

#: Head-turn smoothing: exponential approach with this time constant, and a
#: cap on angular speed, so turns are deliberate rather than snapping.
TURN_TIME_CONSTANT_S = 0.35
MAX_TURN_RAD_PER_S = math.radians(150.0)

ALL = "all"

# Words that mean "the whole table" when found in a line.
_EVERYONE_RE = re.compile(
    r"\b(everyone|everybody|all of you|you all|y'all|team|party|folks|gang|"
    r"adventurers|heroes|guys)\b", re.IGNORECASE)


# ── geometry ─────────────────────────────────────────────────────────────────
def slot_index(slot):
    """tuber_N -> N, or None for anything that is not a slot id."""
    match = re.fullmatch(r"tuber_(\d+)", str(slot or ""))
    return int(match.group(1)) if match else None


def slot_grid_position(slot, columns=GRID_COLUMNS):
    """(col, row) of `slot`'s tile, or None."""
    index = slot_index(slot)
    if index is None:
        return None
    return index % columns, index // columns


def gaze_angles(from_slot, to_slot, columns=GRID_COLUMNS):
    """(yaw, pitch) radians for `from_slot`'s head to face `to_slot`'s tile.
    Rest pose when either is unknown or they are the same tile."""
    a = slot_grid_position(from_slot, columns)
    b = slot_grid_position(to_slot, columns)
    if a is None or b is None or a == b:
        return REST_YAW_RAD, REST_PITCH_RAD
    dx = (b[0] - a[0]) * TILE_W_PX
    dy = (b[1] - a[1]) * TILE_H_PX
    yaw = math.atan2(dx, GAZE_DEPTH_PX)
    pitch = math.atan2(dy, GAZE_DEPTH_PX) * PITCH_SCALE
    return (max(-MAX_YAW_RAD, min(MAX_YAW_RAD, yaw)),
            max(-MAX_PITCH_RAD, min(MAX_PITCH_RAD, pitch)))


def sweep_order(from_slot, targets, columns=GRID_COLUMNS):
    """Listeners in reading order across the table (row, then column) so a
    speaker addressing the room pans smoothly instead of zig-zagging."""
    def key(slot):
        pos = slot_grid_position(slot, columns)
        return (pos[1], pos[0]) if pos else (99, 99)
    return sorted({t for t in targets if t and t != from_slot}, key=key)


# ── who is this line addressed to ────────────────────────────────────────────
def _as_target_list(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        return ALL if value.lower() in (ALL, "everyone", "table") else [value]
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if str(v or "").strip()]
        return items or None
    return None


def explicit_addressees(scene):
    """An episode may say who a line is for: `addressee` (or `to`) on any of
    the scene's events — a slot id / speaker id, a list of them, or "all".
    Returns ALL, a list, or None when the episode doesn't say."""
    for event in (scene or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        for key in ("addressee", "addressees", "to"):
            found = _as_target_list(event.get(key))
            if found is not None:
                return found
    return None


def mentioned_slots(text, names_by_slot, exclude=None):
    """Slots whose display name appears as a whole word in `text`, in the
    order they are first mentioned."""
    hits = []
    for slot, name in (names_by_slot or {}).items():
        if slot == exclude or not name:
            continue
        match = re.search(r"(?<!\w)" + re.escape(str(name)) + r"(?!\w)",
                          text or "", re.IGNORECASE)
        if match:
            hits.append((match.start(), slot))
    return [slot for _pos, slot in sorted(hits)]


def resolve_addressees(scene, speaker_slot, cast, participants, names_by_slot,
                       previous_speaker=None, gm_slot="tuber_0"):
    """The slots `speaker_slot` should look at while voicing `scene`.

    Precedence, first hit wins:
      1. explicit `addressee`/`to` on the scene's events (speaker ids are
         mapped through `cast`, slot ids pass straight through);
      2. names of other characters spoken in the line ("Max, check the door");
      3. "everyone"-style words in the line -> the whole table;
      4. the GM with nobody named is addressing the table;
      5. anyone else is answering whoever spoke last (or, failing that,
         the GM).
    Always returns a non-empty list when there is anyone to look at, and
    never includes the speaker itself.
    """
    cast = cast or {}
    others = [s for s in participants if s != speaker_slot]
    if not others:
        return []

    def to_slots(items):
        slots = []
        for item in items:
            slot = item if slot_index(item) is not None else cast.get(item)
            if slot and slot != speaker_slot and slot not in slots:
                slots.append(slot)
        return slots

    explicit = explicit_addressees(scene)
    if explicit == ALL:
        return others
    if explicit:
        slots = to_slots(explicit)
        if slots:
            return slots

    text = " ".join(
        [str((scene or {}).get("narration") or "")]
        + [str(e.get("text") or "") for e in (scene or {}).get("events") or []
           if isinstance(e, dict)])
    named = [s for s in mentioned_slots(text, names_by_slot, exclude=speaker_slot)
             if s in others]
    if named:
        return named
    if _EVERYONE_RE.search(text):
        return others
    if speaker_slot == gm_slot:
        return others
    if previous_speaker in others:
        return [previous_speaker]
    if gm_slot in others:
        return [gm_slot]
    return others


# ── the stage file ───────────────────────────────────────────────────────────
def write_stage(path, speaker_slot, addressees, duration_s, started_at=None,
                envelope=None, envelope_rate_hz=None, previous_speaker=None,
                airing_id=None):
    """Atomically publish "who is speaking to whom, since when, for how long".
    Unique tmp name per process: two tiles may overlap under a
    max_concurrent>1 voice gate, and a shared tmp name would interleave."""
    state = {
        "speaker": speaker_slot,
        "addressees": list(addressees or []),
        "started_at": time.time() if started_at is None else float(started_at),
        "duration": max(0.0, float(duration_s or 0.0)),
        "envelope": envelope or None,
        "envelope_rate_hz": envelope_rate_hz,
        "previous_speaker": previous_speaker,
        "airing_id": airing_id,
    }
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)
    return state


def read_stage(path):
    """The stage dict, or None when missing/unreadable (-> rest pose)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def stage_is_live(stage, now):
    """True while a line is playing or within STAGE_LINGER_S after it."""
    if not stage or not stage.get("speaker"):
        return False
    try:
        started = float(stage.get("started_at"))
        duration = float(stage.get("duration") or 0.0)
    except (TypeError, ValueError):
        return False
    return started - 1.0 <= now <= started + duration + STAGE_LINGER_S


def gaze_target(self_slot, stage, now):
    """Which slot `self_slot`'s head should face right now, or None (rest).

    Listener -> the speaker. Speaker -> its addressee, or, with several, the
    one due at this point in the line (evenly spread over the line's length,
    each held at least MIN_SWEEP_DWELL_S)."""
    if not stage_is_live(stage, now):
        return None
    speaker = stage.get("speaker")
    if self_slot != speaker:
        return speaker
    targets = sweep_order(self_slot, stage.get("addressees") or [])
    if not targets:
        return None
    if len(targets) == 1:
        return targets[0]
    duration = float(stage.get("duration") or 0.0)
    dwell = max(MIN_SWEEP_DWELL_S, duration / len(targets))
    elapsed = max(0.0, now - float(stage.get("started_at")))
    # Hold the last listener once the line is over rather than wrapping.
    index = min(int(elapsed / dwell), len(targets) - 1)
    return targets[index]


def mouth_open(self_slot, stage, now):
    """0..1 mouth openness from the line's audio envelope — only for the
    tile that is speaking, and only while its audio is actually playing."""
    if not stage or stage.get("speaker") != self_slot:
        return 0.0
    envelope = stage.get("envelope")
    rate = stage.get("envelope_rate_hz") or 0
    try:
        elapsed = now - float(stage.get("started_at"))
    except (TypeError, ValueError):
        return 0.0
    if not envelope or rate <= 0 or elapsed < 0:
        # No envelope (TTS-less line): a gentle heuristic flap for the
        # line's duration so the speaker still visibly talks.
        duration = float(stage.get("duration") or 0.0)
        if 0 <= elapsed <= duration:
            return 0.5 + 0.5 * math.sin(elapsed * 4.5 * 2 * math.pi)
        return 0.0
    index = int(elapsed * rate)
    if index >= len(envelope):
        return 0.0
    try:
        return max(0.0, min(1.0, float(envelope[index])))
    except (TypeError, ValueError):
        return 0.0


# ── smooth head turning ──────────────────────────────────────────────────────
class GazeController:
    """Eases the head's (yaw, pitch) toward a target each frame — first-order
    exponential approach capped at MAX_TURN_RAD_PER_S — so a change of
    speaker reads as a deliberate head turn, never a snap."""

    def __init__(self, yaw=REST_YAW_RAD, pitch=REST_PITCH_RAD,
                 time_constant_s=TURN_TIME_CONSTANT_S,
                 max_speed=MAX_TURN_RAD_PER_S):
        self.yaw = yaw
        self.pitch = pitch
        self.time_constant_s = max(1e-3, time_constant_s)
        self.max_speed = max_speed
        self._last = None

    def _approach(self, current, target, dt):
        alpha = 1.0 - math.exp(-dt / self.time_constant_s)
        step = (target - current) * alpha
        limit = self.max_speed * dt
        return current + max(-limit, min(limit, step))

    def step(self, target_yaw, target_pitch, now=None):
        now = time.monotonic() if now is None else now
        dt = 0.0 if self._last is None else max(0.0, min(0.5, now - self._last))
        self._last = now
        self.yaw = self._approach(self.yaw, target_yaw, dt)
        self.pitch = self._approach(self.pitch, target_pitch, dt)
        return self.yaw, self.pitch


class StageGaze:
    """Per-tile glue: read the stage file, pick a target, ease toward it.
    `sample()` returns (gaze=(yaw, pitch), mouth_open). Never raises."""

    #: Re-read the stage file at most this often (8 tiles x 12fps would
    #: otherwise be ~100 small reads a second for nothing).
    READ_INTERVAL_S = 0.08

    def __init__(self, slot, stage_path, controller=None, clock=time.time,
                 mono=time.monotonic):
        self.slot = slot
        self.stage_path = stage_path
        self.controller = controller or GazeController()
        self._clock = clock
        self._mono = mono
        self._stage = None
        self._read_at = None

    def _refresh(self):
        mono = self._mono()
        if self._read_at is None or mono - self._read_at >= self.READ_INTERVAL_S:
            self._read_at = mono
            self._stage = read_stage(self.stage_path) if self.stage_path else None
        return self._stage

    def sample(self):
        try:
            stage = self._refresh()
            now = self._clock()
            target = gaze_target(self.slot, stage, now)
            if target is None:
                yaw, pitch = REST_YAW_RAD, REST_PITCH_RAD
            else:
                yaw, pitch = gaze_angles(self.slot, target)
            gaze = self.controller.step(yaw, pitch, now=self._mono())
            return gaze, mouth_open(self.slot, stage, now)
        except Exception:  # noqa: BLE001 — a head must never take a tile down
            return (self.controller.yaw, self.controller.pitch), 0.0


__all__ = [
    "ALL", "GRID_COLUMNS", "REST_YAW_RAD", "REST_PITCH_RAD", "STAGE_LINGER_S",
    "MIN_SWEEP_DWELL_S", "slot_index", "slot_grid_position", "gaze_angles",
    "sweep_order", "explicit_addressees", "mentioned_slots",
    "resolve_addressees", "write_stage", "read_stage", "stage_is_live",
    "gaze_target", "mouth_open", "GazeController", "StageGaze",
]
