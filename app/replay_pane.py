#!/usr/bin/env python3
"""
replay_pane.py
Long-lived tmux pane program for "Rerun Theater": idles with an episode
listing, and performs an episode (app/replay.py) whenever the agent drops
a request file.

The request file is the agent -> pane handoff (same local-file IPC pattern
as agent_state.py): agent.py's replay_request handler writes
REPLAY_REQUEST_FILE atomically; this pane polls for it, performs the
episode, deletes the file, and returns to the idle screen. File-based on
purpose — the pane never consumes Kafka and never executes anything from
the bus; the only thing a bus message can influence is WHICH pre-built,
pre-redacted episode in the library gets played.

The library itself lives in Postgres (app/episode_store.py), uploaded and
validated through message-api's POST /replays — there is no episode
directory mounted into the worker any more. Requested names are still
reduced to a bare basename before lookup, so a hostile payload can only
ever name a row that an operator already uploaded and the validator
already cleared.

Postgres being unreachable therefore means no reruns at all: the idle
screen says so plainly and a request reports the outage instead of
performing. Like every other failure here, it degrades — it never raises
out of the pane loop.

The pane produces to Kafka (never consumes): after a voiced airing it
publishes the spoken transcript as a replay_narration message so
message-logger persists it to Postgres's voiced_narration table. The pane
also upserts the full airing — text plus the synthesized WAV bytes and
measured duration — straight into voiced_narration itself via
app/narration_store.py, reusing the same message_id, so a later
replay_request with payload.narration: "reuse" can replay that exact
airing instead of calling the LLM + TTS again. Postgres being unreachable
just degrades this to an uncached fresh airing every time (see
docs/revoice.md).
"""
import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import episode_store
import narration_store
from agent_state import resolve_state_path
from message_bus import MessageProducer, build_message, resolve
from replay import Pacer, Palette, Performer, prepare_voiced_show

DEFAULT_REQUEST_FILE = "/tmp/replay_request.json"
DEFAULT_WORKER_CONFIG = "/config/worker.yaml"
POLL_INTERVAL_S = 2.0
IDLE_REDRAW_S = 300  # re-list the library occasionally (episodes get uploaded)

# Agent -> pane stop signal (docs/operator_commands.md `replay_stop`): same
# atomic-write / env-override convention as REPLAY_REQUEST_FILE above.
# handle_replay_stop (app/agent.py) writes it; every performance path below
# wires it into its Performer's Pacer(should_stop=...) so an operator stop
# lands within a fraction of a second, not just at the next scene boundary
# (docs/replay.md ReplayStopped).
DEFAULT_REPLAY_STOP_FILE = "/tmp/replay_stop.json"

# ── Duet replay (docs/duet_replay.md) ────────────────────────────────────────
# Relay files the agent (app/agent.py) writes and this pane polls — same
# atomic-write / env-override convention as REPLAY_REQUEST_FILE above.
DEFAULT_REPLAY_CUE_FILE = "/tmp/replay_cue.json"
DEFAULT_REPLAY_READY_FILE = "/tmp/replay_ready.json"

# Director: how long to wait for every invited follower to publish
# replay_ready before refusing the airing outright (duets never degrade to
# solo — see perform_director_request).
REPLAY_READY_TIMEOUT_ENV = "REPLAY_READY_TIMEOUT_S"
REPLAY_READY_TIMEOUT_DEFAULT_S = 60.0
REPLAY_READY_POLL_INTERVAL_S = 0.25

# Follower: cue-file poll interval and the cue ratchet's watchdog timeouts —
# the first cue gets a generous flat allowance (the director is still
# preparing/inviting/annotating before it ever starts scene 0); every cue
# after that is bounded by the PREVIOUS scene's own target_duration (roughly
# how long the director should still be busy performing it) plus a grace
# window, floored so a near-zero-duration scene doesn't produce a hair-
# trigger watchdog.
REPLAY_CUE_POLL_INTERVAL_S = 0.25
REPLAY_FIRST_CUE_TIMEOUT_S = 120.0
REPLAY_WATCHDOG_GRACE_S = 30.0
REPLAY_WATCHDOG_MIN_S = 45.0


def resolve_self_id(config, worker_name):
    """This worker's bus identity, for duet ownership matching (docs/
    duet_replay.md "self id"): config.message_bus.worker_id wins, then the
    WORKER_ID env var, falling back to the pane's --worker-name so a
    config-less/local run still resolves to a stable identity."""
    bus_config = (config or {}).get("message_bus") or {}
    return bus_config.get("worker_id") or os.environ.get("WORKER_ID") or worker_name


def _read_json_file(path):
    """Best-effort read of a small relay file this pane only ever polls
    (never writes) — missing or corrupt content is "nothing new yet", not an
    error worth raising."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _delete_stale_file(path):
    """Best-effort cleanup of a leftover relay file from a previous show —
    a duet role starting up must never trip over stale state (docs/
    duet_replay.md "stale-state hygiene")."""
    try:
        Path(path).unlink()
    except OSError:
        pass


def _resolve_replay_stop_file():
    return os.environ.get("REPLAY_STOP_FILE") or DEFAULT_REPLAY_STOP_FILE


def _resolve_replay_cue_file():
    return os.environ.get("REPLAY_CUE_FILE") or DEFAULT_REPLAY_CUE_FILE


def _resolve_replay_ready_file():
    return os.environ.get("REPLAY_READY_FILE") or DEFAULT_REPLAY_READY_FILE


def build_voice_gate(script, config, tag="worker"):
    """The voice gate for one airing (docs/voice_gate.md), from the layered
    sources in voice_gate.resolve_voice_gate: VOICE_GATE_* env (whole stack,
    compose) > the episode's show.audio header (per show) > the worker
    config's voice.audio (per worker) > defaults (1 seat, 0 gap, shared
    world-state dir).

    Returns (gate, line_gap_s); gate is None when the gate dir is unusable
    or flock is unavailable — the show then runs exactly as before (no
    serialization), which is the required degrade: audio must never be
    withheld over a broken gate.

    All containers of one duet resolve the SAME gate dir and seat count
    from the same (script, env) — the default dir lives on the shared
    world-state named volume, which is what makes the seat a real lock
    ACROSS containers, not just within one process."""
    import voice_gate
    gate_dir, seats, line_gap_s, acquire_timeout_s = voice_gate.resolve_voice_gate(
        script, config, roster_size=7)
    gate = voice_gate.VoiceGate(gate_dir, seats=seats,
                                acquire_timeout_s=acquire_timeout_s, tag=tag)
    if not gate.available():
        return None, line_gap_s
    return gate, line_gap_s


def _build_bus_producer(config):
    """Best-effort Kafka producer from a worker config's message_bus
    section — None when unconfigured or construction fails (e.g. Kafka
    unreachable). Duet director/follower paths treat a None producer as a
    hard refusal (docs/duet_replay.md refusal rule); solo shows never call
    this at all."""
    bus_config = (config or {}).get("message_bus") or {}
    bootstrap_servers = resolve("KAFKA_BOOTSTRAP_SERVERS", bus_config.get("bootstrap_servers"))
    topic = resolve("KAFKA_TOPIC", bus_config.get("topic"))
    if not bootstrap_servers or not topic:
        return None
    try:
        return MessageProducer(bootstrap_servers, topic)
    except Exception as exc:
        print(f"[replay_pane] duet bus producer unavailable: {exc}", file=sys.stderr)
        return None


def _safe_send(producer, message):
    """Best-effort publish — a single duet relay message failing (director
    cue, follower ready, ...) must not take the show down; the receiving
    side's own watchdog/timeout is what recovers from a dropped message."""
    if producer is None:
        return False
    try:
        producer.send(message)
        return True
    except Exception as exc:
        print(f"[replay_pane] duet message publish failed ({message.get('type')}): {exc}",
              file=sys.stderr)
        return False


def sanitize_episode_name(episode):
    """Reduce a requested episode to a bare library key: path components
    stripped, a trailing '.json' dropped. Returns "" for anything empty.

    The store lookup is a parameterized equality match, so this is belt and
    braces — but it keeps the property the old filesystem lookup had, that
    what comes off the bus can only ever name an episode, never a path."""
    name = Path(str(episode or "")).name
    if name.endswith(".json"):
        name = name[:-len(".json")]
    return name


def resolve_episode(episode):
    """Look a requested episode up in the store.

    Returns (name, script), or (None, None) when the episode isn't in the
    library OR the store is unreachable — the caller reports the miss and
    keeps the pane alive, so this never raises. The two cases are
    distinguished on stderr, since "Postgres is down" is the one an operator
    has to act on.
    """
    name = sanitize_episode_name(episode)
    if not name:
        return None, None
    if not episode_store.available():
        print("[replay_pane] episode store unavailable (POSTGRES_* not configured)",
              file=sys.stderr)
        return None, None
    try:
        script = episode_store.load_episode(name)
    except Exception as exc:
        print(f"[replay_pane] episode store unreachable: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None, None
    if script is None:
        return None, None
    return name, script


def read_request(request_file):
    """Read-and-consume the request file. Returns the request dict or None.
    A malformed file is consumed (deleted) and reported — a bad request must
    not wedge the pane in a crash loop."""
    path = Path(request_file)
    if not path.exists():
        return None
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError(f"expected object, got {type(request).__name__}")
    except (OSError, ValueError) as exc:
        print(f"[replay_pane] discarding malformed request: {exc}", file=sys.stderr)
        request = None
    try:
        path.unlink()
    except OSError:
        pass
    return request


def list_episodes():
    """Episode names from the store, or None when the store can't be read.

    None and [] are deliberately different: with no filesystem fallback,
    "the library is empty" and "Postgres is down" need different words on
    the idle screen."""
    if not episode_store.available():
        return None
    try:
        return episode_store.list_episodes()
    except Exception as exc:
        print(f"[replay_pane] episode listing failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


def draw_idle_screen(worker_name):
    episodes = list_episodes()
    print("\x1b[2J\x1b[H", end="")  # clear pane between shows
    print("╔══════════════════════════════════════╗")
    print("║          R E R U N   T H E A T E R   ║")
    print("╚══════════════════════════════════════╝")
    print(f" host: {worker_name}")
    if episodes is None:
        print(" episode store unreachable — check Postgres; no reruns until it's back")
    elif episodes:
        print(f" {len(episodes)} episode(s) in the library:")
        for name in episodes[:20]:
            print(f"   • {name}")
        if len(episodes) > 20:
            print(f"   … and {len(episodes) - 20} more")
    else:
        print(" library empty — upload episodes with POST /replays on message-api")
    print()
    print(' waiting for a replay_request ("perform episode X")…')


def prepare_voice(script, config, workdir, worker_name, speed):
    """Best-effort per-airing narration pass (docs/revoice.md). Returns a
    voiced show, or None for a silent performance — voice being disabled,
    unconfigured, or broken must never stop an episode from airing."""
    if not config:
        return None
    try:
        show = prepare_voiced_show(
            script, config, workdir, worker_name=worker_name, speed=speed,
            progress=lambda message: print(f"[replay_pane] preparing: {message}"),
        )
    except Exception as exc:
        print(f"[replay_pane] voice preparation failed ({exc}) — silent show",
              file=sys.stderr)
        return None
    if show is not None:
        voiced = sum(1 for scene in show if scene.get("audio"))
        print(f"[replay_pane] tonight's episode: {len(show)} scenes, {voiced} voiced")
    return show


def publish_narration(show, config, episode, worker_name):
    """Best-effort: publish this airing's spoken transcript (text only, no
    audio) onto the bus so message-logger persists it to Postgres's
    voiced_narration table. No longer the only durable record of what got
    said — persist_narration() below saves the full airing (including WAV
    bytes) directly to the same table, reusing the id this returns. Kafka
    being down/unconfigured must never stop or delay a show, so this always
    runs after the show is already fully prepared and never raises.

    Returns the published message's id on success, or None when the airing
    was skipped or the publish failed/was unconfigured."""
    if not show:
        return None
    bus_config = (config or {}).get("message_bus") or {}
    bootstrap_servers = resolve("KAFKA_BOOTSTRAP_SERVERS", bus_config.get("bootstrap_servers"))
    topic = resolve("KAFKA_TOPIC", bus_config.get("topic"))
    if not bootstrap_servers or not topic:
        return None
    payload = {
        "episode": episode,
        "aired_at": datetime.now(timezone.utc).isoformat(),
        "scenes": [
            {
                "index": index,
                "kind": scene.get("kind"),
                "speaker": scene.get("speaker"),
                "text": scene.get("narration"),
            }
            for index, scene in enumerate(show)
        ],
    }
    worker_id = bus_config.get("worker_id", worker_name)
    msg = build_message(worker_id, "broadcast", "replay_narration", payload)
    try:
        MessageProducer(bootstrap_servers, topic).send(msg)
    except Exception as exc:
        print(f"[replay_pane] narration transcript publish failed: {exc}", file=sys.stderr)
        return None
    return msg["id"]


def persist_narration(message_id, show, config, episode, worker_name):
    """Best-effort: upsert the full airing (text + WAV bytes + measured
    duration) directly into Postgres's voiced_narration table via
    app/narration_store.py, so a later replay_request with
    payload.narration: "reuse" can replay this exact airing. Reuses
    publish_narration()'s message_id so the two converge on one row set;
    Kafka being down just means we mint our own id — the cache must still
    work even without a bus. Never raises: a store outage must never stop
    or delay a show.

    Returns the message_id the airing was actually saved under (the one
    passed in, or the freshly-minted uuid when it was None) on success, or
    None when nothing was saved. The duet director path uses this as a
    fresh airing's airing_id and treats None as a hard refusal (docs/
    duet_replay.md refusal rule); solo callers ignore the return value."""
    if not show:
        return None
    if message_id is None:
        message_id = str(uuid.uuid4())
    if not narration_store.available():
        print("[replay_pane] narration store unavailable — airing not cached for reuse")
        return None
    bus_config = (config or {}).get("message_bus") or {}
    worker_id = bus_config.get("worker_id", worker_name)
    try:
        n = narration_store.save_airing(
            message_id, worker_id, episode,
            aired_at=datetime.now(timezone.utc).isoformat(),
            show=show,
        )
    except Exception as exc:
        print(f"[replay_pane] narration cache save failed: {exc}", file=sys.stderr)
        return None
    print(f"[replay_pane] cached narration ({n} scenes) for reuse")
    return message_id


def _rebuild_scenes_from_rows(script, rows, workdir, owns=None):
    """Shared by load_reused_show (solo/director reuse) and the duet
    follower path (perform_follower_request): rebuild plan_scenes(script)
    and splice each row's cached narration text — and optionally its
    synthesized audio — back in. Returns None when the scene count or kind
    sequence no longer lines up with `rows` — a stale/incompatible cache the
    caller must refuse rather than perform partially.

    `owns(scene, row)` gates whether that scene's audio bytes get written to
    `workdir` at all (default: every scene, i.e. no gating — the solo/
    director-reuse case, which wants every speaker's real audio locally). A
    duet follower passes a predicate that's only true for scenes cast to
    itself, so it never writes another performer's audio to its own temp
    dir. `target_duration` is always set from audio_duration_s regardless of
    `owns`, so pacing stays correct on scenes this worker doesn't voice too.
    May raise (I/O, tts_client import) — callers wrap in their own try/except
    per their existing error-reporting conventions."""
    from revoice import plan_scenes
    from tts_client import Narration, wav_duration

    scenes = plan_scenes(script.get("events", []))
    if len(scenes) != len(rows) or any(
        scene["kind"] != row["scene_kind"] for scene, row in zip(scenes, rows)
    ):
        return None
    for scene, row in zip(scenes, rows):
        scene["narration"] = row["text"]
        scene["audio"] = None
        scene["target_duration"] = row.get("audio_duration_s")
        if row["audio"] and (owns is None or owns(scene, row)):
            path = Path(workdir) / f"scene_{row['scene_index']:03d}.wav"
            path.write_bytes(row["audio"])
            duration = row["audio_duration_s"] or wav_duration(path)
            scene["audio"] = Narration(audio_path=path, duration=duration)
    return scenes


def _load_cached_show(script, episode, workdir):
    """Core of load_reused_show, also used by the duet director's
    narration:"reuse" path (perform_director_request), which additionally
    needs the raw rows to recover the reused airing's message_id/airing_id
    (docs/duet_replay.md). Returns (scenes, rows) on success, or (None,
    None) when there's nothing usable to reuse. Never raises."""
    if not narration_store.available():
        print("[replay_pane] narration store unavailable — generating fresh narration")
        return None, None
    try:
        cached = narration_store.load_latest_airing(episode)
    except Exception as exc:
        print(f"[replay_pane] narration cache load failed: {exc}", file=sys.stderr)
        return None, None
    if not cached:
        print(f"[replay_pane] no cached narration for {episode!r} — generating fresh")
        return None, None
    try:
        scenes = _rebuild_scenes_from_rows(script, cached, workdir)
        if scenes is None:
            print(f"[replay_pane] cached narration no longer matches episode script "
                  f"— generating fresh")
            return None, None
        print(f"[replay_pane] reusing cached narration for {episode!r} ({len(scenes)} scenes)")
        return scenes, cached
    except Exception as exc:
        print(f"[replay_pane] narration reuse failed: {exc}", file=sys.stderr)
        return None, None


def load_reused_show(script, episode, workdir):
    """Rebuild a voiced show from the latest cached airing of `episode`
    instead of calling the LLM + TTS again. Returns the show, or None when
    there's nothing usable to reuse — the caller falls back to a fresh
    generation (show-must-air rule, docs/revoice.md). Never raises."""
    scenes, _rows = _load_cached_show(script, episode, workdir)
    return scenes


def _send_replay_end(producer, self_id, followers, airing_id, reason):
    """Director → each follower: replay_end (docs/duet_replay.md #4). Used
    both for a normal finish (reason "finished") and for every refusal path
    (reason "ready_timeout" / "aborted") — best-effort, one publish failure
    must not stop the rest from going out."""
    for follower in followers:
        _safe_send(producer, build_message(self_id, follower, "replay_end",
                                           {"airing_id": airing_id, "reason": reason}))


def _send_operator_error(producer, self_id, error):
    """Director → operator: operator_reply carrying a duet refusal reason
    (docs/duet_replay.md refusal rule). Best-effort — panes may produce but
    a failed publish must not raise out of the refusal path."""
    _safe_send(producer, build_message(self_id, "operator", "operator_reply", {"error": error}))


# ── local roundtable tiles (roundtable_stream_design.md §4/§4.1, WP-6 task 1) ─
# The GM container hosts one tile pane per cast slot (app/tile_pane.py). Tiles
# are LOCAL: they are invited and cued by relay FILES the director writes, not
# by Kafka — a tile never consumes the bus. The six character workers have no
# tiles at all, so every write below is gated on _resolve_local_tiles() and
# every one of them is best-effort (a failed relay write must never take the
# show down; the tile's own watchdog is the backstop, same philosophy as
# _safe_send above).
ROUNDTABLE_PRESET = "roundtable"
TILE_RELAY_DIR_ENV = "TILE_RELAY_DIR"
LAYOUT_PRESET_ENV = "LAYOUT_PRESET"


def _atomic_write_json(path, data):
    """Atomic write of a small director -> tile relay file (same
    temp+replace pattern as agent_state.py / agent.py's _atomic_write_json)
    so a polling tile never reads a half-written cue. Raises OSError —
    callers decide how loudly to report a failure."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def _layout_preset(config):
    """The worker's layout preset, LAYOUT_PRESET env winning over the config
    file — same precedence build_layout.resolve_preset uses, supporting both
    `layout: {preset: roundtable}` and the `layout: roundtable` shorthand."""
    layout = (config or {}).get("layout")
    file_preset = None
    if isinstance(layout, dict):
        file_preset = layout.get("preset")
    elif isinstance(layout, str):
        file_preset = layout
    return os.environ.get(LAYOUT_PRESET_ENV) or file_preset


def _resolve_local_tiles(cast, config):
    """The slot ids this worker drives as LOCAL tiles, or [] when it has
    none. Opt-in on purpose (design §4, WP-6 task 1): only the GM container
    runs tile panes, so the six character workers must resolve to [] and
    behave exactly as they did before this wire existed.

    Two gates, both required: a relay dir must be configured
    (TILE_RELAY_DIR), AND the worker's layout preset / agent role must name
    the roundtable. The roster itself is every slot in the cast — the cast's
    VALUES are worker/slot ids — INCLUDING the director's own slot, because
    the GM is a character whose lines are played by its TILE, not by the
    director process (§1.1/§4.1)."""
    if not os.environ.get(TILE_RELAY_DIR_ENV):
        return []
    role = ((config or {}).get("agent") or {}).get("role")
    if _layout_preset(config) != ROUNDTABLE_PRESET and role != ROUNDTABLE_PRESET:
        return []
    return sorted({str(slot) for slot in (cast or {}).values() if slot})


def _write_tile_relay_file(path, payload, label):
    """Best-effort atomic relay write — one tile failing to be written must
    never stop the show or the other tiles (see _safe_send)."""
    try:
        _atomic_write_json(path, payload)
        return True
    except OSError as exc:
        print(f"[replay_pane] tile relay write failed ({label} -> {path}): {exc}",
              file=sys.stderr)
        return False


def _clear_tile_relay_files(relay_dir, slots):
    """Stale-state hygiene BEFORE anything else, same convention as the
    director's cue_file/ready_file/stop_file clearing below. A leftover
    cue/end from a PREVIOUS airing is read on the tile's very first poll,
    which either aborts the new show instantly (a stale "end") or
    fast-forwards it into Performer.perform's catch-up path where owned
    audio is discarded instead of played. tile_pane.clear_stale_relay_files
    does the same thing, but it runs in the TILE process — a tile may not
    even be polling yet, so the director must do its own clearing."""
    import tile_pane
    for slot in slots:
        _delete_stale_file(tile_pane.tile_cue_file(relay_dir, slot))
        _delete_stale_file(tile_pane.tile_request_file(relay_dir, slot))


def _write_tile_requests(relay_dir, slots, payload):
    """Kick each tile off once, up front — mirroring how replay_invite
    precedes cues for remote followers."""
    import tile_pane
    for slot in slots:
        _write_tile_relay_file(tile_pane.tile_request_file(relay_dir, slot),
                               payload, "request")


def _write_tile_cues(relay_dir, slots, airing_id, index):
    import tile_pane
    for slot in slots:
        _write_tile_relay_file(
            tile_pane.tile_cue_file(relay_dir, slot),
            {"airing_id": airing_id, "type": "cue", "scene_index": index}, "cue")


def _write_tile_end(relay_dir, slots, airing_id):
    """`type: "end"` makes a tile's wait_for_scene return -1 so it drops back
    to idle instead of sitting on a watchdog. Written on a normal finish AND
    on every refusal path."""
    import tile_pane
    for slot in slots:
        _write_tile_relay_file(
            tile_pane.tile_cue_file(relay_dir, slot),
            {"airing_id": airing_id, "type": "end"}, "end")


def perform_director_request(request, worker_name, state_path, self_id,
                             default_speed=1.0, config=None):
    """Duet director path (docs/duet_replay.md): prepare + persist the full
    airing exactly like a solo show, invite the other cast workers, and only
    perform once every one of them is ready. Refuses outright — never
    degrading to a solo performance — when narration_store, the Kafka
    producer, voice preparation, or persistence isn't available, or when a
    follower never shows up in time. Returns True only when the show
    actually aired."""
    episode = request.get("episode")
    episode_name, script = resolve_episode(episode)
    if script is None:
        print(f"[replay_pane] episode not in the library: {episode!r}", file=sys.stderr)
        return False
    try:
        speed = float(request.get("speed") or default_speed)
    except (TypeError, ValueError):
        speed = default_speed
    name = str(request.get("worker_name") or worker_name)
    cast = request.get("cast") or {}
    # Local roundtable tiles (§4.1) are driven by relay FILES, not Kafka —
    # tile_pane.py never sends replay_ready. A cast slot resolved as a local
    # tile must therefore never enter `followers`/the ready-wait below, or
    # the director waits REPLAY_READY_TIMEOUT_S for a replay_ready that is
    # never coming and refuses every all-local (or mixed) roundtable show.
    # Computed here (before followers) deliberately: _resolve_local_tiles
    # only needs cast + config, not the airing that's built further down.
    local_tile_slots = set(_resolve_local_tiles(cast, config))
    followers = sorted({worker_id for worker_id in cast.values()
                        if worker_id and worker_id != self_id
                        and worker_id not in local_tile_slots})

    producer = _build_bus_producer(config)
    invited = []  # only populated once invites actually go out — a refusal
                  # before that point has nobody to send replay_end to yet
    # Local roundtable tiles (§4.1): resolved further down, once the airing
    # exists. Mutable holder so refuse() — a closure used by refusal paths
    # BOTH before and after that point — can see whatever has been resolved
    # so far. Empty on the earliest refusals (no producer, no narration
    # store), which is exactly right: nothing has been told to start yet.
    tiles = {"relay_dir": None, "slots": []}

    def refuse(log_message, operator_error=None, reason="aborted", airing_id=None):
        print(f"[replay_pane] duet refused: {log_message}", file=sys.stderr)
        # Tiles first: a refusal must release every tile that was already
        # kicked off, so none sits on a watchdog. Guarded and best-effort —
        # a refusal must never raise.
        if tiles["slots"] and tiles["relay_dir"]:
            _write_tile_end(tiles["relay_dir"], tiles["slots"], airing_id)
        if producer is not None:
            _send_replay_end(producer, self_id, invited, airing_id, reason)
            _send_operator_error(producer, self_id, operator_error or log_message)
        return False

    if producer is None:
        return refuse("no Kafka producer available for duet replay")
    if not narration_store.available():
        return refuse("narration store unavailable for duet replay")

    with tempfile.TemporaryDirectory(prefix="replay_voice_") as workdir:
        show, airing_id = None, None
        if request.get("narration") == "reuse":
            reused, rows = _load_cached_show(script, episode_name, workdir)
            if reused is not None:
                show, airing_id = reused, rows[0]["message_id"]
        if show is None:
            show = prepare_voice(script, config, workdir, name, speed)
            if show is None:
                return refuse("voice preparation failed or is disabled for this worker")
            message_id = publish_narration(show, config, episode_name, name)
            airing_id = persist_narration(message_id, show, config, episode_name, name)
            if airing_id is None:
                return refuse("failed to persist duet airing for followers to load")

        # Annotate every scene for the duet: "owned" gates whether THIS
        # worker plays its audio/speaks (Performer._perform_scene); a scene
        # this worker doesn't own has its audio stripped so it never gets
        # played here, but keeps target_duration so visual pacing still
        # tracks the owner's timing.
        #
        # Ownership rule — exactly one performer per line:
        #   * speaker mapped to a LOCAL TILE slot → the tile owns it
        #     (design §4.1: "the director owns no scenes and is silent —
        #     the GM's own lines are played by the GM's TILE"). Before this
        #     fix the old formula (cast.get(speaker, self_id) == self_id)
        #     gave owned=True on BOTH the director and the GM tile for the
        #     GM's own-slot lines → two paplays ~300 ms apart into the same
        #     Pulse vout sink → "characters talking over each other".
        #   * speaker mapped to a REMOTE FOLLOWER  → that follower owns it
        #     (unchanged).
        #   * speaker not mapped in cast at all (solo, or the "boss"
        #     narrator in a header-less two-speaker duet) → the DIRECTOR
        #     must own it, or the line would be heard on no channel at
        #     all (unchanged) — UNLESS the director itself is a roundtable
        #     tile slot (self_id in local_tile_slots), in which case an
        #     uncast speaker is routed to the director's OWN tile instead
        #     (see effective_cast below). Without this, an uncast "boss"
        #     narration line (revoice.plan_scenes defaults a speaker-less
        #     user_message to "boss") is heard on air but displayed on NO
        #     tile at all — reported live as "the very first voice did not
        #     show text" (the ashiorid_generated_ce8d/ashiorid episodes both
        #     open with several speaker-less GM narration lines before the
        #     first line whose speaker happens to be explicitly cast).
        #
        # The fix: the director owns a line only if the old formula says so
        # AND the line is NOT mapped to a local tile slot — when the
        # director's own slot (or any speaker's slot) resolves to a tile,
        # that tile plays the line and the director stays silent. Lines the
        # cast does not map to a slot fall back to the director UNLESS the
        # director is itself a local tile (the roundtable's tuber_0), in
        # which case effective_cast below reroutes them to that tile so a
        # non-slot speaker is still audible AND visible on a roundtable
        # airing — never heard from a tile-less, invisible pane.
        local_tile_set = set(local_tile_slots)
        # effective_cast: on the roundtable (self_id has its own tile), any
        # speaker cast doesn't map at all is treated as if it were
        # explicitly mapped to self_id — the GM's own tile picks up its own
        # unattributed narration instead of it vanishing into the hidden
        # director pane. On a plain (non-roundtable) duet, self_id is never
        # in local_tile_set (the director has no tile there — it has a
        # normal visible replay pane), so this is a no-op and behavior is
        # byte-identical to before.
        effective_cast = dict(cast)
        if self_id in local_tile_set:
            for scene in show:
                speaker = scene.get("speaker")
                if speaker not in effective_cast:
                    effective_cast[speaker] = self_id
        for scene in show:
            speaker = scene.get("speaker")
            old_formula = effective_cast.get(speaker, self_id) == self_id
            owned = old_formula and (effective_cast.get(speaker) not in local_tile_set)
            audio = scene.get("audio")
            scene["owned"] = owned
            if audio is not None:
                scene["target_duration"] = getattr(audio, "duration", None)
            if not owned:
                scene["audio"] = None

        for follower in followers:
            payload = {"airing_id": airing_id, "episode": episode_name, "cast": cast,
                      "speed": speed, "worker_name": name, "director": self_id}
            _safe_send(producer, build_message(self_id, follower, "replay_invite", payload))
        invited = followers

        # ── local roundtable tiles (design §4/§4.1, WP-6 task 1) ──────────
        # The GM also hosts a tile pane per cast slot IN THIS CONTAINER.
        # They are invited/cued by relay FILES rather than Kafka, but the
        # sequence mirrors the remote followers exactly: stale hygiene,
        # then one request each up front, then a cue per scene, then an end.
        # A non-roundtable worker resolves to [] here and writes nothing at
        # all, so the six character channels behave exactly as before.
        local_tiles = sorted(local_tile_slots)
        if local_tiles:
            import tile_pane
            relay_dir = tile_pane.resolve_relay_dir()
            tile_pane.ensure_relay_dir(relay_dir)
            # Stale-state hygiene FIRST, before a single new byte is
            # written — same rule (and the same repeat-bug history) as the
            # cue_file/ready_file/stop_file clearing just below.
            _clear_tile_relay_files(relay_dir, local_tiles)
            tiles["relay_dir"] = relay_dir
            tiles["slots"] = local_tiles
            _write_tile_requests(relay_dir, local_tiles, {
                # effective_cast (not the raw `cast` payload) — every tile's
                # own make_owns() independently re-checks
                # cast.get(speaker) == slot, so it must see the SAME
                # uncast-speaker-routed-to-self_id rerouting the annotation
                # loop above just applied, or the tile that owns an
                # uncast/"boss" narration line per `scene["owned"]` would
                # disagree with its own local ownership check and never
                # actually play/display it (the exact "first voice showed
                # no text" bug this fix addresses).
                "airing_id": airing_id, "episode": episode_name, "cast": effective_cast,
                "speed": speed, "worker_name": name,
            })

        cue_file = _resolve_replay_cue_file()
        _delete_stale_file(cue_file)

        # Stop signal (docs/operator_commands.md `replay_stop`): stale-state
        # hygiene first (a leftover stop from a PREVIOUS airing must never
        # kill this new one before it starts), same convention as cue_file
        # above. should_stop is checked both while waiting on followers
        # below and inside the performance itself (Pacer, docs/replay.md).
        stop_file = _resolve_replay_stop_file()
        _delete_stale_file(stop_file)
        should_stop = lambda: os.path.exists(stop_file)

        # Stale-state hygiene, same convention as cue_file/stop_file above:
        # handle_replay_ready (app/agent.py) unions a sender into an existing
        # ready_file when its airing_id matches — which it always will for a
        # narration:"reuse" airing performed more than once (the airing_id
        # is the persisted cache's message_id, identical on every replay).
        # Without this, a previous performance's ready_file already lists
        # every follower, so the wait loop below sees "ready" on its very
        # first read and starts firing cues before this run's followers have
        # even loaded their own audio — they fall straight into the
        # >=2-scenes-behind catch-up path (Performer.perform's catch_up_to),
        # which discards owned audio via playback.stop() instead of playing
        # it (app/replay.py _perform_scene). A follower whose own scene
        # lands early in the episode can lose its voice entirely and look
        # broken with no error anywhere.
        ready_file = _resolve_replay_ready_file()
        _delete_stale_file(ready_file)
        try:
            timeout_s = float(os.environ.get(REPLAY_READY_TIMEOUT_ENV) or REPLAY_READY_TIMEOUT_DEFAULT_S)
        except (TypeError, ValueError):
            timeout_s = REPLAY_READY_TIMEOUT_DEFAULT_S
        deadline = time.monotonic() + timeout_s
        followers_needed = set(followers)
        ready = False
        stopped_before_ready = False
        # No remote Kafka followers to wait on (e.g. an all-local roundtable
        # cast, or a solo show): nothing will ever write the ready_file, so
        # polling it would be an unconditional timeout every time. The empty
        # set is trivially "ready" — but an already-signalled stop must
        # still be honored before we declare that and start performing.
        if not followers_needed:
            if should_stop():
                stopped_before_ready = True
            else:
                ready = True
        while not ready and not stopped_before_ready:
            if should_stop():
                stopped_before_ready = True
                break
            state = _read_json_file(ready_file)
            if isinstance(state, dict) and state.get("airing_id") == airing_id:
                if followers_needed <= set(state.get("workers") or []):
                    ready = True
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(REPLAY_READY_POLL_INTERVAL_S)
        if stopped_before_ready:
            return refuse("operator replay_stop received before duet cast was ready",
                          reason="stopped", airing_id=airing_id)
        if not ready:
            return refuse("timed out waiting for duet followers to become ready",
                          reason="ready_timeout", airing_id=airing_id)

        def on_scene_start(index):
            for follower in followers:
                _safe_send(producer, build_message(self_id, follower, "replay_cue",
                                                    {"airing_id": airing_id, "scene_index": index}))
            # Same hook, same clock: local tile cues are written here too so
            # the roundtable can never desync from the character channels by
            # more than one scene boundary (WP-6 §6 risk row).
            if local_tiles:
                _write_tile_cues(tiles["relay_dir"], local_tiles, airing_id, index)

        gate, line_gap_s = build_voice_gate(script, config, tag=f"director:{self_id}")
        performer = Performer(
            pacer=Pacer(speed=speed, should_stop=should_stop),
            palette=Palette(enabled=True),
            worker_name=name,
            state_path=state_path,
            on_scene_start=on_scene_start,
            speaker_names=((config or {}).get("voice") or {}).get("speaker_names") or {},
            boss_name=((config or {}).get("voice") or {}).get("boss_name"),
            voice_gate=gate,
            line_gap_s=line_gap_s,
        )
        completed = performer.perform(script, show=show)
        _delete_stale_file(stop_file)

        _send_replay_end(producer, self_id, followers, airing_id,
                         "finished" if completed else "stopped")
        # Release every local tile too — without this a tile sits polling
        # until its watchdog fires instead of dropping straight back to idle.
        if local_tiles:
            _write_tile_end(tiles["relay_dir"], local_tiles, airing_id)
    return True


def perform_follower_request(request, worker_name, state_path, self_id,
                             default_speed=1.0, config=None):
    """Duet follower path (docs/duet_replay.md): load the SAME airing the
    director already persisted — NEVER generate fresh narration here — keep
    audio only for the scenes cast to this worker, tell the director it's
    ready, then perform scene-by-scene as replay_cue messages authorize each
    one (the cue ratchet, see wait_for_scene below). Returns True only when
    the show actually aired."""
    airing_id = request.get("airing_id")
    episode = request.get("episode")
    cast = request.get("cast")
    director = request.get("director")
    if not airing_id or not episode or not isinstance(cast, dict):
        print(f"[replay_pane] malformed follower request (airing_id={airing_id!r} "
              f"episode={episode!r} cast={cast!r}) — ignoring", file=sys.stderr)
        return False

    episode_name, script = resolve_episode(episode)
    if script is None:
        print(f"[replay_pane] episode not in the library: {episode!r}", file=sys.stderr)
        return False
    try:
        speed = float(request.get("speed") or default_speed)
    except (TypeError, ValueError):
        speed = default_speed
    name = str(request.get("worker_name") or worker_name)

    if not narration_store.available():
        print("[replay_pane] narration store unavailable — cannot follow duet airing",
              file=sys.stderr)
        return False
    try:
        rows = narration_store.load_airing(airing_id)
    except Exception as exc:
        print(f"[replay_pane] follower airing load failed: {exc}", file=sys.stderr)
        return False
    if not rows:
        print(f"[replay_pane] no cached airing {airing_id!r} to follow — cannot perform",
              file=sys.stderr)
        return False

    cue_file = _resolve_replay_cue_file()
    with tempfile.TemporaryDirectory(prefix="replay_voice_") as workdir:
        try:
            show = _rebuild_scenes_from_rows(
                script, rows, workdir,
                owns=lambda scene, row: cast.get(scene.get("speaker")) == self_id,
            )
        except Exception as exc:
            print(f"[replay_pane] follower scene rebuild failed: {exc}", file=sys.stderr)
            return False
        if show is None:
            print(f"[replay_pane] cached airing {airing_id!r} no longer matches episode "
                  f"script — cannot follow", file=sys.stderr)
            return False

        for scene in show:
            scene["owned"] = cast.get(scene.get("speaker")) == self_id

        # Stale-state hygiene: clear any leftover cue from a previous show
        # BEFORE announcing readiness, so this follower can never consume a
        # cue/end meant for a different airing (docs/duet_replay.md). Same
        # for the operator stop signal (docs/operator_commands.md
        # `replay_stop`) — a leftover from a PREVIOUS airing must never
        # kill this new one before it starts.
        _delete_stale_file(cue_file)
        stop_file = _resolve_replay_stop_file()
        _delete_stale_file(stop_file)

        producer = _build_bus_producer(config)
        target = director or "operator"
        if not _safe_send(producer, build_message(self_id, target, "replay_ready",
                                                   {"airing_id": airing_id})):
            print("[replay_pane] could not notify director of readiness — cannot follow",
                  file=sys.stderr)
            return False

        def wait_for_scene(index):
            if index == 0:
                timeout_s = REPLAY_FIRST_CUE_TIMEOUT_S
            else:
                prev_duration = show[index - 1].get("target_duration") or 0
                timeout_s = max(REPLAY_WATCHDOG_MIN_S, prev_duration + REPLAY_WATCHDOG_GRACE_S)
            deadline = time.monotonic() + timeout_s
            while True:
                # Operator replay_stop reuses the existing cue-abort protocol
                # (-1 -> perform()'s "interrupted" shutdown) rather than a
                # separate code path — a follower blocked here (not inside
                # Pacer.sleep) needs its own check.
                if os.path.exists(stop_file):
                    return -1
                cue = _read_json_file(cue_file)
                if isinstance(cue, dict) and cue.get("airing_id") == airing_id:
                    if cue.get("type") == "end":
                        return -1
                    if cue.get("type") == "cue":
                        scene_index = cue.get("scene_index")
                        if isinstance(scene_index, int) and scene_index >= index:
                            return scene_index
                if time.monotonic() >= deadline:
                    print(f"[replay_pane] duet follower watchdog timed out waiting for "
                          f"scene {index}", file=sys.stderr)
                    return -1
                time.sleep(REPLAY_CUE_POLL_INTERVAL_S)

        gate, line_gap_s = build_voice_gate(script, config, tag=f"follower:{self_id}")
        performer = Performer(
            pacer=Pacer(speed=speed, should_stop=lambda: os.path.exists(stop_file)),
            palette=Palette(enabled=True),
            worker_name=name,
            state_path=state_path,
            wait_for_scene=wait_for_scene,
            speaker_names=((config or {}).get("voice") or {}).get("speaker_names") or {},
            boss_name=((config or {}).get("voice") or {}).get("boss_name"),
            voice_gate=gate,
            line_gap_s=line_gap_s,
        )
        performer.perform(script, show=show)
        _delete_stale_file(stop_file)
    return True


def perform_request(request, worker_name, state_path, default_speed=1.0,
                    config=None):
    """Resolve and perform one request. Returns True if an episode played.

    Duet dispatch (docs/duet_replay.md): payload "mode": "follow" ⇒ this
    worker is a follower in someone else's duet airing (perform_follower_
    request); else a "cast" dict mapping any speaker to a worker other than
    this one ⇒ this worker directs a new duet airing (perform_director_
    request). Neither ⇒ solo, exactly as before this feature landed — a
    cast whose values are ALL this worker's own id is solo too, since there
    is nobody else to duet with.
    """
    self_id = resolve_self_id(config, worker_name)
    if request.get("mode") == "follow":
        return perform_follower_request(request, worker_name, state_path, self_id,
                                        default_speed=default_speed, config=config)
    cast = request.get("cast")
    if isinstance(cast, dict) and any(worker_id != self_id for worker_id in cast.values()):
        return perform_director_request(request, worker_name, state_path, self_id,
                                        default_speed=default_speed, config=config)

    episode = request.get("episode")
    episode_name, script = resolve_episode(episode)
    if script is None:
        print(f"[replay_pane] episode not in the library: {episode!r}", file=sys.stderr)
        return False
    try:
        speed = float(request.get("speed") or default_speed)
    except (TypeError, ValueError):
        speed = default_speed
    name = str(request.get("worker_name") or worker_name)

    # Stop signal (docs/operator_commands.md `replay_stop`): stale-state
    # hygiene first — a leftover stop from a PREVIOUS episode must never
    # kill this new one before it starts (same convention as the duet relay
    # files above) — then consumed again after performing.
    stop_file = _resolve_replay_stop_file()
    _delete_stale_file(stop_file)

    gate, line_gap_s = build_voice_gate(script, config, tag=f"solo:{name}")
    performer = Performer(
        pacer=Pacer(speed=speed, should_stop=lambda: os.path.exists(stop_file)),
        palette=Palette(enabled=True),
        worker_name=name,
        state_path=state_path,
        speaker_names=((config or {}).get("voice") or {}).get("speaker_names") or {},
        boss_name=((config or {}).get("voice") or {}).get("boss_name"),
        voice_gate=gate,
        line_gap_s=line_gap_s,
    )
    with tempfile.TemporaryDirectory(prefix="replay_voice_") as workdir:
        show = None
        if request.get("voice") is not False:  # request can force a silent airing
            if request.get("narration") == "reuse":
                show = load_reused_show(script, episode_name, workdir)
            if show is None:
                show = prepare_voice(script, config, workdir, name, speed)
                message_id = publish_narration(show, config, episode_name, name)
                persist_narration(message_id, show, config, episode_name, name)
        performer.perform(script, show=show)
    _delete_stale_file(stop_file)
    return True


def load_worker_config(path):
    """Worker config for the voice/llm sections; None (silent shows) when
    missing or unparseable."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or None
    except Exception as exc:
        print(f"[replay_pane] could not read worker config {path}: {exc}",
              file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Rerun Theater pane — idles, performs requested episodes")
    parser.add_argument("--request-file", default=os.environ.get("REPLAY_REQUEST_FILE", DEFAULT_REQUEST_FILE))
    parser.add_argument("--worker-name", default=os.environ.get("WORKER_ID", "worker"))
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", DEFAULT_WORKER_CONFIG),
                        help="Worker config YAML — its voice+llm sections drive spoken "
                             "narration (voice.provider: null keeps shows silent)")
    parser.add_argument("--once", action="store_true",
                        help="Handle at most one pending request, then exit (testing)")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    state_path = resolve_state_path()
    config = load_worker_config(args.config)
    provider = ((config or {}).get("voice") or {}).get("provider")
    print(f"[replay_pane] library={'postgres' if episode_store.available() else 'UNAVAILABLE'} "
          f"request_file={args.request_file} "
          f"voice={'on' if provider not in (None, 'null') else 'off'}")

    if args.once:
        request = read_request(args.request_file)
        if request:
            perform_request(request, args.worker_name, state_path,
                            config=config)
        return

    last_drawn = 0.0
    while True:
        request = read_request(args.request_file)
        if request:
            try:
                perform_request(request, args.worker_name, state_path,
                                config=config)
            except Exception as exc:  # one bad episode must not kill the pane
                print(f"[replay_pane] episode failed: {exc}", file=sys.stderr)
            time.sleep(5)  # hold the final frame briefly
            last_drawn = 0.0  # force idle redraw
        if time.time() - last_drawn > IDLE_REDRAW_S:
            draw_idle_screen(args.worker_name)
            last_drawn = time.time()
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
