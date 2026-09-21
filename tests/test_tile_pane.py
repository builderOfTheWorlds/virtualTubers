"""Tests for app/tile_pane.py — the roundtable tile pane (design spec
.claude/prompts/roundtable_stream_design.md §5 / WP-3).

Fully mocked: no Kafka, no Postgres, no real audio, no piper. The real
Performer is replaced by the same kind of recording stand-ins
tests/test_replay_pane.py uses for the duet paths, so nothing here touches
timing, pacing or playback.

The load-bearing properties under test:
  * a tile plays audio ONLY for the scenes its slot owns (the follower
    contract, reused rather than reimplemented);
  * the local cue ratchet obeys exactly the duet cue protocol;
  * stale relay files from a previous airing are cleared BEFORE performing;
  * every failure degrades to idle instead of raising out of the pane loop;
  * two tiles write to DIFFERENT avatar state files (§5.1 / review finding
    G2 — the whole reason this module exists instead of reusing avatar.py).
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import replay_pane  # noqa: E402
import tile_pane  # noqa: E402
import replay as _replay  # noqa: E402  (real Performer path, for the end-to-end ownership test)
from agent_state import read_state  # noqa: E402
from tile_pane import (  # noqa: E402
    clear_stale_relay_files,
    draw_idle_screen,
    handle_once,
    make_owns,
    make_wait_for_scene,
    perform_tile_request,
    render_tile,
    resolve_relay_dir,
    tile_cue_file,
    tile_request_file,
    tile_state_file,
)


# ── stand-ins ────────────────────────────────────────────────────────────────
class FakePerformer:
    """Records constructor kwargs and the show it was handed; perform() is a
    no-op walk so nothing real gets timed, paced or played."""
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.performed_show = None
        self.performed_script = None
        FakePerformer.instances.append(self)

    def perform(self, script, show=None, start=0, limit=None):
        self.performed_script = script
        self.performed_show = show
        # Write avatar state the way the real Performer._avatar does, so the
        # per-tile state-path guard exercises a real write_state call.
        state_path = self.kwargs.get("state_path")
        if state_path:
            tile_pane.write_tile_state(state_path, "speaking",
                                       action=f"performing {len(show or [])} scenes")
        return True


@pytest.fixture
def fake_performer(monkeypatch):
    FakePerformer.instances = []
    monkeypatch.setattr(tile_pane, "Performer", FakePerformer)
    return FakePerformer


@pytest.fixture
def tile_library(monkeypatch):
    """In-memory episode_store stand-in holding one two-scene episode —
    same shape as test_replay_pane.py's duet_library."""
    script = {
        "source": "round_ep",
        "events": [
            {"type": "user_message", "text": "open the session"},
            {"type": "assistant_text", "text": "on it"},
        ],
    }
    scripts = {"round_ep": script}
    monkeypatch.setattr(replay_pane.episode_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane.episode_store, "load_episode", lambda name: scripts.get(name))
    monkeypatch.setattr(replay_pane.episode_store, "list_episodes", lambda: sorted(scripts))
    return scripts


def _rows(boss_audio=None, coder_audio=None, boss_duration=None, coder_duration=None):
    """Rows shaped like narration_store.load_airing(), matching
    tile_library's plan_scenes() output (scene 0 = boss, scene 1 = coder)."""
    return [
        {"scene_index": 0, "scene_kind": "boss", "speaker": "boss", "text": "boss line",
         "audio": boss_audio, "audio_duration_s": boss_duration},
        {"scene_index": 1, "scene_kind": "coder_talk", "speaker": "coder", "text": "coder line",
         "audio": coder_audio, "audio_duration_s": coder_duration},
    ]


@pytest.fixture
def store(monkeypatch):
    """narration_store stand-in: available, serving whatever rows the test
    parks in holder['rows'] (None ⇒ missing airing)."""
    holder = {"rows": _rows(), "raises": None}

    def load_airing(airing_id):
        if holder["raises"] is not None:
            raise holder["raises"]
        return holder["rows"]

    monkeypatch.setattr(tile_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(tile_pane.narration_store, "load_airing", load_airing)
    return holder


@pytest.fixture
def relay(tmp_path, monkeypatch):
    """A relay dir plus a stop-file path that does NOT exist, so the shared
    operator replay_stop signal can't leak in from the host /tmp."""
    d = tmp_path / "tiles"
    d.mkdir()
    monkeypatch.setenv("REPLAY_STOP_FILE", str(tmp_path / "no_such_stop.json"))
    return d


@pytest.fixture
def fast_cues(monkeypatch):
    """Tiny cue-protocol timeouts so the watchdog resolves near-instantly.
    Patched on replay_pane because that is where tile_pane reads them from —
    proof in itself that the constants are shared, not redefined."""
    monkeypatch.setattr(replay_pane, "REPLAY_CUE_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(replay_pane, "REPLAY_FIRST_CUE_TIMEOUT_S", 0.1)
    monkeypatch.setattr(replay_pane, "REPLAY_WATCHDOG_MIN_S", 0.1)
    monkeypatch.setattr(replay_pane, "REPLAY_WATCHDOG_GRACE_S", 0.05)


CAST = {"boss": "tuber_0", "coder": "tuber_2"}


def _request(**over):
    request = {"airing_id": "airing-1", "episode": "round_ep", "cast": dict(CAST),
               "speed": 1000, "worker_name": "Vex"}
    request.update(over)
    return request


# ── relay path resolution ────────────────────────────────────────────────────
def test_relay_paths_are_per_slot(monkeypatch):
    monkeypatch.delenv("TILE_RELAY_DIR", raising=False)
    assert resolve_relay_dir() == "/tmp/tiles"
    monkeypatch.setenv("TILE_RELAY_DIR", "/tmp/elsewhere")
    assert resolve_relay_dir() == "/tmp/elsewhere"
    assert resolve_relay_dir("/explicit") == "/explicit"  # flag beats env

    assert tile_request_file("/r", "tuber_2") == "/r/tuber_2.request.json"
    assert tile_cue_file("/r", "tuber_2") == "/r/tuber_2.cue.json"
    assert tile_state_file("/r", "tuber_2") == "/r/tuber_2.state.json"


# ── the owns predicate ───────────────────────────────────────────────────────
def test_owns_predicate_matches_only_this_slots_speakers():
    owns = make_owns(CAST, "tuber_2")
    assert owns({"speaker": "coder"}, None) is True
    assert owns({"speaker": "boss"}, None) is False
    assert owns({"speaker": "nobody"}, None) is False   # uncast speaker
    assert owns({}, None) is False                      # speakerless scene
    assert make_owns(None, "tuber_2")({"speaker": "coder"}) is False


def test_owned_scenes_keep_audio_and_unowned_scenes_are_stripped(
        tile_library, store, relay, fake_performer, fast_cues):
    """The follower contract, exercised through the real
    replay_pane._rebuild_scenes_from_rows: only this slot's audio bytes ever
    reach this tile, but pacing data survives for every scene."""
    store["rows"] = _rows(boss_audio=b"boss-wav", coder_audio=b"coder-wav",
                          boss_duration=1.5, coder_duration=2.5)

    ok = perform_tile_request(_request(), "tuber_2", str(relay))

    assert ok is True
    show = FakePerformer.instances[0].performed_show
    boss = next(s for s in show if s["speaker"] == "boss")
    coder = next(s for s in show if s["speaker"] == "coder")
    assert boss["owned"] is False
    assert boss["audio"] is None            # never written to this tile's dir
    assert boss["target_duration"] == 1.5   # still paced to the owner's timing
    assert coder["owned"] is True
    assert coder["audio"] is not None
    assert coder["audio"].duration == 2.5
    assert coder["narration"] == "coder line"  # the director's text, not regenerated


def test_a_different_slot_owns_the_other_scene(
        tile_library, store, relay, fake_performer, fast_cues):
    """Same airing, different tile: ownership flips. One code path, seven
    tiles."""
    store["rows"] = _rows(boss_audio=b"boss-wav", coder_audio=b"coder-wav",
                          boss_duration=1.0, coder_duration=1.0)

    assert perform_tile_request(_request(), "tuber_0", str(relay)) is True
    show = FakePerformer.instances[0].performed_show
    boss = next(s for s in show if s["speaker"] == "boss")
    coder = next(s for s in show if s["speaker"] == "coder")
    assert boss["owned"] is True and boss["audio"] is not None
    assert coder["owned"] is False and coder["audio"] is None


# ── the cue ratchet ──────────────────────────────────────────────────────────
def _ratchet(relay, fast_cues_unused=None, show=None):
    show = show or [{"target_duration": 1.0}, {"target_duration": 1.0}]
    cue_file = tile_cue_file(str(relay), "tuber_2")
    return make_wait_for_scene(cue_file, "airing-1", show, "tuber_2"), Path(cue_file)


def test_cue_ratchet_returns_index_on_matching_cue(relay, fast_cues):
    wait, cue_file = _ratchet(relay)
    cue_file.write_text(json.dumps({"airing_id": "airing-1", "type": "cue", "scene_index": 1}),
                        encoding="utf-8")
    assert wait(1) == 1
    # A cue AHEAD of us authorizes the jump (Performer's catch-up rule).
    cue_file.write_text(json.dumps({"airing_id": "airing-1", "type": "cue", "scene_index": 3}),
                        encoding="utf-8")
    assert wait(1) == 3


def test_cue_ratchet_returns_minus_one_on_end_cue(relay, fast_cues):
    wait, cue_file = _ratchet(relay)
    cue_file.write_text(json.dumps({"airing_id": "airing-1", "type": "end",
                                    "reason": "finished"}), encoding="utf-8")
    assert wait(0) == -1


def test_cue_ratchet_returns_minus_one_on_watchdog_timeout(relay, fast_cues, capsys):
    wait, cue_file = _ratchet(relay)
    assert not cue_file.exists()
    assert wait(0) == -1          # first-cue timeout
    assert wait(1) == -1          # per-scene watchdog
    assert "watchdog timed out" in capsys.readouterr().err


def test_cue_ratchet_ignores_a_cue_for_another_airing(relay, fast_cues):
    wait, cue_file = _ratchet(relay)
    cue_file.write_text(json.dumps({"airing_id": "some-other-airing", "type": "cue",
                                    "scene_index": 5}), encoding="utf-8")
    assert wait(0) == -1  # never authorized; the watchdog is what fires


def test_cue_ratchet_ignores_a_cue_below_the_requested_index(relay, fast_cues):
    """A stale cue for an EARLIER scene must never authorize a later one —
    the ratchet only ever moves forward."""
    wait, cue_file = _ratchet(relay)
    cue_file.write_text(json.dumps({"airing_id": "airing-1", "type": "cue", "scene_index": 0}),
                        encoding="utf-8")
    assert wait(1) == -1


def test_cue_ratchet_ignores_malformed_cue_payloads(relay, fast_cues):
    wait, cue_file = _ratchet(relay)
    for payload in ['{"airing_id": "airing-1", "type": "cue", "scene_index": "1"}',
                    '{"airing_id": "airing-1", "type": "cue"}',
                    '{"airing_id": "airing-1"}',
                    "not json at all"]:
        cue_file.write_text(payload, encoding="utf-8")
        assert wait(0) == -1


def test_cue_ratchet_reuses_the_follower_timeout_constants(relay, monkeypatch):
    """Guard against someone re-adding tile-local timeout numbers: the tile
    must read replay_pane's constants at call time."""
    seen = []
    monkeypatch.setattr(replay_pane, "REPLAY_FIRST_CUE_TIMEOUT_S", 0.0)
    monkeypatch.setattr(replay_pane, "REPLAY_WATCHDOG_MIN_S", 0.0)
    monkeypatch.setattr(replay_pane, "REPLAY_WATCHDOG_GRACE_S", 0.0)
    monkeypatch.setattr(replay_pane, "REPLAY_CUE_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(tile_pane.time, "sleep", lambda s: seen.append(s))
    wait, _cue = _ratchet(relay)
    assert wait(0) == -1  # a zeroed timeout fires immediately
    assert not hasattr(tile_pane, "REPLAY_CUE_POLL_INTERVAL_S")
    assert not hasattr(tile_pane, "REPLAY_FIRST_CUE_TIMEOUT_S")


# ── stale-state hygiene ──────────────────────────────────────────────────────
def test_clear_stale_relay_files_removes_cue_and_request(relay):
    cue = Path(tile_cue_file(str(relay), "tuber_2"))
    req = Path(tile_request_file(str(relay), "tuber_2"))
    cue.write_text("{}", encoding="utf-8")
    req.write_text("{}", encoding="utf-8")
    other = Path(tile_cue_file(str(relay), "tuber_3"))
    other.write_text("{}", encoding="utf-8")

    clear_stale_relay_files(str(relay), "tuber_2")

    assert not cue.exists()
    assert not req.exists()
    assert other.exists()      # another tile's relay file is not ours to delete
    clear_stale_relay_files(str(relay), "tuber_2")  # idempotent, never raises


def test_stale_cue_from_a_previous_airing_is_cleared_before_performing(
        tile_library, store, relay, monkeypatch, fast_cues):
    """The real bug this guards: a leftover cue/end from the PREVIOUS show
    is read on the first poll of the new one and either aborts it instantly
    or fast-forwards it past its own audio."""
    cue = Path(tile_cue_file(str(relay), "tuber_2"))
    req = Path(tile_request_file(str(relay), "tuber_2"))
    cue.write_text(json.dumps({"airing_id": "old-airing", "type": "end"}), encoding="utf-8")
    req.write_text(json.dumps({"airing_id": "old-airing"}), encoding="utf-8")

    observed = {}

    class CheckingPerformer(FakePerformer):
        def perform(self, script, show=None, start=0, limit=None):
            observed["cue_exists_at_perform_time"] = cue.exists()
            observed["request_exists_at_perform_time"] = req.exists()
            return super().perform(script, show=show, start=start, limit=limit)

    FakePerformer.instances = []
    monkeypatch.setattr(tile_pane, "Performer", CheckingPerformer)

    assert perform_tile_request(_request(), "tuber_2", str(relay)) is True
    assert observed == {"cue_exists_at_perform_time": False,
                        "request_exists_at_perform_time": False}
    assert not cue.exists()  # and consumed again on the way out


# ── degradation: nothing raises out of the pane loop ─────────────────────────
def test_malformed_request_file_is_consumed_and_pane_returns_to_idle(
        tile_library, store, relay, fake_performer, capsys):
    req = Path(tile_request_file(str(relay), "tuber_2"))
    req.write_text("{ this is not json", encoding="utf-8")

    assert handle_once("tuber_2", str(relay)) is False

    assert not req.exists()                 # consumed, not left to wedge the pane
    assert FakePerformer.instances == []    # nothing performed
    assert "malformed" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("payload", [
    {},                                                    # nothing at all
    {"airing_id": "a1"},                                   # no episode
    {"episode": "round_ep"},                               # no airing_id
    {"airing_id": "a1", "episode": "round_ep", "cast": "nope"},  # cast not a dict
    {"airing_id": "a1", "episode": "not_in_library", "cast": dict(CAST)},
])
def test_unusable_requests_degrade_to_idle(tile_library, store, relay, fake_performer,
                                           capsys, payload):
    assert perform_tile_request(payload, "tuber_2", str(relay)) is False
    assert FakePerformer.instances == []
    assert capsys.readouterr().err  # the reason is always reported


def test_non_dict_request_degrades(tile_library, store, relay, fake_performer, capsys):
    assert perform_tile_request(["not", "a", "dict"], "tuber_2", str(relay)) is False
    assert FakePerformer.instances == []
    assert "malformed" in capsys.readouterr().err.lower()


def test_unavailable_narration_store_degrades(tile_library, relay, monkeypatch,
                                              fake_performer, capsys):
    monkeypatch.setattr(tile_pane.narration_store, "available", lambda: False)

    def boom(airing_id):
        raise AssertionError("must not touch the store when it is unavailable")

    monkeypatch.setattr(tile_pane.narration_store, "load_airing", boom)

    assert perform_tile_request(_request(), "tuber_2", str(relay)) is False
    assert FakePerformer.instances == []
    assert "narration store unavailable" in capsys.readouterr().err


def test_unreachable_narration_store_degrades(tile_library, store, relay,
                                              fake_performer, capsys):
    store["raises"] = RuntimeError("connection refused")
    assert perform_tile_request(_request(), "tuber_2", str(relay)) is False
    assert FakePerformer.instances == []
    assert "airing load failed" in capsys.readouterr().err


def test_missing_airing_degrades(tile_library, store, relay, fake_performer, capsys):
    store["rows"] = None
    assert perform_tile_request(_request(), "tuber_2", str(relay)) is False
    assert FakePerformer.instances == []
    assert "no cached airing" in capsys.readouterr().err


def test_airing_that_no_longer_matches_the_script_degrades(
        tile_library, store, relay, fake_performer, capsys):
    store["rows"] = [{"scene_index": 0, "scene_kind": "coder_talk", "speaker": "coder",
                      "text": "x", "audio": None, "audio_duration_s": None}]
    assert perform_tile_request(_request(), "tuber_2", str(relay)) is False
    assert FakePerformer.instances == []
    assert "no longer matches" in capsys.readouterr().err


def test_a_raising_performer_does_not_escape_handle_once(
        tile_library, store, relay, monkeypatch, capsys):
    class ExplodingPerformer(FakePerformer):
        def perform(self, script, show=None, start=0, limit=None):
            raise RuntimeError("tmux pane went away")

    FakePerformer.instances = []
    monkeypatch.setattr(tile_pane, "Performer", ExplodingPerformer)
    Path(tile_request_file(str(relay), "tuber_2")).write_text(
        json.dumps(_request()), encoding="utf-8")

    assert handle_once("tuber_2", str(relay)) is False  # degraded, not raised
    assert "show failed" in capsys.readouterr().err


def test_handle_once_with_no_request_is_a_no_op(tile_library, store, relay, fake_performer):
    assert handle_once("tuber_2", str(relay)) is False
    assert FakePerformer.instances == []


# ── per-tile avatar state (§5.1 / G2 regression guard) ───────────────────────
def test_two_tiles_write_to_different_state_files(
        tile_library, store, relay, fake_performer, fast_cues):
    """THE reason this module exists: avatar.py resolves one state path per
    container (AGENT_STATE_FILE env -> config -> /tmp/agent_state.json), so
    seven tiles would share one file and render seven identical faces. Each
    tile must own its own state path."""
    state_a = tile_state_file(str(relay), "tuber_2")
    state_b = tile_state_file(str(relay), "tuber_5")
    assert state_a != state_b

    perform_tile_request(_request(), "tuber_2", str(relay), state_path=state_a)
    perform_tile_request(_request(), "tuber_5", str(relay), state_path=state_b)

    paths = [inst.kwargs["state_path"] for inst in FakePerformer.instances]
    assert paths == [state_a, state_b]
    assert len(set(paths)) == 2

    # ...and both files really exist independently on disk.
    a, b = Path(state_a), Path(state_b)
    assert a.is_file() and b.is_file()
    assert json.loads(a.read_text(encoding="utf-8"))["expression"] == "speaking"
    assert json.loads(b.read_text(encoding="utf-8"))["expression"] == "speaking"


def test_tile_state_path_ignores_agent_state_file_env(monkeypatch, tmp_path):
    """A tile must NOT inherit the container-wide AGENT_STATE_FILE — that is
    exactly the collision agent_state.resolve_state_path would cause."""
    monkeypatch.setenv("AGENT_STATE_FILE", str(tmp_path / "shared.json"))
    assert tile_state_file("/tmp/tiles", "tuber_3") == "/tmp/tiles/tuber_3.state.json"


def test_write_tile_state_degrades_on_unwritable_path(capsys):
    assert tile_pane.write_tile_state("/nonexistent-dir/nope.json", "idle") is None
    assert "avatar state update skipped" in capsys.readouterr().err
    assert tile_pane.write_tile_state(None, "idle") is None  # no state path configured


# ── idle screen (§5: the roundtable never goes blank) ────────────────────────
def test_idle_screen_shows_a_neutral_listening_status(relay, capsys):
    """The slot/character name is NOT drawn inside the frame — it comes from
    the pane's own tmux border (build_layout.py's _resolve_tile_title) — so
    this only asserts the avatar+status contract, not a slot string."""
    state_path = tile_state_file(str(relay), "tuber_4")
    lines = draw_idle_screen("tuber_4", state_path)

    body = "\n".join(lines)
    assert "status: listening" in body
    for face_row in tile_pane.TILE_FACES["idle"]:
        assert face_row.strip() in body
    assert json.loads(Path(state_path).read_text(encoding="utf-8"))["expression"] == "idle"
    assert capsys.readouterr().out.endswith(body + "\n")


def test_tile_render_fits_the_detected_pane_height(relay):
    """v1.4: the text subpanel grows to fill the pane instead of a fixed 2
    lines, so this asserts the CONTRACT (exact row count for a given detected
    height + width, never an unbounded/wrapped line) rather than the now
    outdated fixed 15-16 row budget."""
    width, height = 36, 20
    lines = render_tile("tuber_6", expression="speaking",
                        line="a very long spoken line " * 20, status="speaking",
                        width=width, height=height)
    assert len(lines) == height
    assert max(len(line) for line in lines) <= width


def test_render_tile_falls_back_for_an_unknown_expression():
    lines = render_tile("tuber_1", expression="wildly_unknown")
    assert any("_" in line for line in lines)  # still drew a face, no KeyError


def test_every_expression_the_performer_writes_has_its_own_face():
    """replay.Performer._avatar writes these; a missing entry silently fell
    back to the idle face, so a tile looked asleep through most of a show."""
    for expression in ("speaking", "listening", "idle", "thinking",
                       "focused", "frustrated"):
        assert expression in tile_pane.TILE_FACES


# ── avatar + text + status subpanels (the broadcast tile contract, v1.4) ─────
def _dialogue_rows(lines, height=None):
    """The TEXT subpanel, located structurally: top, name, name/avatar-divider,
    avatar (TILE_AVATAR_LINES), avatar/text-divider, TEXT rows,
    text/status-divider, status, bottom. `height` must match what the frame
    was rendered at so the dialogue-row count lines up with
    resolve_dialogue_line_count(height)."""
    n = tile_pane.resolve_dialogue_line_count(height)
    return lines[-(n + 3):-3]


def test_tile_text_subpanel_fills_the_detected_height(relay):
    """The TEXT subpanel grows/shrinks with the pane's height and stays a
    FIXED size for a given height in every state, so a tile that starts
    talking can never shove its neighbours around on air."""
    width, height = 40, 24
    empty = render_tile("tuber_1", width=width, height=height)
    one = render_tile("tuber_1", lines=["only line"], width=width, height=height)
    many = render_tile("tuber_1", lines=["a", "b", "c", "d"], width=width, height=height)
    assert len(empty) == len(one) == len(many) == height
    expected = tile_pane.resolve_dialogue_line_count(height)
    for frame in (empty, one, many):
        assert len(_dialogue_rows(frame, height)) == expected


def test_tile_text_subpanel_grows_with_a_taller_pane(relay):
    """The design ask: text should 'take up the rest of the tuber panel' —
    a taller pane must give the TEXT subpanel more rows, not a fixed 2."""
    short = render_tile("tuber_1", width=40, height=20)
    tall = render_tile("tuber_1", width=40, height=30)
    assert len(_dialogue_rows(short, 20)) < len(_dialogue_rows(tall, 30))


def test_tile_shows_the_last_lines_newest_at_the_bottom(relay):
    width, height = 40, 20
    lines = render_tile("tuber_1", lines=["first", "second", "third"], width=width, height=height)
    rows = _dialogue_rows(lines, height)
    assert "third" in rows[-1]
    assert "second" in rows[-2]


def test_avatar_face_is_drawn_alongside_the_dialogue(relay):
    """The avatar must be present at ALL times — including while lines show."""
    body = "\n".join(render_tile("tuber_1", expression="speaking",
                                 lines=["talking now"], width=40, height=20))
    for face_row in tile_pane.TILE_FACES["speaking"]:
        assert face_row.strip() in body
    assert "talking now" in body


def test_tile_renders_avatar_text_and_status_as_distinct_subpanels(relay):
    """The design ask: avatar / text / status must read as three SEPARATE
    subpanels (each behind its own divider), not one undifferentiated block.
    No separate name row: the character name lives on the pane's own tmux
    border, not inside the frame."""
    lines = render_tile("tuber_1", expression="speaking", lines=["hello"],
                        status="speaking", width=40, height=20)
    dividers = [i for i, row in enumerate(lines) if row.startswith("├")]
    # avatar/text + text/status = 2 internal dividers.
    assert len(dividers) == 2
    avatar_rows = lines[1:1 + tile_pane.TILE_AVATAR_LINES]
    for face_row in tile_pane.TILE_FACES["speaking"]:
        assert any(face_row.strip() in row for row in avatar_rows)
    status_row = lines[dividers[1] + 1]
    assert "status: speaking" in status_row


def test_render_strips_ansi_so_the_box_never_overflows():
    colored = "\x1b[32m\x1b[1mVEX ▸\x1b[0m hello there"
    lines = render_tile("tuber_1", lines=[colored], width=40)
    body = "\n".join(lines)
    assert "\x1b[32m" not in body
    assert "VEX ▸ hello there" in body
    assert max(len(l) for l in lines) <= 40


# ── TileRenderer: the Performer's `out` ──────────────────────────────────────
class _Sink:
    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text
        return len(text)

    def flush(self):
        pass


def _renderer(relay, slot="tuber_2"):
    state_path = tile_state_file(str(relay), slot)
    sink = _Sink()
    r = tile_pane.TileRenderer(slot, state_path, out=sink)
    return r, sink, state_path


def test_renderer_swallows_the_transcript_instead_of_scrolling_the_tile(relay):
    """THE bug this class fixes: the Performer's full transcript used to go
    straight to the pane and scrolled the avatar off the top. The slot/name
    is not drawn inside the frame (it's on the tmux border), so this checks
    for the idle FACE having been drawn instead of the transcript text."""
    r, sink, _state = _renderer(relay)
    r.write("VEX ▸ a long line of transcript that must never reach the pane\n")
    assert "long line of transcript" not in sink.text
    for face_row in tile_pane.TILE_FACES["listening"]:
        assert face_row.strip() in sink.text   # a tile frame was drawn instead


def test_renderer_write_reports_the_byte_count_it_was_given(relay):
    """The Performer treats `out` as a file object; write() must behave."""
    r, _sink, _state = _renderer(relay)
    assert r.write("hello") == 5
    assert r.write("") == 0
    assert r.write(None) == 0


def test_renderer_picks_up_spoken_lines_from_the_avatar_state_file(relay):
    r, sink, state_path = _renderer(relay)
    tile_pane.write_tile_state(state_path, "speaking", bubble="first line")
    r.write("x")
    tile_pane.write_tile_state(state_path, "speaking", bubble="second line")
    r.write("x")

    assert list(r.lines) == ["first line", "second line"]
    frame = "\n".join(r.draw())
    assert "first line" in frame and "second line" in frame


def test_renderer_retains_more_than_the_display_slice(relay):
    """v1.4: TileRenderer keeps a generous history (DEFAULT_HISTORY); it's
    render_tile's resolve_dialogue_line_count() that decides how many of
    those lines actually show, based on the pane's real height."""
    r, _sink, state_path = _renderer(relay)
    for text in ("one", "two", "three"):
        tile_pane.write_tile_state(state_path, "speaking", bubble=text)
        r.write("x")
    assert list(r.lines) == ["one", "two", "three"]
    # ...but a render at a small height only shows the tail of it.
    frame = "\n".join(render_tile(r.slot, lines=list(r.lines), width=30, height=20))
    assert "three" in frame


def test_renderer_repeated_bubble_is_not_duplicated(relay):
    """The Performer writes many times per spoken line (it types character by
    character); only a NEW bubble is a new line."""
    r, _sink, state_path = _renderer(relay)
    tile_pane.write_tile_state(state_path, "speaking", bubble="just once")
    for _ in range(10):
        r.write("c")
    assert list(r.lines) == ["just once"]


def test_renderer_tracks_expression_and_status(relay):
    r, _sink, state_path = _renderer(relay)
    tile_pane.write_tile_state(state_path, "speaking", bubble="mine")
    r.write("x")
    assert r.expression == "speaking"
    assert r.status == "speaking"

    # An unowned scene: this tile stays visible and goes quiet.
    tile_pane.write_tile_state(state_path, "idle", action="listening to the show")
    r.write("x")
    assert r.expression == "idle"
    assert r.status == "listening"
    assert list(r.lines) == ["mine"]  # the last line stays on screen


def test_renderer_degrades_when_the_state_file_is_missing(relay, tmp_path):
    r = tile_pane.TileRenderer("tuber_2", str(tmp_path / "nope.json"), out=_Sink())
    r.write("x")                       # must not raise
    assert list(r.lines) == []
    body = "\n".join(r.draw())
    for face_row in tile_pane.TILE_FACES["listening"]:
        assert face_row.strip() in body


def test_renderer_refresh_forces_a_repaint(relay):
    r, sink, _state = _renderer(relay)
    sink.text = ""
    r.refresh()
    for face_row in tile_pane.TILE_FACES["listening"]:
        assert face_row.strip() in sink.text


def test_performer_is_given_the_tile_renderer_as_its_out(
        tile_library, store, relay, fake_performer, fast_cues):
    """Wiring guard: if the Performer is ever constructed without `out`, the
    transcript goes back to scrolling the avatar off the pane."""
    state_path = tile_state_file(str(relay), "tuber_2")
    assert perform_tile_request(_request(), "tuber_2", str(relay),
                                state_path=state_path) is True
    out = FakePerformer.instances[0].kwargs.get("out")
    assert isinstance(out, tile_pane.TileRenderer)
    assert out.slot == "tuber_2"
    assert out.state_path == state_path


# ── end-to-end bubble ownership: the real Performer, a real cast ──────────
class _RecordingRealPerformer:
    """Thin wrapper over the REAL replay_pane.Performer that records every
    _avatar() call it makes during perform_tile_request(). This is the seam
    that lets one test verify (a) the owning tile still gets its dialogue
    bubble, and (b) the non-owning tile never gets another character's
    dialogue bubble — both through the ACTUAL production code path, not a
    stand-in.

    The underlying Performer runs unmodified; the wrapper only observes
    `_avatar` as a side channel.
    """
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.avatar_calls = []
        from replay import Performer as _RealP
        self._real = _RealP(**kwargs)
        _RecordingRealPerformer.instances.append(self)

    def perform(self, script, show=None, start=0, limit=None):
        real_avatar = self._real._avatar

        def spy(expression, action="", bubble=None):
            self.avatar_calls.append((expression, action, bubble))
            return real_avatar(expression, action=action, bubble=bubble)

        self._real._avatar = spy
        return self._real.perform(script, show=show, start=start, limit=limit)


def test_real_performer_does_not_echo_a_foreign_dialogue_bubble(
        tile_library, store, relay, monkeypatch):
    """THE user-visible bug, proven through the ACTUAL Performer.

    A campaign voiceline is an assistant_text event (the dialogue beat).
    When a tile renders a scene it does NOT own (for pacing), the
    Performer's `_on_assistant_text` must not fire an avatar "speaking" +
    bubble write for that text. Before the fix, EVERY tile's Performer,
    writing its avatar unconditionally per assistant_text, echoed every
    character's dialogue into its own speech bubble, so all the roundtable
    tiles showed the same lines.

    The fixture's cast maps `coder` -> tuber_2, `boss` -> tuber_0, so:
      * drive the perform_tile_request for tuber_3 (owns NEITHER scene).
        Its Performer will render BOTH scenes for pacing — including the
        coder scene's "on it" assistant_text beat.
        After the fix, tuber_3's avatar must not receive a `speaking`
        + `"talking to the stream"` bubble, because neither scene is its own.
        This is the leak the user reported: a tile showing a line it didn't
        say.
    """
    _RecordingRealPerformer.instances = []
    monkeypatch.setattr(tile_pane, "Performer", _RecordingRealPerformer)

    # Silence the voice-gate build (it would try to bind a local flock file).
    monkeypatch.setattr(replay_pane, "build_voice_gate",
                        lambda script, cfg, tag="": (None, 0.0))

    # The tile's cue ratchet would otherwise block waiting for the FIRST cue;
    # swap in a no-op that authorizes each scene on request. (The relay
    # protocol itself is already pinned by the other tests in this file.)
    monkeypatch.setattr(
        tile_pane, "make_wait_for_scene",
        lambda cue_file, airing_id, show, slot, stop_file=None: (lambda i: i))

    request = _request()  # cast: {"boss": "tuber_0", "coder": "tuber_2"}
    # tuber_3 is not in the cast at all — it owns zero scenes.
    assert perform_tile_request(request, "tuber_3", str(relay)) is True

    performed = _RecordingRealPerformer.instances[0]
    talking = [c for c in performed.avatar_calls
               if c[0] == "speaking" and c[1] == "talking to the stream"]
    # No speaking/dialogue bubble may reach a tile that owns no dialogue.
    assert talking == [], \
        f"non-owning tile tuber_3 echoed a dialogue bubble: {talking}"


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_cli_defaults_state_file_into_the_relay_dir(monkeypatch, tmp_path, relay,
                                                    tile_library, store, fake_performer):
    calls = {}

    def fake_handle_once(slot, relay_dir, state_path=None, config=None, default_speed=1.0):
        calls.update(slot=slot, relay_dir=relay_dir, state_path=state_path)
        return False

    monkeypatch.setattr(tile_pane, "handle_once", fake_handle_once)
    monkeypatch.setattr(tile_pane, "load_worker_config", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(tile_pane.replay_pane, "load_worker_config", lambda path: None)

    assert tile_pane.main(["--slot", "tuber_2", "--relay-dir", str(relay), "--once"]) == 0
    assert calls["slot"] == "tuber_2"
    assert calls["relay_dir"] == str(relay)
    assert calls["state_path"] == tile_state_file(str(relay), "tuber_2")


def test_cli_state_file_override_wins(monkeypatch, tmp_path, relay):
    calls = {}
    monkeypatch.setattr(tile_pane, "handle_once",
                        lambda slot, relay_dir, **kw: calls.update(kw) or False)
    monkeypatch.setattr(tile_pane.replay_pane, "load_worker_config", lambda path: None)
    override = str(tmp_path / "custom.json")

    tile_pane.main(["--slot", "tuber_2", "--relay-dir", str(relay),
                    "--state-file", override, "--once"])
    assert calls["state_path"] == override


def test_cli_requires_a_slot():
    with pytest.raises(SystemExit):
        tile_pane.build_parser().parse_args([])


# ── post-line fade (dim after speaking) ──────────────────────────────────────
def test_render_tile_at_full_brightness_emits_no_extra_color_code():
    """brightness=1.0 (the default) must be byte-identical to pre-fade-feature
    output — no truecolor SGR wrap at all — so every existing caller/test
    that doesn't know about fades is completely unaffected."""
    sink = _Sink()
    render_tile("tuber_1", out=sink, width=40, height=20)
    assert "\x1b[38;2;" not in sink.text


def test_render_tile_at_reduced_brightness_wraps_in_a_dimmed_color_and_resets():
    sink = _Sink()
    render_tile("tuber_1", out=sink, width=40, height=20, brightness=0.5)
    assert "\x1b[38;2;" in sink.text
    assert sink.text.rstrip().endswith("\x1b[0m")
    # The returned rows (what tests/other callers introspect) stay plain —
    # only what actually reached `out` carries the color wrap.
    rows = render_tile("tuber_1", width=40, height=20, brightness=0.5, out=_Sink())
    assert all("\x1b[38;2;" not in row for row in rows)


def test_tile_color_code_clamps_to_the_fade_floor():
    """A caller passing a brightness below TILE_FADE_FLOOR (shouldn't happen
    given _current_brightness's own clamp, but the helper must not produce
    black/garbage output if it ever does) is clamped up to the floor."""
    floor_code = tile_pane._tile_color_code(tile_pane.TILE_FADE_FLOOR)
    below_floor_code = tile_pane._tile_color_code(0.0)
    assert below_floor_code == floor_code


def test_tile_color_code_at_full_brightness_matches_the_active_rgb():
    r, g, b = tile_pane.TILE_ACTIVE_RGB
    assert tile_pane._tile_color_code(1.0) == f"\x1b[38;2;{r};{g};{b}m"


def test_current_brightness_is_full_before_any_line_has_ever_finished(relay):
    """A fresh tile that hasn't spoken yet (or is CURRENTLY speaking) must
    render at full brightness — nothing has "just stopped" to fade from."""
    r, _sink, _state = _renderer(relay)
    r._fade_enabled = True
    assert r._current_brightness() == 1.0


def test_current_brightness_ramps_down_linearly_over_the_fade_duration(relay):
    r, _sink, _state = _renderer(relay)
    r._fade_enabled = True
    r._faded_since = 100.0
    # Immediately after the transition: still full brightness (the fade
    # STARTS at 1.0 and ramps down, it doesn't jump straight to the floor).
    assert r._current_brightness(now=100.0) == pytest.approx(1.0)
    # Halfway through TILE_FADE_DURATION_S: halfway between 1.0 and the floor.
    half_point = 100.0 + tile_pane.TILE_FADE_DURATION_S / 2
    expected_half = 1.0 - 0.5 * (1.0 - tile_pane.TILE_FADE_FLOOR)
    assert r._current_brightness(now=half_point) == pytest.approx(expected_half)
    # At and beyond the full duration: pinned at the floor, never below it.
    at_end = 100.0 + tile_pane.TILE_FADE_DURATION_S
    assert r._current_brightness(now=at_end) == pytest.approx(tile_pane.TILE_FADE_FLOOR)
    long_after = 100.0 + tile_pane.TILE_FADE_DURATION_S * 10
    assert r._current_brightness(now=long_after) == pytest.approx(tile_pane.TILE_FADE_FLOOR)


def test_current_brightness_ignores_fade_state_when_fade_disabled(relay):
    """fade_enabled=False (every caller except perform_tile_request, and
    every existing test) must always report full brightness regardless of
    _faded_since — the exact pre-fade-feature behavior."""
    r, _sink, _state = _renderer(relay)  # fade_enabled defaults to False
    r._faded_since = 0.0  # simulate a transition having somehow been recorded
    assert r._current_brightness(now=1000.0) == 1.0


def test_absorb_starts_the_fade_the_instant_speaking_stops(relay):
    """THE design ask: the fade must start IMMEDIATELY when a line ends, not
    after any delay — _absorb records the transition timestamp the moment
    the expression stops being "speaking", with no extra gap."""
    state_path = tile_state_file(str(relay), "tuber_2")
    r = tile_pane.TileRenderer("tuber_2", state_path, out=_Sink(), fade_enabled=True)
    try:
        r.expression = "speaking"  # simulate having just been the active speaker
        assert r._faded_since is None
        r._absorb({"expression": "idle"}, None)
        assert r._faded_since is not None
        # Brightness measured at the exact same instant is still ~1.0 (the
        # fade ramps FROM full, it doesn't snap to dim) — but a fade IS now
        # armed, unlike before the transition.
        assert r._current_brightness(now=r._faded_since) == pytest.approx(1.0)
    finally:
        r.close()


def test_absorb_snaps_back_to_full_brightness_when_speaking_resumes(relay):
    state_path = tile_state_file(str(relay), "tuber_2")
    r = tile_pane.TileRenderer("tuber_2", state_path, out=_Sink(), fade_enabled=True)
    try:
        r.expression = "speaking"
        r._absorb({"expression": "idle"}, None)
        assert r._faded_since is not None
        r._absorb({"expression": "speaking"}, None)
        assert r._faded_since is None
        assert r._current_brightness() == 1.0
    finally:
        r.close()


def test_fade_disabled_renderer_spawns_no_ticker_thread(relay):
    """The default (fade_enabled=False, every existing caller) must not pay
    any background-thread cost at all."""
    r, _sink, _state = _renderer(relay)
    assert r._ticker is None
    assert r._stop_event is None
    r.close()  # must be a safe no-op even with nothing to stop


def test_fade_enabled_renderer_dims_during_a_silent_wait(relay):
    """THE core behavior this feature exists for: a tile that stops speaking
    must visibly dim over time even while nothing calls write()/flush() at
    all — the exact shape of a Performer blocked in wait_extra() or the
    inter-line line_gap_s pause. Drives the ticker's real background thread
    (not just _current_brightness in isolation) so this also proves the
    thread actually wakes up and calls draw()."""
    state_path = tile_state_file(str(relay), "tuber_2")
    sink = _Sink()
    r = tile_pane.TileRenderer("tuber_2", state_path, out=sink,
                               fade_enabled=True, history=5)
    try:
        r.expression = "speaking"
        r._absorb({"expression": "idle"}, None)  # arms the fade, starting now
        # Give the ticker thread (FADE_TICK_S wake interval) a couple of
        # cycles to actually run and redraw at a dimmer brightness. This is
        # a real-time wait, not mocked, so keep the budget generous relative
        # to FADE_TICK_S to avoid flaking on a loaded CI box.
        deadline = time.monotonic() + max(1.0, tile_pane.TileRenderer.FADE_TICK_S * 6)
        saw_dim_frame = False
        while time.monotonic() < deadline:
            if "\x1b[38;2;" in sink.text:
                saw_dim_frame = True
                break
            time.sleep(tile_pane.TileRenderer.FADE_TICK_S)
        assert saw_dim_frame, (
            "expected the background fade ticker to have painted at least one "
            "dimmed frame while the renderer was otherwise idle"
        )
    finally:
        r.close()


def test_close_stops_the_ticker_thread_promptly(relay):
    state_path = tile_state_file(str(relay), "tuber_2")
    r = tile_pane.TileRenderer("tuber_2", state_path, out=_Sink(), fade_enabled=True)
    assert r._ticker.is_alive()
    r.close()
    assert not r._ticker.is_alive()


def test_perform_tile_request_enables_the_fade_and_closes_it_when_done(
        tile_library, store, relay, monkeypatch, capsys):
    """The one production call site (perform_tile_request) must actually turn
    fade_enabled on, and must close() the renderer (stop its ticker thread)
    once the show is over — including when the Performer raises, so a tile
    process airing many shows over a long broadcast can't accumulate leaked
    ticker threads."""
    seen = {}
    real_init = tile_pane.TileRenderer.__init__

    def spy_init(self, *args, **kwargs):
        seen["fade_enabled"] = kwargs.get("fade_enabled")
        real_init(self, *args, **kwargs)
        seen["renderer"] = self

    monkeypatch.setattr(tile_pane.TileRenderer, "__init__", spy_init)
    monkeypatch.setattr(tile_pane, "Performer", FakePerformer)
    request = {"airing_id": "airing-1", "episode": "round_ep",
              "cast": {"boss": "tuber_2"}, "worker_name": "Vigil"}
    Path(tile_request_file(str(relay), "tuber_2")).write_text(
        json.dumps(request), encoding="utf-8")

    assert handle_once("tuber_2", str(relay), state_path=tile_state_file(str(relay), "tuber_2")) is True
    assert seen["fade_enabled"] is True
    assert seen["renderer"]._ticker is not None
    assert not seen["renderer"]._ticker.is_alive()  # closed after the show


def test_perform_tile_request_closes_the_renderer_even_if_the_performer_raises(
        tile_library, store, relay, monkeypatch, capsys):
    class RaisingPerformer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def perform(self, script, show=None, start=0, limit=None):
            raise RuntimeError("boom")

    seen = {}
    real_init = tile_pane.TileRenderer.__init__

    def spy_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        seen["renderer"] = self

    monkeypatch.setattr(tile_pane.TileRenderer, "__init__", spy_init)
    monkeypatch.setattr(tile_pane, "Performer", RaisingPerformer)
    request = {"airing_id": "airing-1", "episode": "round_ep",
              "cast": {"boss": "tuber_2"}, "worker_name": "Vigil"}
    Path(tile_request_file(str(relay), "tuber_2")).write_text(
        json.dumps(request), encoding="utf-8")

    # handle_once swallows the exception (§ "one bad show must never kill
    # the tile"), but the renderer's ticker thread must STILL be stopped.
    assert handle_once("tuber_2", str(relay), state_path=tile_state_file(str(relay), "tuber_2")) is False
    assert not seen["renderer"]._ticker.is_alive()
