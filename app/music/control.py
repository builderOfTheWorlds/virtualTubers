"""
control.py — where the music's mood comes from, and who wins.

The music module is STANDALONE: nothing in the show calls into it. Other
parts of the stack only PUBLISH mood; the director (music_director.py)
polls these sources every bar and resolves them:

  1. GM override — Redis key `music:{worker_id}:override`, JSON
       {"mood": "tension", "intensity": 0.7}   hold this mood
       {"mood": "silence"}                     fade the music out
       absent / {"mood": "follow"}             follow the scene
     Written by message-api (POST /music/{worker_id}) for the control panel,
     and later by an AI GM.
  2. Scene cue — a small JSON file (default /tmp/music/scene_cue.json)
       {"mood": ["tension"], "intensity": 0.5, "scene_id": "party-attack"}
     written by the roundtable director at each scene start.
  3. Otherwise hold whatever is playing.

Scene cues are subject to a minimum dwell time so a burst of short scenes
can't whiplash the score; a GM override always applies immediately.
"""
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from music.mood_map import normalize_mood

log = logging.getLogger("music.control")

SILENCE = "silence"
FOLLOW = "follow"
DEFAULT_CUE_PATH = "/tmp/music/scene_cue.json"


def override_key(worker_id):
    return f"music:{worker_id}:override"


def status_key(worker_id):
    return f"music:{worker_id}:status"


#: How long the director's now-playing status survives without a refresh —
#: an absent status means "no music director is running".
STATUS_TTL_S = 30


class MusicControl:
    """Redis contract shared by message-api (writer of overrides, reader of
    status) and music_director.py (reader of overrides, writer of status).

    Keys:
      music:{worker_id}:override  JSON {"mood", "intensity"}; absent = follow scene
      music:{worker_id}:status    JSON now-playing, TTL STATUS_TTL_S

    Reads fail open (None); writes RAISE so the API can tell the operator a
    change didn't take effect — same policy as console_theme.ConsoleThemeControl.
    """

    def __init__(self, client):
        self._client = client

    @classmethod
    def from_url(cls, redis_url, socket_timeout=2):
        import redis
        return cls(redis.Redis.from_url(redis_url, socket_timeout=socket_timeout,
                                        socket_connect_timeout=socket_timeout,
                                        decode_responses=True))

    @staticmethod
    def validate_mood(mood):
        """Canonical override mood, or raise ValueError (user-facing message)."""
        from music.mood_map import MOODS
        value = str(mood or "").strip().lower()
        if value in MOODS or value == SILENCE:
            return value
        raise ValueError(f"unknown mood {mood!r}; expected one of "
                         f"{', '.join(list(MOODS) + [SILENCE])}")

    def get_override(self, worker_id):
        try:
            return parse_override(self._client.get(override_key(worker_id)))
        except Exception as exc:  # noqa: BLE001 — fail open
            log.warning("music override read failed worker=%s err=%s", worker_id, exc)
            return None

    def set_override(self, worker_id, mood, intensity=0.5):
        mood = self.validate_mood(mood)
        intensity = _parse_intensity(intensity)
        self._client.set(override_key(worker_id),
                         json.dumps({"mood": mood, "intensity": intensity, "at": time.time()}))
        log.info("music override set worker=%s mood=%s intensity=%.2f", worker_id, mood, intensity)
        return {"mood": mood, "intensity": intensity}

    def clear_override(self, worker_id):
        self._client.delete(override_key(worker_id))
        log.info("music override cleared worker=%s", worker_id)

    def publish_status(self, worker_id, status):
        """Director heartbeat. Never raises."""
        try:
            self._client.set(status_key(worker_id), json.dumps(status), ex=STATUS_TTL_S)
        except Exception as exc:  # noqa: BLE001
            log.debug("music status publish failed worker=%s err=%s", worker_id, exc)

    def get_status(self, worker_id):
        try:
            raw = self._client.get(status_key(worker_id))
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001 — fail open
            log.warning("music status read failed worker=%s err=%s", worker_id, exc)
            return None


@dataclass(frozen=True)
class MoodRequest:
    mood: str                 # a MOODS member, or SILENCE
    intensity: float
    source: str               # "gm_override" | "scene" | "hold"
    scene_id: str = ""
    theme: str = ""           # campaign theme requested by a scene cue ("" = keep)


def _parse_intensity(value, default=0.5):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def parse_override(raw):
    """Redis value -> MoodRequest | None (None = follow the scene)."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = {"mood": str(raw)}
    if not isinstance(data, dict):
        return None
    mood = str(data.get("mood") or "").strip().lower()
    if mood in ("", FOLLOW):
        return None
    if mood != SILENCE:
        mood = normalize_mood(mood)
    return MoodRequest(mood, _parse_intensity(data.get("intensity")), "gm_override")


def parse_scene_cue(data):
    """Cue dict -> MoodRequest | None. `mood` may be a string or a list of
    GEMS moods (the scene's `mood:` field); the first valid one wins."""
    if not isinstance(data, dict):
        return None
    moods = data.get("mood")
    if isinstance(moods, str):
        moods = [moods]
    if not isinstance(moods, list) or not moods:
        return None
    mood = next((normalize_mood(m) for m in moods if normalize_mood(m) != "neutral"), "neutral")
    return MoodRequest(mood, _parse_intensity(data.get("intensity")), "scene",
                       str(data.get("scene_id") or ""), str(data.get("theme") or ""))


class MoodResolver:
    """Pure decision logic, clock injected for tests."""

    def __init__(self, min_dwell_s=20.0, clock=time.monotonic):
        self.min_dwell_s = float(min_dwell_s)
        self.clock = clock
        self.current = MoodRequest("neutral", 0.5, "hold")
        self._changed_at = -1e9
        self._pending_scene = None

    def resolve(self, override, scene):
        """Return the MoodRequest to play now given the latest override and
        scene cue (either may be None)."""
        now = self.clock()
        if override is not None:
            self._pending_scene = None
            if override != self.current:
                log.debug("resolver override %s -> %s", self.current, override)
                self._set(override, now)
            return self.current
        if scene is not None and scene != self._last_scene_seen():
            self._pending_scene = scene
        if self.current.source == "gm_override":
            # Override just released: go straight back to the scene's mood.
            target = self._pending_scene or scene or MoodRequest(
                self.current.mood if self.current.mood != SILENCE else "neutral",
                self.current.intensity, "hold")
            self._pending_scene = None
            self._set(target, now)
            return self.current
        if self._pending_scene is not None and now - self._changed_at >= self.min_dwell_s:
            log.debug("resolver scene %s -> %s", self.current, self._pending_scene)
            self._set(self._pending_scene, now)
            self._pending_scene = None
        return self.current

    def _last_scene_seen(self):
        return self._pending_scene or (self.current if self.current.source == "scene" else None)

    def _set(self, req, now):
        if req.mood != self.current.mood or req.source != self.current.source:
            log.info("music mood resolved mood=%s intensity=%.2f source=%s scene=%s",
                     req.mood, req.intensity, req.source, req.scene_id)
        self.current = req
        self._changed_at = now


class ControlSources:
    """Reads the two inputs. Every failure degrades to 'no input'."""

    def __init__(self, worker_id, cue_path=DEFAULT_CUE_PATH, redis_client=None):
        self.worker_id = worker_id
        self.cue_path = Path(cue_path)
        self.redis = redis_client
        self._cue_mtime = None
        self._cue = None

    def read_override(self):
        if self.redis is None:
            return None
        try:
            return parse_override(self.redis.get(override_key(self.worker_id)))
        except Exception as exc:  # noqa: BLE001 — redis down => follow scene
            log.warning("music override read failed worker=%s err=%s", self.worker_id, exc)
            return None

    def read_scene(self):
        try:
            mtime = self.cue_path.stat().st_mtime
        except OSError:
            return self._cue
        if mtime != self._cue_mtime:
            self._cue_mtime = mtime
            try:
                self._cue = parse_scene_cue(json.loads(self.cue_path.read_text(encoding="utf-8")))
                log.debug("music scene cue read path=%s cue=%s", self.cue_path, self._cue)
            except (OSError, ValueError) as exc:
                log.warning("music scene cue unreadable path=%s err=%s", self.cue_path, exc)
        return self._cue


def write_scene_cue(mood, scene_id="", intensity=0.5, path=DEFAULT_CUE_PATH, theme=None):
    """Publisher helper for the show side (replay_pane on_scene_start).
    `theme` optionally names the campaign theme to switch to (the director
    loads <themes_dir>/<theme>/music/theme.yaml). Atomic write; never raises."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        payload = {"mood": mood, "intensity": intensity, "scene_id": scene_id, "at": time.time()}
        if theme:
            payload["theme"] = str(theme)
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as exc:
        log.error("music write_scene_cue failed path=%s err=%s", path, exc)
        return False
