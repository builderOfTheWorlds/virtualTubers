"""
office/playlist.py
The ashiorid_office fallback playlist (OB-33): what airs when nobody is working.

Two callers need filler:
  * off hours — segment s0 (00:00-06:00, app/office/clock.py) has no live work;
  * a stall   — the day runner's stall detector (OB-30) saw N idle minutes.

`OfficePlaylist.next_item(now, reason)` answers both with one `PlaylistItem`,
or None when there is nothing to air. The rotation is:

  1. ambient scenes from the pack's ambient pool
     (campaigns/ashiorid_office/scenes/a0NN-*.yaml; empty until OB-40), then
  2. APPROVED office replays from the Rerun Theater library (message-api
     `GET /replays?status=approved`, names starting `office-`). Drafts never
     appear: the query asks for approved only, and every row is re-checked.

Order is deterministic: items sort by (ambient before replay, name) and the
playlist walks that list, continuing after the last item it handed out and
wrapping at the end. So a pool of two or more never repeats back to back,
and an item removed from the library mid-rotation doesn't restart the cycle.

`replay_request_message(item, to)` turns an item into the bus message the
replay pane already understands (`replay_request`, payload.episode).

No Kafka, no LLM. The only I/O is reading scene YAML and one injectable HTTP
GET. Nothing here raises for a down library: it keeps the last good list.
See docs/office_playlist.md.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from message_bus import build_message

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


REASON_OFF_HOURS = "off_hours"
REASON_STALL = "stall"
REASONS = (REASON_OFF_HOURS, REASON_STALL)

KIND_AMBIENT = "ambient"
KIND_REPLAY = "replay"
_KIND_RANK = {KIND_AMBIENT: 0, KIND_REPLAY: 1}

#: The pack's ambient pool (OB-40 writes a0NN-*.yaml here).
DEFAULT_SCENES_DIR = Path(__file__).resolve().parents[2] / "campaigns" / "ashiorid_office" / "scenes"
AMBIENT_FILE_RE = re.compile(r"^a\d{3}-[A-Za-z0-9._-]*\.ya?ml$")
#: role_attribution.episode_name() prefixes every office replay with this.
OFFICE_REPLAY_PREFIX = "office-"
#: Library name an ambient scene airs under once it is built and uploaded.
AMBIENT_EPISODE_TEMPLATE = "office-ambient-{scene_id}"
DEFAULT_MESSAGE_API_URL = "http://127.0.0.1:8090"
DEFAULT_REFRESH_S = 300.0
DEFAULT_TIMEOUT_S = 10.0
#: Default sender for playlist messages (not a seat; mirrors protocol.CLOCK_SENDER).
PLAYLIST_SENDER = "office_playlist"
#: The phase an off-hours pick is filtered by (clock.PHASES[0]).
OFF_PHASE = "off"


@dataclass(frozen=True)
class PlaylistItem:
    """One thing to air.

    kind        ambient | replay
    ref         the scene id (ambient) or episode name (replay)
    episode     the Rerun Theater library name to request
    reason      off_hours | stall — why it was picked
    picked_at   the `now` passed to next_item
    scene_path  the scene YAML (ambient only), for a caller that plays the
                scene through the campaign runtime instead of the library
    """

    kind: str
    ref: str
    episode: str
    reason: str
    picked_at: datetime | None = None
    scene_path: str | None = None

    @property
    def key(self) -> tuple[int, str]:
        return (_KIND_RANK[self.kind], self.ref)


@dataclass(frozen=True)
class AmbientScene:
    scene_id: str
    path: Path
    phases: tuple[str, ...] = ()   # empty = eligible in every phase


def _phase_name(value) -> str:
    """YAML 1.1 reads a bare `off` as False (and `on` as True); an author
    writing `phases: [off]` means the off segment, so map it back."""
    if value is False:
        return OFF_PHASE
    if value is True:
        return "on"
    return str(value)


def load_ambient_pool(scenes_dir: Path | str = DEFAULT_SCENES_DIR) -> list[AmbientScene]:
    """The ambient scenes in `scenes_dir` (a0NN-*.yaml), sorted by id.

    A scene is skipped (WARN) if its YAML won't parse or it says
    `ambient: false`. An optional `phases:` (or `phase:`) list limits when it
    may air. A missing directory is an empty pool, not an error."""
    _trace("load_ambient_pool enter dir=%s", scenes_dir)
    root = Path(scenes_dir)
    if not root.is_dir():
        log.debug("playlist.ambient_dir_missing dir=%s", root)
        return []
    import yaml  # lazy: callers that pass an empty pool never need it

    scenes: dict[str, AmbientScene] = {}
    for path in sorted(root.iterdir()):
        if not AMBIENT_FILE_RE.match(path.name):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.warning("playlist.ambient_unreadable file=%s error=%s", path.name, type(exc).__name__)
            continue
        if not isinstance(data, dict):
            log.warning("playlist.ambient_not_mapping file=%s", path.name)
            continue
        if data.get("ambient") is False:
            log.debug("playlist.ambient_skip_flagged_false file=%s", path.name)
            continue
        scene_id = str(data.get("id") or path.stem).strip()
        raw_phases = data.get("phases", data.get("phase"))
        if isinstance(raw_phases, str):
            raw_phases = [raw_phases]
        phases = tuple(_phase_name(p) for p in raw_phases) if isinstance(raw_phases, list) else ()
        if scene_id in scenes:
            log.warning("playlist.ambient_duplicate_id id=%s file=%s", scene_id, path.name)
            continue
        scenes[scene_id] = AmbientScene(scene_id, path, phases)
    pool = [scenes[k] for k in sorted(scenes)]
    log.debug("playlist.ambient_loaded dir=%s count=%d", root, len(pool))
    return pool


def _httpx_get_json(url, params, timeout):
    import httpx
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


class ReplayLibrary:
    """Approved office replays from message-api, cached for `refresh_s`.

    `fetch_json(url, params, timeout) -> dict` is injectable (tests pass a
    fake); it may raise on any failure. The default uses httpx."""

    def __init__(self, message_api_url: str = DEFAULT_MESSAGE_API_URL, *,
                 prefix: str = OFFICE_REPLAY_PREFIX, refresh_s: float = DEFAULT_REFRESH_S,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 fetch_json: Callable[[str, dict, float], dict] | None = None):
        self.url = f"{message_api_url.rstrip('/')}/replays"
        self.prefix = prefix
        self.refresh_s = refresh_s
        self.timeout_s = timeout_s
        self._fetch = fetch_json or _httpx_get_json
        self._cache: list[str] = []
        self._fetched_at: datetime | None = None

    def approved(self, now: datetime | None = None) -> list[str]:
        """Sorted names of approved office replays. Refetches when the cache
        is older than `refresh_s` (or `now` is None); keeps the last good
        list if the library is unreachable."""
        _trace("ReplayLibrary.approved enter now=%s", now)
        fresh = (now is not None and self._fetched_at is not None
                 and 0 <= (now - self._fetched_at).total_seconds() < self.refresh_s)
        if fresh:
            log.debug("playlist.library_cache_hit count=%d", len(self._cache))
            return list(self._cache)
        log.debug("playlist.library_fetch url=%s", self.url)
        try:
            data = self._fetch(self.url, {"status": "approved"}, self.timeout_s)
            rows = data.get("episodes") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise ValueError("response has no 'episodes' list")
        except Exception as exc:  # noqa: BLE001 — a down library must not stop the playlist
            log.warning("playlist.library_unavailable url=%s error=%s cached=%d",
                        self.url, type(exc).__name__, len(self._cache))
            return list(self._cache)
        names = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            # Belt and braces: never trust the filter alone to exclude drafts.
            if row.get("status", "approved") != "approved":
                log.debug("playlist.library_skip_unapproved name=%s", name)
                continue
            if name.startswith(self.prefix):
                names.add(name)
        self._cache = sorted(names)
        self._fetched_at = now
        log.debug("playlist.library_fetched rows=%d office_approved=%d", len(rows), len(self._cache))
        return list(self._cache)


@dataclass
class OfficePlaylist:
    """The day runner's filler source. See the module docstring.

    ambient_pool         AmbientScene list; None loads `scenes_dir`
    library              a ReplayLibrary, or None for ambient only
    ambient_requires_approved
                         True: an ambient scene is eligible only once its
                         built episode (AMBIENT_EPISODE_TEMPLATE) is approved
                         in the library — use this when ambient airs through
                         replay_request rather than the campaign runtime
    """

    ambient_pool: list[AmbientScene] | None = None
    library: ReplayLibrary | None = None
    scenes_dir: Path | str = DEFAULT_SCENES_DIR
    ambient_requires_approved: bool = False
    ambient_episode_template: str = AMBIENT_EPISODE_TEMPLATE
    last: PlaylistItem | None = field(default=None, init=False)

    def __post_init__(self):
        if self.ambient_pool is None:
            self.ambient_pool = load_ambient_pool(self.scenes_dir)

    def reload_ambient(self) -> int:
        """Re-read the ambient pool from `scenes_dir` (after OB-40 adds scenes)."""
        self.ambient_pool = load_ambient_pool(self.scenes_dir)
        log.info("playlist.ambient_reloaded count=%d", len(self.ambient_pool))
        return len(self.ambient_pool)

    def candidates(self, now: datetime | None, reason: str, phase: str | None = None) -> list[PlaylistItem]:
        """Everything eligible right now, in rotation order."""
        _trace("candidates enter reason=%s phase=%s", reason, phase)
        if reason not in REASONS:
            log.error("playlist.bad_reason reason=%r", reason)
            raise ValueError(f"reason must be one of {REASONS}, got {reason!r}")
        if phase is None and reason == REASON_OFF_HOURS:
            phase = OFF_PHASE
        replays = self.library.approved(now) if self.library is not None else []
        library_names = set(replays)
        items = []
        for scene in self.ambient_pool or []:
            if phase is not None and scene.phases and phase not in scene.phases:
                log.debug("playlist.ambient_skip_phase id=%s phase=%s", scene.scene_id, phase)
                continue
            episode = self.ambient_episode_template.format(scene_id=scene.scene_id)
            if self.ambient_requires_approved and episode not in library_names:
                log.debug("playlist.ambient_skip_unbuilt id=%s", scene.scene_id)
                continue
            items.append(PlaylistItem(KIND_AMBIENT, scene.scene_id, episode, reason, now, str(scene.path)))
        ambient_episodes = {i.episode for i in items}
        for name in replays:
            if name in ambient_episodes:
                continue   # already queued as its ambient scene
            items.append(PlaylistItem(KIND_REPLAY, name, name, reason, now))
        items.sort(key=lambda i: i.key)
        return items

    def next_item(self, now: datetime | None, reason: str, phase: str | None = None) -> PlaylistItem | None:
        """The next thing to air, or None when the pool is empty.

        `reason` is REASON_OFF_HOURS or REASON_STALL (ValueError otherwise).
        `phase` (a clock.PHASES value) filters phase-tagged ambient scenes;
        off_hours defaults it to "off". Never returns the previous item
        again while any other item is eligible."""
        _trace("next_item enter now=%s reason=%s", now, reason)
        items = self.candidates(now, reason, phase)
        if not items:
            log.warning("playlist.empty reason=%s", reason)
            return None
        choice = items[0]
        if self.last is not None:
            later = [i for i in items if i.key > self.last.key]
            choice = later[0] if later else items[0]
            log.debug("playlist.rotate last=%s wrapped=%s", self.last.ref, not later)
        self.last = choice
        log.info("playlist.next kind=%s ref=%s reason=%s pool=%d",
                 choice.kind, choice.ref, reason, len(items))
        return choice


def replay_request_message(item: PlaylistItem, to: str, *, from_: str = PLAYLIST_SENDER,
                           cast: dict | None = None, correlation_id: str | None = None,
                           causation_id: str | None = None) -> dict:
    """A `replay_request` bus envelope for `item` addressed to worker `to`.

    payload.episode is the library name (the only field the replay handler
    requires); `cast` is passed through for the roundtable's duet director
    (agent_handlers/replay_relay.py); payload.playlist records why it aired."""
    _trace("replay_request_message enter episode=%s to=%s", item.episode, to)
    if not to:
        raise ValueError("replay_request_message needs a recipient")
    payload = {"episode": item.episode, "playlist": {"kind": item.kind, "reason": item.reason}}
    if cast:
        payload["cast"] = dict(cast)
    msg = build_message(from_, to, "replay_request", payload,
                        correlation_id=correlation_id, causation_id=causation_id)
    log.debug("playlist.message id=%s to=%s episode=%s", msg["id"], to, item.episode)
    return msg


def replay_request_messages(item: PlaylistItem, targets: Iterable[str], **kwargs) -> list[dict]:
    """One replay_request per worker id in `targets` (the control panel's
    per-worker fan-out; see panel.py play_replay)."""
    return [replay_request_message(item, to, **kwargs) for to in targets]
