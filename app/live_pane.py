#!/usr/bin/env python3
"""
live_pane.py
The LIVE office transcript on the roundtable (ashiorid_office OB-32,
agent_dnd_architecture.md W2 / §7.1). docs/live_pane.md has the full story.

The recorded-episode roundtable already works like this (roundtable_stream_
design.md §4, docs/relay_io.md):

    bus --> agent.py handler --> relay FILE --> replay_pane.py (director)
                                                   --> <relay>/<slot>.* files
                                                   --> tile_pane.py (one per seat)

W2 keeps every hop and only changes the FEED. Instead of a replayed episode
the director reads the office's committed spoken lines, in commit order,
from a relay spool, and hands each line to the speaker's tile through one
more per-slot relay file. Nothing here consumes Kafka: the roundtable's own
agent.py does that and drops files, exactly like replay_request does.

    seat handler (office.py)   publish_office_line -> bus `office_line`
                               to "roundtable" (agent.office.live_transcript)
    roundtable agent.py        handle_office_line  -> <relay>/live/<ns>-<id>.json
                               handle_observer_pose -> <relay>/<seat>.pose.json
    replay_pane.py (director)  LiveDirector.drain_once: oldest spool file
                               first, TTS it (voice.speakers[<seat>]), write
                               <relay>/<seat>.live.json, wait for the tile to
                               claim it and for the line to finish, next line
    tile_pane.py (per seat)    handle_live_once: claim <slot>.live.json and
                               perform it through the SAME Performer + voice
                               gate + stage writer (gaze.py) a replay uses

The Party Member's seat (agent.live.observer_slot, default tuber_7) never
gets text: its lines are refused at every hop, and its tile head follows
the live speaker (stage.json) or, between lines, the latest observer_pose
gaze target (ObserverGaze).

Everything is opt-in on `agent.live.enabled` in the ROUNDTABLE config plus
the director gates the tiles already use (TILE_RELAY_DIR env + role
roundtable), so the dev-team roundtable, whose config has no `live:` block,
behaves exactly as before. Every step degrades, none raises into a caller.
"""
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path

import gaze
import relay_io
from message_bus import build_message, correlation_of

log = logging.getLogger("live_pane")
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

# ── contract constants ───────────────────────────────────────────────────────
#: Bus type a seat sends when it commits a spoken line.
OFFICE_LINE = "office_line"
#: Bus type the Party Member's idle hook broadcasts (agent_handlers.office).
OBSERVER_POSE = "observer_pose"
#: The roundtable show container's bus id (control panel ROUNDTABLE_WORKER_ID).
ROUNDTABLE_WORKER_ID = "roundtable"
ROUNDTABLE_ROLE = "roundtable"
#: `type` of a per-slot tile live file.
LIVE_LINE_TYPE = "live_line"
PARTY_MEMBER_ROLE = "party_member"

DEFAULT_OBSERVER_SLOT = "tuber_7"
#: A spooled line older than this is dropped, never aired (the director was
#: busy with a replay, or down): a stale line out of context is worse than none.
DEFAULT_MAX_AGE_S = 120.0
#: How long the director waits for a tile to claim its line (tile poll is 1 s;
#: a tile busy airing a replay never claims it, so the line is withdrawn).
DEFAULT_CONSUME_TIMEOUT_S = 20.0
#: Beat of air the director leaves between two committed lines.
DEFAULT_LINE_GAP_S = 0.5
#: Upper bound on queued lines; the oldest go first when it is exceeded.
DEFAULT_SPOOL_MAX = 200
#: An observer_pose older than this no longer steers the observer's head.
POSE_TTL_S = 90.0
#: Keep a synthesized live WAV at most this long (the tile deletes it after
#: playing; this is only the backstop for lines that were never played).
AUDIO_TTL_S = 600.0
MAX_TEXT_CHARS = 1200
CONSUME_POLL_S = 0.25
#: Silent-line pacing (no TTS): about 150 words a minute, floored.
SPOKEN_CHARS_PER_S = 15.0
MIN_LINE_S = 2.0

_SPOOL_NAME_RE = re.compile(r"^\d{20}-[0-9a-f]{8}\.json$")


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


def _event(event, level=logging.INFO, **fields):
    """One structured key=value line to stderr (panes: stdout is the display;
    agent.py prints are what docker logs see) and the module logger."""
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    line = f"event={event} {kv}".rstrip()
    prefix = {logging.ERROR: "ERROR ", logging.WARNING: "WARN "}.get(level, "")
    print(f"[live_pane] {prefix}{line}", file=sys.stderr)
    log.log(level, "live_pane %s", line)


# ── relay paths (all under TILE_RELAY_DIR) ───────────────────────────────────
def resolve_relay_dir(relay_dir=None):
    return relay_io.resolve_tile_relay_dir(relay_dir)


def live_spool_dir(relay_dir):
    """agent -> director: one file per committed line, oldest name first."""
    return str(Path(relay_dir) / "live")


def tile_live_file(relay_dir, slot):
    """director -> tile: the ONE line this seat's tile should speak next."""
    return str(Path(relay_dir) / f"{slot}.live.json")


def tile_pose_file(relay_dir, slot):
    """agent -> observer tile: the latest observer_pose for `slot`."""
    return str(Path(relay_dir) / f"{slot}.pose.json")


def live_audio_dir(relay_dir):
    """director -> tile: synthesized WAVs for live lines."""
    return str(Path(relay_dir) / "live_audio")


# ── settings ─────────────────────────────────────────────────────────────────
def _agent_block(config):
    """Accept a full worker config or just its `agent:` block."""
    config = config or {}
    agent = config.get("agent")
    return agent if isinstance(agent, dict) else config


def live_settings(config):
    """The roundtable's `agent.live` block, normalised. enabled=False when
    the block is absent (the dev-team roundtable)."""
    raw = _agent_block(config).get("live")
    raw = raw if isinstance(raw, dict) else {}

    def number(key, default):
        try:
            return float(raw.get(key, default))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": raw.get("enabled") is True,
        "observer_slot": str(raw.get("observer_slot") or DEFAULT_OBSERVER_SLOT),
        "max_age_s": number("max_age_s", DEFAULT_MAX_AGE_S),
        "consume_timeout_s": number("consume_timeout_s", DEFAULT_CONSUME_TIMEOUT_S),
        "line_gap_s": max(0.0, number("line_gap_s", DEFAULT_LINE_GAP_S)),
        "spool_max": max(1, int(number("spool_max", DEFAULT_SPOOL_MAX))),
    }


def roundtable_live_enabled(config):
    """True only in the roundtable container of a live show: the same two
    gates replay_pane._resolve_local_tiles uses for tiles (TILE_RELAY_DIR
    env set, role roundtable) plus `agent.live.enabled: true`."""
    if not os.environ.get(relay_io.TILE_RELAY_DIR_ENV):
        return False
    if _agent_block(config).get("role") != ROUNDTABLE_ROLE:
        return False
    return live_settings(config)["enabled"]


def observer_slot_of(config):
    """The seat that never gets text on a LIVE roundtable, or None when the
    live feed is off (a dev-team tuber_7 is a normal speaking character)."""
    settings = live_settings(config)
    return settings["observer_slot"] if settings["enabled"] else None


def seat_ids():
    """Every office seat id, tuber_0..tuber_7 (app/office/roles.py SEAT)."""
    from office.roles import SEAT
    return sorted(set(SEAT.values()), key=lambda s: gaze.slot_index(s) or 0)


def estimate_line_seconds(text):
    return max(MIN_LINE_S, len(text or "") / SPOKEN_CHARS_PER_S)


# ── seat side: office handlers call this where a line is spoken ─────────────
def live_transcript_enabled(agent_config):
    """Seat opt-in: `agent.office.live_transcript: true`."""
    office = (agent_config or {}).get("office")
    return isinstance(office, dict) and office.get("live_transcript") is True


def publish_office_line(worker_id, agent_config, producer, line, emotion=None,
                        correlation_id=None, to=None):
    """Send the line a seat just spoke to the roundtable as `office_line`
    {seat, text, emotion, to}. Opt-in per seat (live_transcript_enabled);
    the Party Member never publishes. Returns the message or None. Never
    raises — the roundtable is decoration for the office chain."""
    _trace("publish_office_line enter worker=%s", worker_id)
    try:
        if not live_transcript_enabled(agent_config):
            return None
        if str((agent_config or {}).get("office_role") or "") == PARTY_MEMBER_ROLE:
            log.debug("live_pane publish skipped worker=%s reason=party_member", worker_id)
            return None
        text = str(line or "").strip()
        if not text:
            log.debug("live_pane publish skipped worker=%s reason=empty", worker_id)
            return None
        payload = {"seat": worker_id, "text": text[:MAX_TEXT_CHARS], "emotion": emotion}
        if to:
            payload["to"] = to
        msg = build_message(worker_id, ROUNDTABLE_WORKER_ID, OFFICE_LINE, payload,
                            correlation_id=correlation_id)
        producer.send(msg)
        log.debug("live_pane published worker=%s chars=%d correlation_id=%s",
                  worker_id, len(text), correlation_of(msg))
        return msg
    except Exception as exc:  # noqa: BLE001 — never break the office chain
        print(f"[agent:{worker_id}] ERROR event=office_line_publish_failed error='{exc}'")
        log.error("live_pane publish failed worker=%s error=%s", worker_id, exc)
        return None


# ── roundtable agent side: bus -> relay files ────────────────────────────────
def accept_office_line(msg, config):
    """Validate one `office_line` into a spool entry, or None (logged).

    Rules: a known seat id; the SENDER is that seat (a seat commits only its
    own lines); non-empty text; never the observer seat."""
    payload = (msg or {}).get("payload") if isinstance(msg, dict) else None
    if not isinstance(payload, dict):
        _event("office_line_refused", logging.WARNING, reason="no_payload")
        return None
    seat = str(payload.get("seat") or "")
    if seat not in seat_ids():
        _event("office_line_refused", logging.WARNING, reason="unknown_seat", seat=seat)
        return None
    if msg.get("from") != seat:
        _event("office_line_refused", logging.WARNING, reason="sender_mismatch",
               seat=seat, sender=msg.get("from"))
        return None
    if seat == observer_slot_of(config):
        _event("office_line_refused", logging.WARNING, reason="observer_never_speaks", seat=seat)
        return None
    text = str(payload.get("text") or "").strip()
    if not text:
        _event("office_line_refused", logging.WARNING, reason="empty_text", seat=seat)
        return None
    entry = {
        "type": OFFICE_LINE,
        "line_id": str(msg.get("id") or uuid.uuid4()),
        "seat": seat,
        "text": text[:MAX_TEXT_CHARS],
        "emotion": payload.get("emotion"),
        "correlation_id": correlation_of(msg),
        "sent_at": msg.get("timestamp"),
        "received_at": time.time(),
    }
    to = payload.get("to")
    if isinstance(to, (str, list)) and to:
        entry["to"] = to
    return entry


def list_spool(relay_dir):
    """Spool files, oldest first (names sort by enqueue time)."""
    spool = Path(live_spool_dir(relay_dir))
    try:
        names = sorted(n for n in os.listdir(spool) if _SPOOL_NAME_RE.match(n))
    except OSError:
        return []
    return [str(spool / n) for n in names]


def enqueue_line(relay_dir, entry, spool_max=DEFAULT_SPOOL_MAX):
    """Atomically add `entry` to the spool; trims the OLDEST entries past
    spool_max. Returns the path. Raises OSError (the handler reports it)."""
    spool = Path(live_spool_dir(relay_dir))
    spool.mkdir(parents=True, exist_ok=True)
    path = spool / f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}.json"
    relay_io.atomic_write_json(path, entry)
    pending = list_spool(relay_dir)
    for old in pending[:max(0, len(pending) - spool_max)]:
        relay_io.remove_file(old)
        _event("office_line_dropped", logging.WARNING, reason="spool_full", path=old)
    return str(path)


def handle_office_line(worker_id, agent_config, msg, relay_dir=None):
    """Roundtable agent.py: spool one committed line for the director.
    Returns the spool path, or None (not a live roundtable / refused /
    write failed). Never raises."""
    _trace("handle_office_line enter worker=%s", worker_id)
    if not roundtable_live_enabled(agent_config):
        log.debug("live_pane office_line ignored worker=%s reason=live_disabled", worker_id)
        return None
    entry = accept_office_line(msg, agent_config)
    if entry is None:
        return None
    relay_dir = resolve_relay_dir(relay_dir)
    try:
        path = enqueue_line(relay_dir, entry, live_settings(agent_config)["spool_max"])
    except OSError as exc:
        _event("office_line_spool_failed", logging.ERROR, seat=entry["seat"], error=f"'{exc}'")
        return None
    _event("office_line_spooled", seat=entry["seat"], line_id=entry["line_id"],
           correlation_id=entry["correlation_id"])
    return path


def write_pose(relay_dir, slot, pose, gaze_target, now=None):
    data = {"seat": slot, "pose": pose, "gaze_target": gaze_target,
            "at": time.time() if now is None else float(now)}
    relay_io.atomic_write_json(tile_pose_file(relay_dir, slot), data)
    return data


def handle_observer_pose(worker_id, agent_config, msg, relay_dir=None):
    """Roundtable agent.py: record the Party Member's latest pose for its
    tile. Only the observer seat's own pose is kept; there is no text field
    to carry. Returns the written dict or None. Never raises."""
    if not roundtable_live_enabled(agent_config):
        return None
    payload = (msg or {}).get("payload") or {}
    seat = str(payload.get("seat") or "")
    observer = observer_slot_of(agent_config)
    if seat != observer or msg.get("from") != seat:
        _event("observer_pose_refused", logging.WARNING, seat=seat, sender=msg.get("from"))
        return None
    target = payload.get("gaze_target")
    target = str(target) if gaze.slot_index(target) is not None else None
    try:
        return write_pose(resolve_relay_dir(relay_dir), seat,
                          str(payload.get("pose") or "idle_watch"), target)
    except OSError as exc:
        _event("observer_pose_write_failed", logging.ERROR, error=f"'{exc}'")
        return None


# ── director side (replay_pane.py main loop) ─────────────────────────────────
def _display_name(config, slot):
    voice = (config or {}).get("voice") or {}
    names = voice.get("speaker_names") or {}
    if names.get(slot):
        return str(names[slot])
    roster = (config or {}).get("roster") or {}
    entry = roster.get(slot) if isinstance(roster, dict) else None
    name = entry.get("name") if isinstance(entry, dict) else entry
    return str(name or slot)


class LiveDirector:
    """Drains the live spool in commit order, one line at a time.

    The director is the clock, as in a replay (on_scene_start writes the
    cues): it hands line N to its tile, waits until the tile has claimed it
    and the line has had time to finish, then hands line N+1. The voice gate
    still enforces "one voice at a time" inside the tiles; this ordering is
    what keeps two tiles from racing for the gate out of commit order."""

    def __init__(self, config, relay_dir=None, tts=None, clock=time.time,
                 sleep=time.sleep, mono=time.monotonic):
        self.config = config or {}
        self.settings = live_settings(self.config)
        self.relay_dir = resolve_relay_dir(relay_dir)
        self.tts = tts
        self.clock = clock
        self.sleep = sleep
        self.mono = mono

    @classmethod
    def from_config(cls, config, relay_dir=None):
        """A director for this worker, or None when the live feed is off."""
        if not roundtable_live_enabled(config):
            return None
        tts = None
        try:
            from tts_client import build_tts_client
            tts = build_tts_client(config)
        except Exception as exc:  # noqa: BLE001 — silent lines beat no lines
            _event("live_tts_unavailable", logging.WARNING, error=f"'{exc}'")
        director = cls(config, relay_dir=relay_dir, tts=tts)
        _event("live_director_ready", relay_dir=director.relay_dir,
               observer=director.settings["observer_slot"], tts=tts is not None)
        return director

    def pending(self):
        return list_spool(self.relay_dir)

    def _synthesize(self, entry):
        """(audio_path, duration) for the line, or (None, estimate)."""
        estimate = estimate_line_seconds(entry["text"])
        if self.tts is None:
            return None, estimate
        out = Path(live_audio_dir(self.relay_dir)) / f"{entry['line_id']}.wav"
        log.debug("live_pane tts enter seat=%s line_id=%s", entry["seat"], entry["line_id"])
        try:
            narration = self.tts.synthesize(entry["text"], out, speaker=entry["seat"])
        except Exception as exc:  # noqa: BLE001
            _event("live_tts_failed", logging.WARNING, seat=entry["seat"],
                   line_id=entry["line_id"], error=f"'{exc}'")
            return None, estimate
        duration = float(getattr(narration, "duration", 0) or 0)
        if duration <= 0:
            return None, estimate
        return str(getattr(narration, "audio_path", out)), duration

    def prepare(self, entry):
        """The tile payload for one spooled line."""
        audio_path, duration = self._synthesize(entry)
        payload = {
            "type": LIVE_LINE_TYPE,
            "line_id": entry["line_id"],
            "speaker": entry["seat"],
            "name": _display_name(self.config, entry["seat"]),
            "text": entry["text"],
            "emotion": entry.get("emotion"),
            "audio_path": audio_path,
            "duration": duration,
            "correlation_id": entry.get("correlation_id"),
            "created_at": self.clock(),
        }
        if entry.get("to"):
            payload["to"] = entry["to"]
        return payload

    def hand(self, payload):
        """Write the tile file and wait for the tile to claim it; then hold
        for the line's duration + gap. True when the tile took the line."""
        slot = payload["speaker"]
        path = tile_live_file(self.relay_dir, slot)
        try:
            relay_io.atomic_write_json(path, payload)
        except OSError as exc:
            _event("live_hand_failed", logging.ERROR, seat=slot, error=f"'{exc}'")
            return False
        deadline = self.mono() + self.settings["consume_timeout_s"]
        while os.path.exists(path):
            if self.mono() >= deadline:
                # Withdraw it only if it is still THIS line, so a tile that
                # wakes up late never speaks an out-of-order line.
                relay_io.remove_if(path, lambda d: isinstance(d, dict)
                                   and d.get("line_id") == payload["line_id"])
                _event("live_line_withdrawn", logging.WARNING, seat=slot,
                       line_id=payload["line_id"], reason="tile_did_not_claim")
                if payload.get("audio_path"):
                    relay_io._unlink_quietly(payload["audio_path"])
                return False
            self.sleep(CONSUME_POLL_S)
        _event("live_line_handed", seat=slot, line_id=payload["line_id"],
               voiced=payload.get("audio_path") is not None,
               duration_s=round(payload["duration"], 2))
        self.sleep(payload["duration"] + self.settings["line_gap_s"])
        return True

    def drain_once(self, should_yield=None, limit=None):
        """Air every pending line (oldest first) until the spool is empty,
        `limit` lines were handed, or should_yield() says a replay request
        is waiting — the recorded path wins and the rest stays spooled.
        Returns the number of lines handed. Never raises."""
        handed = 0
        try:
            for path in self.pending():
                if should_yield is not None and should_yield():
                    log.debug("live_pane drain yielded handed=%d", handed)
                    break
                if limit is not None and handed >= limit:
                    break
                found, entry, error = relay_io.consume_json(path)
                if not found:
                    continue
                if error or not isinstance(entry, dict) or not entry.get("seat") \
                        or not entry.get("text"):
                    _event("live_line_dropped", logging.WARNING, reason="invalid", path=path)
                    continue
                age = self.clock() - float(entry.get("received_at") or 0)
                if age > self.settings["max_age_s"]:
                    _event("live_line_dropped", logging.WARNING, reason="stale",
                           seat=entry["seat"], age_s=round(age, 1))
                    continue
                if entry["seat"] == self.settings["observer_slot"]:
                    _event("live_line_dropped", logging.WARNING,
                           reason="observer_never_speaks", seat=entry["seat"])
                    continue
                if self.hand(self.prepare(entry)):
                    handed += 1
            self.prune_audio()
        except Exception as exc:  # noqa: BLE001 — the director loop must survive
            _event("live_drain_failed", logging.ERROR, error=f"'{type(exc).__name__}: {exc}'")
        return handed

    def prune_audio(self):
        folder = Path(live_audio_dir(self.relay_dir))
        try:
            entries = list(folder.iterdir())
        except OSError:
            return 0
        cutoff, removed = self.clock() - AUDIO_TTL_S, 0
        for item in entries:
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


# ── tile side (tile_pane.py main loop) ───────────────────────────────────────
#: slot -> TileRenderer kept across live lines, so a tile shows the running
#: conversation, not one line at a time. One tile process = one slot.
_renderers = {}


def reset_live(slot):
    """Forget a tile's live history (a recorded airing just ran over it)."""
    renderer = _renderers.pop(slot, None)
    if renderer is not None:
        renderer.close()


def _renderer_for(slot, state_path, out=None):
    import tile_pane
    renderer = _renderers.get(slot)
    if renderer is None:
        renderer = tile_pane.TileRenderer(slot, state_path, out=out, fade_enabled=out is None)
        _renderers[slot] = renderer
    return renderer


def _live_cast():
    return {seat: seat for seat in seat_ids()}


def live_script(payload):
    """A one-event, header-less script for the voice gate / stage writer."""
    event = {"type": "assistant_text", "text": payload["text"]}
    if payload.get("to"):
        event["to"] = payload["to"]
    return {"source": "live", "events": [event]}


def live_scene(payload, script):
    """The Performer scene for one live line: owned by the speaker's tile,
    voiced when the director synthesized audio, else held for its estimate."""
    from tts_client import Narration
    audio = None
    audio_path, duration = payload.get("audio_path"), float(payload.get("duration") or 0)
    if audio_path and duration > 0 and os.path.exists(audio_path):
        audio = Narration(audio_path=Path(audio_path), duration=duration)
    return {"kind": "assistant", "speaker": payload["speaker"], "owned": True,
            "narration": None, "events": list(script["events"]), "audio": audio,
            "target_duration": duration if duration > 0 else estimate_line_seconds(payload["text"])}


def perform_live_line(payload, slot, relay_dir, state_path=None, config=None, out=None):
    """Speak one claimed live line on this tile. True when it performed."""
    import replay_pane
    import tile_pane
    from replay import Pacer, Palette, Performer, ReplayStopped

    script = live_script(payload)
    scene = live_scene(payload, script)
    renderer = _renderer_for(slot, state_path, out=out)
    voice = (config or {}).get("voice") or {}
    stop_file = replay_pane._resolve_replay_stop_file()
    gate, _gap = replay_pane.build_voice_gate(script, config, tag=f"live:{slot}")
    try:
        on_voice_start = tile_pane.make_stage_writer(
            slot, tile_pane.tile_stage_file(relay_dir), script, _live_cast(),
            config=config, airing_id=payload.get("line_id"))
    except Exception as exc:  # noqa: BLE001 — gaze is decoration
        _event("live_gaze_disabled", logging.WARNING, seat=slot, error=f"'{exc}'")
        on_voice_start = None
    performer = Performer(
        out=renderer,
        pacer=Pacer(speed=1.0, should_stop=lambda: os.path.exists(stop_file)),
        palette=Palette(enabled=True),
        worker_name=str(payload.get("name") or slot),
        state_path=state_path,
        speaker_names=voice.get("speaker_names") or {},
        boss_name=voice.get("boss_name"),
        voice_gate=gate,
        line_gap_s=0.0,
        on_voice_start=on_voice_start,
    )
    performed = True
    try:
        # One scene, not perform(): perform() frames a whole airing with
        # "REPLAY:" / "fin" and a closing bubble that must not appear here.
        performer._perform_scene(scene)
    except ReplayStopped:
        performed = False
        _event("live_line_stopped", seat=slot, line_id=payload.get("line_id"))
    finally:
        tile_pane.write_tile_state(state_path, "idle", action="listening")
        renderer._last_bubble = None  # the same words may be said again
        renderer.refresh()
        if payload.get("audio_path"):
            relay_io._unlink_quietly(payload["audio_path"])
    return performed


def handle_live_once(slot, relay_dir, state_path=None, config=None, out=None):
    """Tile poll: claim this slot's live line (if any) and perform it.
    False without touching anything when the live feed is off. The observer
    slot's file is claimed and DISCARDED — its tile never shows text.
    Never raises."""
    if not live_settings(config)["enabled"]:
        return False
    path = tile_live_file(relay_dir, slot)
    if not os.path.exists(path):
        return False
    found, payload, error = relay_io.consume_json(path)
    if not found:
        return False
    if slot == observer_slot_of(config):
        _event("live_line_discarded", logging.WARNING, seat=slot, reason="observer_never_speaks")
        if isinstance(payload, dict) and payload.get("audio_path"):
            relay_io._unlink_quietly(payload["audio_path"])
        return False
    if error or not isinstance(payload, dict) or payload.get("type") != LIVE_LINE_TYPE \
            or payload.get("speaker") != slot or not payload.get("text"):
        _event("live_line_discarded", logging.WARNING, seat=slot, reason="malformed")
        return False
    try:
        return perform_live_line(payload, slot, relay_dir, state_path=state_path,
                                 config=config, out=out)
    except Exception as exc:  # noqa: BLE001 — one bad line must never kill the tile
        _event("live_line_failed", logging.ERROR, seat=slot,
               error=f"'{type(exc).__name__}: {exc}'")
        return False


def draw_idle(slot, state_path=None, config=None, out=None):
    """The tile's idle frame. On a live roundtable: the observer shows its
    face and a "watching" status, never text; a seat that has spoken keeps
    its recent lines up. Otherwise exactly tile_pane.draw_idle_screen."""
    import tile_pane
    if slot == observer_slot_of(config):
        tile_pane.write_tile_state(state_path, "idle", action="watching the room")
        return tile_pane.render_tile(slot, expression="idle", line="", status="watching", out=out)
    renderer = _renderers.get(slot)
    if renderer is not None and renderer.lines:
        return renderer.draw()
    return tile_pane.draw_idle_screen(slot, state_path, out=out)


# ── observer head: gaze-follow idle pose ─────────────────────────────────────
class ObserverGaze:
    """The Party Member tile's head. Follows the live speaker (stage.json,
    written by the speaking tile at voice start), else the latest fresh
    observer_pose gaze_target, else rests. The mouth never moves.
    `sample()` has StageGaze's contract: ((yaw, pitch), mouth_open)."""

    READ_INTERVAL_S = gaze.StageGaze.READ_INTERVAL_S

    def __init__(self, slot, stage_path, pose_path, controller=None,
                 clock=time.time, mono=time.monotonic):
        self.slot = slot
        self.stage_path = stage_path
        self.pose_path = pose_path
        self.controller = controller or gaze.GazeController()
        self._clock = clock
        self._mono = mono
        self._read_at = None
        self._stage = None
        self._pose = None

    def _refresh(self):
        mono = self._mono()
        if self._read_at is None or mono - self._read_at >= self.READ_INTERVAL_S:
            self._read_at = mono
            self._stage = gaze.read_stage(self.stage_path) if self.stage_path else None
            self._pose = relay_io.read_json(self.pose_path) if self.pose_path else None

    def target(self, now=None):
        self._refresh()
        now = self._clock() if now is None else now
        target = gaze.gaze_target(self.slot, self._stage, now)
        if target and target != self.slot:
            return target
        pose = self._pose if isinstance(self._pose, dict) else {}
        try:
            fresh = now - float(pose.get("at")) <= POSE_TTL_S
        except (TypeError, ValueError):
            fresh = False
        candidate = pose.get("gaze_target")
        if fresh and candidate and candidate != self.slot and gaze.slot_index(candidate) is not None:
            return candidate
        return None

    def sample(self):
        try:
            target = self.target()
            if target is None:
                yaw, pitch = gaze.REST_YAW_RAD, gaze.REST_PITCH_RAD
            else:
                yaw, pitch = gaze.gaze_angles(self.slot, target)
            return self.controller.step(yaw, pitch, now=self._mono()), 0.0
        except Exception:  # noqa: BLE001 — a head must never take a tile down
            return (self.controller.yaw, self.controller.pitch), 0.0


def make_gaze_source(slot, stage_path, config=None):
    """The head's gaze source for a tile: ObserverGaze for the observer seat
    on a live roundtable, else the stock gaze.StageGaze (unchanged)."""
    if slot == observer_slot_of(config):
        pose_path = tile_pose_file(str(Path(stage_path).parent), slot)
        return ObserverGaze(slot, stage_path, pose_path).sample
    return gaze.StageGaze(slot, stage_path).sample


__all__ = [
    "OFFICE_LINE", "OBSERVER_POSE", "LIVE_LINE_TYPE", "ROUNDTABLE_WORKER_ID",
    "live_spool_dir", "tile_live_file", "tile_pose_file", "live_audio_dir",
    "live_settings", "roundtable_live_enabled", "observer_slot_of",
    "live_transcript_enabled", "publish_office_line", "accept_office_line",
    "enqueue_line", "list_spool", "handle_office_line", "handle_observer_pose",
    "LiveDirector", "handle_live_once", "perform_live_line", "draw_idle",
    "reset_live", "ObserverGaze", "make_gaze_source",
]
