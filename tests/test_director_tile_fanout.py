"""Tests for WP-6 task 1 — the duet/roundtable director's LOCAL tile fan-out
(.claude/prompts/wp6_rehearsal_and_e2e_plan.md §2,
.claude/prompts/roundtable_stream_design.md v1.1 §4 / §4.1 / §6.1).

app/tile_pane.py polls `<relay>/<slot>.request.json` and
`<relay>/<slot>.cue.json`; before this work nothing wrote them, so the GM's
seven tiles sat on their idle screens forever. These tests pin the producer
half: request up front, a cue per scene, an `end` on finish AND on refusal,
stale hygiene first, atomic writes — and, the regression guard that protects
the six live character channels, NOTHING AT ALL on a non-roundtable worker.

Fully mocked: no Kafka, no Postgres, no TTS, no real Performer timing.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import replay_pane  # noqa: E402
import tile_pane  # noqa: E402
from replay_pane import perform_director_request  # noqa: E402


# ── fixtures / doubles (conventions mirrored from tests/test_replay_pane.py) ──
class RecordingProducer:
    """Fake MessageProducer recording everything sent through ONE instance —
    the director builds exactly one and reuses it for every invite/cue/end."""

    def __init__(self, bootstrap_servers, topic):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


def _recording_producer_ctor(holder):
    def ctor(bootstrap_servers, topic):
        producer = RecordingProducer(bootstrap_servers, topic)
        holder["producer"] = producer
        return producer
    return ctor


class FakePerformer:
    """Walks `show` calling on_scene_start(i)/wait_for_scene(i) — enough to
    exercise the cue wiring with no real timing, pacing, or audio."""
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.performed_show = None
        FakePerformer.instances.append(self)

    def perform(self, script, show=None, start=0, limit=None):
        self.performed_show = show
        on_scene_start = self.kwargs.get("on_scene_start")
        wait_for_scene = self.kwargs.get("wait_for_scene")
        for i in range(len(show or [])):
            if on_scene_start is not None:
                on_scene_start(i)
            if wait_for_scene is not None and wait_for_scene(i) == -1:
                return False
        return True


def _fake_voiced_show(script, config, workdir, **kwargs):
    """prepare_voiced_show() stand-in: a 2-scene show matching the library
    script below (one "boss" scene, one "coder" scene)."""
    return [
        {"kind": "boss", "speaker": "boss", "narration": "boss line",
         "events": [{"type": "assistant_text", "text": "boss line"}], "audio": None},
        {"kind": "coder_talk", "speaker": "coder", "narration": "coder line",
         "events": [{"type": "assistant_text", "text": "coder line"}], "audio": None},
    ]


CAST = {"boss": "tuber_0", "coder": "tuber_1", "hacker": "tuber_2"}
SLOTS = ["tuber_0", "tuber_1", "tuber_2"]
AIRING = "airing-abc"


def _roundtable_config(self_id="tuber_0"):
    return {
        "message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages",
                        "worker_id": self_id},
        "agent": {"role": "roundtable"},
        "layout": {"preset": "roundtable"},
    }


def _character_config(self_id="tuber_1"):
    return {
        "message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages",
                        "worker_id": self_id},
        "agent": {"role": "coder"},
        "layout": {"preset": "coder"},
    }


@pytest.fixture
def library(monkeypatch):
    script = {
        "source": "round_ep",
        "events": [
            {"type": "user_message", "text": "ship the login fix"},
            {"type": "assistant_text", "text": "on it boss"},
        ],
    }
    monkeypatch.setattr(replay_pane.episode_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane.episode_store, "load_episode",
                        lambda name: {"round_ep": script}.get(name))
    return script


@pytest.fixture
def fake_performer(monkeypatch):
    FakePerformer.instances = []
    monkeypatch.setattr(replay_pane, "Performer", FakePerformer)
    return FakePerformer


@pytest.fixture
def duet_timeouts(monkeypatch):
    monkeypatch.setattr(replay_pane, "REPLAY_READY_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(replay_pane, "REPLAY_READY_TIMEOUT_DEFAULT_S", 0.1)


@pytest.fixture
def relay_files(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAY_CUE_FILE", str(tmp_path / "duet_cue.json"))
    monkeypatch.setenv("REPLAY_READY_FILE", str(tmp_path / "duet_ready.json"))
    monkeypatch.setenv("REPLAY_STOP_FILE", str(tmp_path / "duet_stop.json"))
    return {"ready_file": tmp_path / "duet_ready.json"}


@pytest.fixture
def relay_dir(tmp_path, monkeypatch):
    """The GM's tile relay dir (TILE_RELAY_DIR, read by
    tile_pane.resolve_relay_dir)."""
    d = tmp_path / "tiles"
    d.mkdir()
    monkeypatch.setenv("TILE_RELAY_DIR", str(d))
    monkeypatch.delenv("LAYOUT_PRESET", raising=False)
    return d


@pytest.fixture
def airing(monkeypatch):
    """Everything between "the request arrives" and "the airing exists",
    stubbed: no TTS, no Kafka publish, no Postgres."""
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", _fake_voiced_show)
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **kw: "msg-1")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **kw: AIRING)


def _become_ready(monkeypatch, relay_files, airing_id=AIRING, workers=("tuber_1", "tuber_2")):
    """The ready_file must land AFTER the director starts waiting (it clears
    any pre-existing one first), so simulate followers' replay_ready arriving
    mid-poll via the sleep hook — same trick tests/test_replay_pane.py uses."""
    def fake_sleep(seconds):
        relay_files["ready_file"].write_text(
            json.dumps({"airing_id": airing_id, "workers": list(workers)}), encoding="utf-8")
    monkeypatch.setattr(replay_pane.time, "sleep", fake_sleep)


def _run_director(config=None, request=None, self_id="tuber_0"):
    return perform_director_request(
        request or {"episode": "round_ep", "cast": CAST, "speed": 1000},
        "tuber_0", None, self_id, config=config or _roundtable_config(self_id))


def _cue(relay_dir, slot):
    return json.loads(Path(tile_pane.tile_cue_file(relay_dir, slot)).read_text(encoding="utf-8"))


def _request(relay_dir, slot):
    return json.loads(
        Path(tile_pane.tile_request_file(relay_dir, slot)).read_text(encoding="utf-8"))


# ── _resolve_local_tiles: the opt-in gate ────────────────────────────────────
def test_resolve_local_tiles_returns_every_cast_slot_for_the_roundtable(relay_dir):
    """Including the director's OWN slot: the GM is a character whose lines
    are played by its TILE, not by the director process (§1.1/§4.1)."""
    assert replay_pane._resolve_local_tiles(CAST, _roundtable_config()) == SLOTS


def test_resolve_local_tiles_accepts_role_or_preset_or_env(relay_dir, monkeypatch):
    role_only = {"agent": {"role": "roundtable"}}
    preset_only = {"layout": {"preset": "roundtable"}}
    shorthand = {"layout": "roundtable"}
    assert replay_pane._resolve_local_tiles(CAST, role_only) == SLOTS
    assert replay_pane._resolve_local_tiles(CAST, preset_only) == SLOTS
    assert replay_pane._resolve_local_tiles(CAST, shorthand) == SLOTS
    monkeypatch.setenv("LAYOUT_PRESET", "roundtable")
    assert replay_pane._resolve_local_tiles(CAST, {"agent": {"role": "coder"}}) == SLOTS


def test_resolve_local_tiles_empty_for_a_character_worker(relay_dir):
    """THE regression guard for the six live channels: even with a relay dir
    configured, a non-roundtable worker drives no tiles."""
    assert replay_pane._resolve_local_tiles(CAST, _character_config()) == []


def test_resolve_local_tiles_empty_without_a_relay_dir(monkeypatch):
    monkeypatch.delenv("TILE_RELAY_DIR", raising=False)
    monkeypatch.delenv("LAYOUT_PRESET", raising=False)
    assert replay_pane._resolve_local_tiles(CAST, _roundtable_config()) == []


# ── cue fan-out ──────────────────────────────────────────────────────────────
def test_pure_local_roundtable_airs_without_any_ready_file_ever_written(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    """Regression guard for the real production bug: with CAST fully local
    (every slot resolves to a tile), followers is empty and tile_pane.py
    NEVER writes a replay_ready state (it has no bus access) — so the
    ready_file is never created at all. The show must still air immediately
    instead of polling for REPLAY_READY_TIMEOUT_S and refusing. Deliberately
    does NOT call _become_ready()."""
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    assert _run_director() is True
    assert len(FakePerformer.instances) == 1


def test_director_cues_every_tile_once_per_scene_in_order(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch):
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    _become_ready(monkeypatch, relay_files)

    written = []  # (slot, payload) in write order, captured live — the final
                  # file only ever shows the LAST write per slot.
    real = replay_pane._atomic_write_json

    def spy(path, data):
        written.append((Path(path).name, json.loads(json.dumps(data))))
        real(path, data)

    monkeypatch.setattr(replay_pane, "_atomic_write_json", spy)

    FakePerformer.instances = []
    monkeypatch.setattr(replay_pane, "Performer", FakePerformer)

    assert _run_director() is True

    for slot in SLOTS:
        cues = [d for name, d in written
                if name == f"{slot}.cue.json" and d.get("type") == "cue"]
        assert [c["scene_index"] for c in cues] == [0, 1]
        assert all(c["airing_id"] == AIRING for c in cues)


def test_director_writes_tile_requests_once_up_front_before_any_cue(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    _become_ready(monkeypatch, relay_files)

    written = []
    real = replay_pane._atomic_write_json

    def spy(path, data):
        written.append((Path(path).name, json.loads(json.dumps(data))))
        real(path, data)

    monkeypatch.setattr(replay_pane, "_atomic_write_json", spy)

    assert _run_director() is True

    names = [name for name, _ in written]
    first_cue = names.index(f"{SLOTS[0]}.cue.json")
    for slot in SLOTS:
        requests = [d for name, d in written if name == f"{slot}.request.json"]
        assert len(requests) == 1, f"{slot} must be invited exactly once"
        assert requests[0] == {
            "airing_id": AIRING, "episode": "round_ep", "cast": CAST,
            "speed": 1000.0, "worker_name": "tuber_0",
        }
        assert names.index(f"{slot}.request.json") < first_cue
        # ...and the file on disk still holds it after the show.
        assert _request(relay_dir, slot)["airing_id"] == AIRING


# ── the regression guard: character workers write nothing ────────────────────
def test_character_worker_writes_no_relay_files_at_all(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    """A worker whose preset/role is not the roundtable must behave exactly as
    it did before this wire existed — the six live channels depend on it."""
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    _become_ready(monkeypatch, relay_files, workers=("tuber_0", "tuber_2"))

    ok = _run_director(config=_character_config("tuber_1"), self_id="tuber_1")

    assert ok is True
    assert sorted(p.name for p in relay_dir.iterdir()) == []
    # ...and the Kafka path is untouched.
    assert [m["type"] for m in holder["producer"].sent].count("replay_cue") == 4


def test_worker_without_relay_dir_writes_nothing_and_creates_no_dir(
        library, airing, relay_files, duet_timeouts, monkeypatch, fake_performer, tmp_path):
    monkeypatch.delenv("TILE_RELAY_DIR", raising=False)
    monkeypatch.delenv("LAYOUT_PRESET", raising=False)
    # If the gate leaked, resolve_relay_dir()'s default would be used instead.
    monkeypatch.setattr(tile_pane, "DEFAULT_RELAY_DIR", str(tmp_path / "never"))
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    _become_ready(monkeypatch, relay_files)

    assert _run_director() is True
    assert not (tmp_path / "never").exists()


# ── stale-state hygiene ──────────────────────────────────────────────────────
def test_director_clears_stale_cue_and_request_files_before_the_show(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch):
    """A leftover cue/end from a PREVIOUS airing is read on the tile's very
    first poll — either aborting the new show instantly (stale "end") or
    fast-forwarding it into the catch-up path where owned audio is discarded.
    Same class of bug the director already guards against for
    cue_file/ready_file/stop_file."""
    for slot in SLOTS:
        Path(tile_pane.tile_cue_file(relay_dir, slot)).write_text(
            json.dumps({"airing_id": "airing-OLD", "type": "end"}), encoding="utf-8")
        Path(tile_pane.tile_request_file(relay_dir, slot)).write_text(
            json.dumps({"airing_id": "airing-OLD", "episode": "old_ep", "cast": {}}),
            encoding="utf-8")

    seen = {}  # what the relay files looked like at the moment cueing began

    class SnapshottingPerformer(FakePerformer):
        def perform(self, script, show=None, start=0, limit=None):
            for slot in SLOTS:
                # The stale "end" must be GONE, not merely overwritten later:
                # a tile polling before scene 0's cue would have consumed it.
                seen[slot] = (Path(tile_pane.tile_cue_file(relay_dir, slot)).exists(),
                              _request(relay_dir, slot))
            return super().perform(script, show=show)

    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    _become_ready(monkeypatch, relay_files)
    FakePerformer.instances = []
    monkeypatch.setattr(replay_pane, "Performer", SnapshottingPerformer)

    deleted = []
    real_delete = replay_pane._delete_stale_file

    def spy_delete(path):
        deleted.append(Path(path).name)
        real_delete(path)

    monkeypatch.setattr(replay_pane, "_delete_stale_file", spy_delete)

    assert _run_director() is True

    for slot in SLOTS:
        assert f"{slot}.cue.json" in deleted
        assert f"{slot}.request.json" in deleted
        # Nothing from the old airing survived into this show.
        assert seen[slot][0] is False, "stale cue file still present at show start"
        assert seen[slot][1]["airing_id"] == AIRING
        assert seen[slot][1]["episode"] == "round_ep"
    assert "airing-OLD" not in json.dumps(seen)


# ── end cues ─────────────────────────────────────────────────────────────────
def test_director_writes_end_cue_to_every_tile_on_a_normal_finish(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    _become_ready(monkeypatch, relay_files)

    assert _run_director() is True

    for slot in SLOTS:
        assert _cue(relay_dir, slot) == {"airing_id": AIRING, "type": "end"}


def test_director_writes_end_cue_to_every_tile_on_a_replay_stop_refusal(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    """A refusal after the tiles were kicked off must release them, or each
    one sits polling until its watchdog fires.

    With a pure-local roundtable cast (CAST — every slot resolves to a local
    tile), `followers` is empty and the ready-wait can never time out on its
    own (an empty set is trivially satisfied). The realistic way a
    local-only roundtable show still refuses after inviting its tiles is an
    operator replay_stop landing before the (already-satisfied) ready
    check — this test exercises that path instead of an unreachable
    kafka-follower timeout."""
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    stop_file = Path(os.environ["REPLAY_STOP_FILE"])

    # With followers empty (pure-local cast) the ready-wait never loops, so
    # there's no sleep hook to write the stop file into. Instead, simulate
    # an operator stop landing in the gap right after the director's own
    # stale-state hygiene clears any pre-existing stop file.
    real_delete = replay_pane._delete_stale_file

    def delete_then_maybe_restop(path):
        real_delete(path)
        if Path(path) == stop_file:
            stop_file.write_text(json.dumps({"stopped_by": "operator", "stopped_at": 0}),
                                 encoding="utf-8")

    monkeypatch.setattr(replay_pane, "_delete_stale_file", delete_then_maybe_restop)

    assert _run_director() is False
    assert FakePerformer.instances == []  # never performed

    for slot in SLOTS:
        assert _cue(relay_dir, slot) == {"airing_id": AIRING, "type": "end"}
    # CAST is fully local (every slot resolves to a tile) — there are no
    # Kafka followers to send replay_end to at all.
    ends = [m for m in holder["producer"].sent if m["type"] == "replay_end"]
    assert ends == []


def test_early_refusal_before_the_roster_exists_does_not_raise(
        library, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    """The narration store is gone: refuse() fires long before the tile
    roster/relay dir have been resolved. It must return False cleanly and
    write nothing — nothing has been told to start yet."""
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: False)
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    assert _run_director() is False
    assert sorted(p.name for p in relay_dir.iterdir()) == []


# ── atomicity ────────────────────────────────────────────────────────────────
def test_relay_writes_are_atomic_and_leave_no_tmp_files(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch):
    """A tile polling a half-written cue must never see partial JSON. The
    reader below runs between every write and parses whatever is on disk."""
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    _become_ready(monkeypatch, relay_files)
    FakePerformer.instances = []
    monkeypatch.setattr(replay_pane, "Performer", FakePerformer)

    real = replay_pane._atomic_write_json

    def spy(path, data):
        real(path, data)
        # Stand-in for a concurrently polling tile: every relay file present
        # at this instant must be complete, parseable JSON.
        for p in relay_dir.iterdir():
            assert not p.name.endswith(".tmp"), f"partial file visible: {p.name}"
            json.loads(p.read_text(encoding="utf-8"))

    monkeypatch.setattr(replay_pane, "_atomic_write_json", spy)

    assert _run_director() is True

    names = sorted(p.name for p in relay_dir.iterdir())
    assert not any(n.endswith(".tmp") for n in names)
    assert names == sorted([f"{s}.cue.json" for s in SLOTS]
                           + [f"{s}.request.json" for s in SLOTS])
    for p in relay_dir.iterdir():
        json.loads(p.read_text(encoding="utf-8"))


def test_relay_write_failure_never_takes_the_show_down(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch,
        fake_performer, capsys):
    """Best-effort, like _safe_send: an unwritable relay file is reported on
    stderr and the show goes on (the tile's watchdog is the backstop)."""
    def boom(path, data):
        raise OSError("disk on fire")

    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    _become_ready(monkeypatch, relay_files)
    monkeypatch.setattr(replay_pane, "_atomic_write_json", boom)

    assert _run_director() is True
    assert "tile relay write failed" in capsys.readouterr().err


# ── audio ownership: exactly one performer per line (double-play regression) ──
class _Audio:
    """Minimal Narration stand-in: the director's ownership loop only reads
    .duration, and _perform_scene's owned branch is what the fix targets."""

    def __init__(self, duration):
        self.audio_path = Path("scene.wav")
        self.duration = duration


def test_director_is_silent_on_a_line_its_own_tile_will_play(
        library, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    """THE double-play regression: with the roundtable cast {boss: tuber_0,
    coder: tuber_1}, the old formula (cast.get(speaker, self_id) == self_id)
    gave the DIRECTOR (tuber_0) owned=True on the `boss` line while tile
    tuber_0 ALSO owned it — two paplays of the same line into one vout sink,
    the 'characters talking over each other' heard on air. After the fix the
    director must strip its own audio for every line the cast maps to a tile
    slot, and keep only target_duration for pacing (it is silent, §4.1)."""
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    def voiced(script, config, workdir, **kwargs):
        return [
            {"kind": "boss", "speaker": "boss", "narration": "gm line",
             "events": [{"type": "assistant_text", "text": "gm line"}],
             "audio": _Audio(1.5)},
            {"kind": "coder_talk", "speaker": "coder", "narration": "coder line",
             "events": [{"type": "assistant_text", "text": "coder line"}],
             "audio": _Audio(2.5)},
        ]

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", voiced)
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **kw: "msg-1")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **kw: AIRING)
    _become_ready(monkeypatch, relay_files)

    assert _run_director() is True
    show = FakePerformer.instances[-1].performed_show
    gm_line = next(s for s in show if s["speaker"] == "boss")
    coder_line = next(s for s in show if s["speaker"] == "coder")
    # Neither line is owned by the director — both are tile-mapped.
    assert gm_line["owned"] is False and gm_line["audio"] is None
    assert coder_line["owned"] is False and coder_line["audio"] is None
    # ...but target_duration IS kept, so the director's silent clock still
    # paces the tiles correctly (the cue ratchet depends on it).
    assert gm_line["target_duration"] == 1.5
    assert coder_line["target_duration"] == 2.5


def test_director_still_owns_a_line_no_tiles_are_cast_for(
        library, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    """Counterpart of the double-play fix, for a NON-roundtable duet (the
    director has no local tile at all — a normal visible replay pane): a
    speaker the cast does NOT map to any slot (e.g. a header narrator
    'narrator', or the header-less 'boss' in a two-speaker duet where the
    cast is {coder, tester}) must stay owned by the DIRECTOR, or that line
    would be heard on NO channel. The fix must silence the director only
    for lines a tile actually plays — here we prove it by casting a
    speaker to no slot at all (speaker 'narrator' absent from CAST, whose
    values are all local tiles), with self_id NOT itself a tile slot."""
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    def voiced(script, config, workdir, **kwargs):
        return [
            # 'narrator' is NOT in CAST → cast.get falls back to self_id and
            # cast.get("narrator") is None (not a tile) → director owns it.
            {"kind": "boss", "speaker": "narrator", "narration": "narration",
             "events": [{"type": "assistant_text", "text": "narration"}],
             "audio": _Audio(1.0)},
            {"kind": "coder_talk", "speaker": "coder", "narration": "tile line",
             "events": [{"type": "assistant_text", "text": "tile line"}],
             "audio": _Audio(2.0)},
        ]

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", voiced)
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **kw: "msg-1")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **kw: AIRING)
    _become_ready(monkeypatch, relay_files)

    # Non-roundtable config (character role/preset) — _resolve_local_tiles
    # returns [] regardless of self_id, so the director has no tile at all
    # here, matching the original (pre-fix) test shape exactly.
    assert _run_director(config=_character_config()) is True
    show = FakePerformer.instances[-1].performed_show
    narration = next(s for s in show if s["speaker"] == "narrator")
    tile_line = next(s for s in show if s["speaker"] == "coder")
    assert narration["owned"] is True and narration["audio"] is not None
    assert tile_line["owned"] is False and tile_line["audio"] is None


def test_uncast_speaker_routes_to_directors_own_tile_on_the_roundtable(
        library, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    """The fix for 'the very first voice showed no text': on the roundtable
    the director IS a tile slot (tuber_0 hosts its own tile). An episode
    with a speaker the cast never explicitly maps (revoice.plan_scenes
    defaults a speaker-less user_message to "boss") must NOT fall through
    to the invisible director pane the way it does in a plain duet —
    it must be treated as if cast to the director's OWN slot, so tuber_0's
    tile actually plays AND displays it. Before this fix such a line was
    heard (director still owned it per the old formula) but shown on no
    tile at all — several of these lines open both shipped campaign
    episodes (ashiorid, ashiorid_generated_ce8d), so the audience heard
    the very first line of the show with nothing on screen for it."""
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    def voiced(script, config, workdir, **kwargs):
        return [
            # 'boss' is NOT in CAST (mirrors a speaker-less user_message
            # event, which plan_scenes defaults to speaker="boss").
            {"kind": "boss", "speaker": "boss", "narration": "opening narration",
             "events": [{"type": "assistant_text", "text": "opening narration"}],
             "audio": _Audio(1.0)},
            {"kind": "coder_talk", "speaker": "coder", "narration": "tile line",
             "events": [{"type": "assistant_text", "text": "tile line"}],
             "audio": _Audio(2.0)},
        ]

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", voiced)
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **kw: "msg-1")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **kw: AIRING)
    _become_ready(monkeypatch, relay_files)

    # self_id="tuber_0" (the default _run_director self_id) IS one of
    # CAST's tile-mapped values — the roundtable shape.
    assert _run_director() is True
    show = FakePerformer.instances[-1].performed_show
    narration = next(s for s in show if s["speaker"] == "boss")
    tile_line = next(s for s in show if s["speaker"] == "coder")
    # The director stays silent for BOTH lines now — tuber_0's own tile
    # (not the hidden director pane) owns the uncast "boss" narration,
    # exactly like it owns any other speaker explicitly cast to it.
    assert narration["owned"] is False and narration["audio"] is None
    assert tile_line["owned"] is False and tile_line["audio"] is None
    # And the tile request payload actually written to the relay carries
    # the rerouted mapping, so tuber_0's own make_owns() agrees and really
    # plays/displays the line instead of silently disagreeing with
    # scene["owned"].
    request = json.loads((relay_dir / "tuber_0.request.json").read_text())
    assert request["cast"].get("boss") == "tuber_0"


# ── voice gate escape hatch: show.audio flows into the gate ──────────────────
def test_build_voice_gate_honours_show_audio_header(monkeypatch, tmp_path):
    """show.audio.max_concurrent=N (the deliberate-overlap escape hatch) must
    resolve to N seats; the default (no audio block) must stay at 1 seat —
    one voice at a time, so two lines can never sound together. Same gate
    dir for all holders, so the seat count really bounds concurrency."""
    monkeypatch.setenv("VOICE_GATE_DIR", str(tmp_path))

    gate_off, gap_off = replay_pane.build_voice_gate(
        {"show": {"slots": ["tuber_0"]}}, None, tag="default")
    gate_on, gap_on = replay_pane.build_voice_gate(
        {"show": {"slots": ["tuber_0"], "audio": {"max_concurrent": 2,
                                                   "line_gap_s": 0.25}}},
        None, tag="duet")

    assert gate_off.seats == 1 and gap_off == 0.0
    assert gate_on.seats == 2 and gap_on == 0.25
    assert Path(gate_off.gate_dir) == Path(gate_on.gate_dir) == Path(
        os.environ["VOICE_GATE_DIR"])

    # Two seats: exactly two lines may sound together, the third waits.
    s1 = gate_on.acquire()
    s2 = gate_on.acquire()
    assert {s1.seat, s2.seat} == {0, 1}
    assert voice_gate_probe_acquire(gate_on) is None  # both taken — blocked
    s1.release()
    s2.release()

    # One-seat default: exactly one line at a time (the no-overlap guarantee).
    first = gate_off.acquire()
    assert first is not None
    assert voice_gate_probe_acquire(gate_off) is None  # second is blocked
    first.release()
    assert gate_off.acquire() is not None  # free again after release


def voice_gate_probe_acquire(gate):
    """Acquire via a fresh gate with a short timeout so a 'blocked' test
    can't hang on a contended seat."""
    probe = type(gate)(gate.gate_dir, seats=gate.seats,
                       acquire_timeout_s=0.15, tag="probe")
    return probe.acquire()


def test_build_voice_gate_degrades_when_dir_unusable(monkeypatch, tmp_path, capsys):
    """The gate must never withhold audio: an unusable dir degrades to
    (None, gap) so the show runs exactly as before."""
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("VOICE_GATE_DIR", str(blocker / "nope"))
    gate, gap = replay_pane.build_voice_gate({"show": {"slots": ["tuber_0"]}}, None)
    assert gate is None and gap == 0.0


# ── coexistence with the Kafka fan-out ───────────────────────────────────────
def test_roundtable_cast_never_reaches_kafka_even_with_tile_relay_failures(
        library, airing, relay_dir, relay_files, duet_timeouts, monkeypatch, fake_performer):
    """_resolve_local_tiles gates on the WORKER (self), not per-slot — so for
    a roundtable director every cast value becomes a local tile, and the
    Kafka follower/invite/cue/end path is never touched at all. (The
    Kafka-only path — a non-roundtable worker — is covered separately by
    test_character_worker_writes_no_relay_files_at_all.)"""
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    _become_ready(monkeypatch, relay_files)

    assert _run_director() is True

    sent = holder["producer"].sent
    assert [m for m in sent if m["type"] in ("replay_invite", "replay_cue", "replay_end")] == []
