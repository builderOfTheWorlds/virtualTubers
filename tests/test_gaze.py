"""Roundtable look-at: who faces whom, sweeping, timing, and the Performer hook."""
import math
from pathlib import Path

import pytest
import yaml

import gaze
import replay
from replay import Pacer, Palette, Performer

PARTICIPANTS = [f"tuber_{i}" for i in range(8)]
CAST = {"boss": "tuber_0", **{s: s for s in PARTICIPANTS}}
NAMES = {"tuber_0": "Game Master", "tuber_1": "Max-1", "tuber_2": "Vigil",
         "tuber_5": "Burt"}


# ── geometry ─────────────────────────────────────────────────────────────────
def test_grid_matches_roundtable_layout():
    """tuber_N sits at col N%4, row N//4 — pinned against the real layout so
    a reshuffled grid can't silently aim every head at the wrong tile."""
    layout = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config/layouts/roundtable.yaml")
        .read_text(encoding="utf-8"))
    slots = [p["with"]["slot"] for p in layout["panes"]]
    assert sorted(slots) == PARTICIPANTS
    assert gaze.slot_grid_position("tuber_0") == (0, 0)
    assert gaze.slot_grid_position("tuber_3") == (3, 0)
    assert gaze.slot_grid_position("tuber_4") == (0, 1)
    assert gaze.slot_grid_position("tuber_7") == (3, 1)
    assert gaze.slot_grid_position("boss") is None


@pytest.mark.parametrize("src,dst,yaw_sign,pitch_sign", [
    ("tuber_3", "tuber_0", -1, 0),   # GM is to the left
    ("tuber_0", "tuber_2", +1, 0),   # GM looks right along the top row
    ("tuber_5", "tuber_0", -1, -1),  # up and to the left
    ("tuber_0", "tuber_4", 0, +1),   # straight down
])
def test_gaze_angles_point_toward_target(src, dst, yaw_sign, pitch_sign):
    yaw, pitch = gaze.gaze_angles(src, dst)
    assert (yaw > 0) - (yaw < 0) == yaw_sign
    assert (pitch > 0) - (pitch < 0) == pitch_sign
    assert abs(yaw) <= gaze.MAX_YAW_RAD and abs(pitch) <= gaze.MAX_PITCH_RAD


def test_farther_targets_turn_further():
    near = gaze.gaze_angles("tuber_0", "tuber_1")[0]
    far = gaze.gaze_angles("tuber_0", "tuber_3")[0]
    assert 0 < near < far


def test_unknown_or_self_is_rest_pose():
    assert gaze.gaze_angles("tuber_1", "tuber_1") == (gaze.REST_YAW_RAD, gaze.REST_PITCH_RAD)
    assert gaze.gaze_angles("tuber_1", "nobody") == (gaze.REST_YAW_RAD, gaze.REST_PITCH_RAD)


# ── addressees ───────────────────────────────────────────────────────────────
def _scene(text, **event_extra):
    return {"narration": text, "events": [{"type": "assistant_text", "text": text, **event_extra}]}


def test_gm_with_nobody_named_addresses_the_whole_table():
    got = gaze.resolve_addressees(_scene("The torches gutter."), "tuber_0", CAST,
                                  PARTICIPANTS, NAMES)
    assert got == [s for s in PARTICIPANTS if s != "tuber_0"]


def test_gm_naming_a_character_looks_at_them():
    got = gaze.resolve_addressees(_scene("Max-1, what do you do?"), "tuber_0", CAST,
                                  PARTICIPANTS, NAMES)
    assert got == ["tuber_1"]


def test_explicit_addressee_wins_and_maps_speaker_ids_through_cast():
    scene = _scene("Burt, Max-1 — look.", addressee="boss")
    assert gaze.resolve_addressees(scene, "tuber_2", CAST, PARTICIPANTS, NAMES) == ["tuber_0"]
    scene = _scene("Heads up.", to="all")
    assert len(gaze.resolve_addressees(scene, "tuber_2", CAST, PARTICIPANTS, NAMES)) == 7


def test_player_with_nobody_named_answers_previous_speaker_then_gm():
    scene = _scene("I check the door.")
    assert gaze.resolve_addressees(scene, "tuber_1", CAST, PARTICIPANTS, NAMES,
                                   previous_speaker="tuber_5") == ["tuber_5"]
    assert gaze.resolve_addressees(scene, "tuber_1", CAST, PARTICIPANTS, NAMES) == ["tuber_0"]


def test_everyone_words_address_the_table():
    got = gaze.resolve_addressees(_scene("Everyone, stay close."), "tuber_1", CAST,
                                  PARTICIPANTS, NAMES)
    assert "tuber_1" not in got and len(got) == 7


def test_name_match_is_whole_word():
    names = {"tuber_1": "Max"}
    got = gaze.resolve_addressees(_scene("Maximum effort."), "tuber_2", CAST,
                                  PARTICIPANTS, names, previous_speaker="tuber_0")
    assert got == ["tuber_0"]


# ── gaze over time ───────────────────────────────────────────────────────────
def _stage(speaker, addressees, started=100.0, duration=8.0):
    return {"speaker": speaker, "addressees": addressees,
            "started_at": started, "duration": duration}


def test_listeners_look_at_the_speaker():
    stage = _stage("tuber_1", ["tuber_0"])
    for slot in ("tuber_0", "tuber_3", "tuber_6"):
        assert gaze.gaze_target(slot, stage, 102.0) == "tuber_1"


def test_speaker_holds_a_single_addressee():
    stage = _stage("tuber_0", ["tuber_1"])
    assert all(gaze.gaze_target("tuber_0", stage, 100.0 + t) == "tuber_1"
               for t in (0, 3, 7.9))


def test_speaker_sweeps_across_several_listeners_in_reading_order():
    stage = _stage("tuber_0", ["tuber_5", "tuber_1", "tuber_2"], duration=9.0)
    seen = [gaze.gaze_target("tuber_0", stage, 100.0 + t) for t in (0.5, 3.5, 6.5, 12.0)]
    assert seen == ["tuber_1", "tuber_2", "tuber_5", "tuber_5"]


def test_sweep_never_dwells_less_than_minimum():
    stage = _stage("tuber_0", PARTICIPANTS[1:], duration=2.0)
    assert gaze.gaze_target("tuber_0", stage, 100.0 + gaze.MIN_SWEEP_DWELL_S * 0.9) == "tuber_1"


def test_heads_return_to_rest_after_linger():
    stage = _stage("tuber_1", ["tuber_0"], duration=4.0)
    assert gaze.gaze_target("tuber_3", stage, 104.0 + gaze.STAGE_LINGER_S - 0.1) == "tuber_1"
    assert gaze.gaze_target("tuber_3", stage, 104.0 + gaze.STAGE_LINGER_S + 0.1) is None
    assert gaze.gaze_target("tuber_3", None, 0) is None


def test_mouth_follows_envelope_only_for_speaker():
    stage = {**_stage("tuber_1", ["tuber_0"]), "envelope": [0.0, 0.8, 0.3],
             "envelope_rate_hz": 10}
    assert gaze.mouth_open("tuber_1", stage, 100.15) == pytest.approx(0.8)
    assert gaze.mouth_open("tuber_0", stage, 100.15) == 0.0
    assert gaze.mouth_open("tuber_1", stage, 101.0) == 0.0  # past envelope end


def test_controller_eases_instead_of_snapping():
    ctl = gaze.GazeController(yaw=0.0, pitch=0.0)
    ctl.step(1.0, 0.0, now=0.0)
    y1, _ = ctl.step(1.0, 0.0, now=0.1)
    assert 0 < y1 < 0.5
    for i in range(2, 60):
        ctl.step(1.0, 0.0, now=i * 0.1)
    assert ctl.yaw == pytest.approx(1.0, abs=1e-3)


def test_stage_roundtrip_and_stage_gaze(tmp_path):
    path = str(tmp_path / "stage.json")
    gaze.write_stage(path, "tuber_1", ["tuber_0"], 5.0, started_at=1000.0)
    assert gaze.read_stage(path)["speaker"] == "tuber_1"
    clock = {"t": 1001.0}
    mono = {"t": 0.0}
    sg = gaze.StageGaze("tuber_3", path, controller=gaze.GazeController(max_speed=100),
                        clock=lambda: clock["t"], mono=lambda: mono["t"])
    for i in range(40):
        mono["t"] = i * 0.1
        (yaw, pitch), mouth = sg.sample()
    target_yaw, _ = gaze.gaze_angles("tuber_3", "tuber_1")
    assert yaw == pytest.approx(target_yaw, abs=1e-3)
    assert mouth == 0.0


def test_stage_gaze_missing_file_is_rest(tmp_path):
    sg = gaze.StageGaze("tuber_3", str(tmp_path / "nope.json"))
    (yaw, pitch), mouth = sg.sample()
    assert math.isfinite(yaw) and mouth == 0.0


# ── Performer hook: text, voice and gaze start together ─────────────────────
class _Audio:
    def __init__(self, path, duration):
        self.audio_path, self.duration = path, duration


class _Playback:
    def running(self):
        return False

    def wait(self, timeout=None):
        return None

    def stop(self):
        return None


def test_on_voice_start_fires_after_gate_with_bubble_already_written(monkeypatch, tmp_path):
    order = []
    monkeypatch.setattr(replay, "play_wav", lambda path, out=None: order.append("play") or _Playback())
    monkeypatch.setattr(replay, "wait_extra", lambda *a, **k: None)

    class Gate:
        def acquire(self):
            order.append("gate")

            class Seat:
                def release(self_inner):
                    order.append("release")
            return Seat()

    state = tmp_path / "s.json"
    perf = Performer(out=open(tmp_path / "o.txt", "w", encoding="utf-8"),
                     pacer=Pacer(speed=1000.0), palette=Palette(False),
                     state_path=str(state), voice_gate=Gate(),
                     on_voice_start=lambda scene, d, p: order.append(("voice", d, p)))
    orig = perf._avatar
    perf._avatar = lambda e, action="", bubble=None: (order.append(("avatar", e, bubble)), orig(e, action, bubble))
    scene = {"speaker": "tuber_1", "narration": "Hi all", "owned": True,
             "audio": _Audio("x.wav", 0.01),
             "events": [{"type": "assistant_text", "text": "Hi all"}]}
    perf._perform_scene(scene)
    gate_i = order.index("gate")
    bubble_i = order.index(("avatar", "speaking", "Hi all"))
    play_i = order.index("play")
    voice_i = next(i for i, o in enumerate(order) if isinstance(o, tuple) and o[0] == "voice")
    assert gate_i < bubble_i < play_i < voice_i
    assert order[voice_i] == ("voice", 0.01, "x.wav")


def test_on_voice_start_not_fired_for_unowned_scene(monkeypatch, tmp_path):
    calls = []
    perf = Performer(out=open(tmp_path / "o.txt", "w", encoding="utf-8"),
                     pacer=Pacer(speed=1000.0), palette=Palette(False),
                     on_voice_start=lambda *a: calls.append(a))
    perf._perform_scene({"speaker": "tuber_2", "narration": "x", "owned": False,
                         "audio": None, "target_duration": 0.01,
                         "events": [{"type": "assistant_text", "text": "x"}]})
    assert calls == []


def test_tile_stage_writer_publishes_addressees(tmp_path):
    import tile_pane
    path = str(tmp_path / "stage.json")
    config = {"roster": {"tuber_0": "Game Master", "tuber_1": {"name": "Max-1"}}}
    hook = tile_pane.make_stage_writer("tuber_0", path, {"events": []}, CAST, config=config)
    hook(_scene("Max-1, roll for it."), 4.0, None)
    stage = gaze.read_stage(path)
    assert stage["speaker"] == "tuber_0" and stage["addressees"] == ["tuber_1"]
    assert stage["duration"] == 4.0
