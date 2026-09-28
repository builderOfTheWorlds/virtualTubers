"""Tests for app/live_pane.py (OB-32): the live office transcript on the
roundtable — seat -> bus `office_line` -> roundtable agent spool -> director
-> the speaker's tile, and the observer tile's gaze-only pose.

Everything runs on tmp_path relay dirs; the LLM, TTS and audio playback are
faked, and time.sleep is a no-op so Performer pacing costs nothing.
"""
import io
import json
import os
from pathlib import Path

import pytest
import yaml

import gaze
import live_pane
import relay_io
import replay
import replay_pane
import tile_pane
from agent_handlers import MESSAGE_HANDLERS, office
from e2e_harness import ScriptedLLM, llm_reply
from message_bus import BROADCAST, build_message
from office.protocol import build_directive
from office.roles import SEAT, OfficeRole as R

CEO, TL, AN, PM = SEAT[R.CEO], SEAT[R.TECH_LEAD], SEAT[R.ANALYST], SEAT[R.PARTY_MEMBER]
NAMES = {"tuber_0": "Graham Ellery", "tuber_1": "Anselm Brody", "tuber_2": "Maren Voss",
         "tuber_7": "A. Penhale"}


# ── fixtures / fakes ─────────────────────────────────────────────────────────
class ListProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


class BrokenProducer:
    def send(self, message):
        raise RuntimeError("kafka down")


class FakeTTS:
    """Writes a real (tiny) file and reports a fixed duration."""

    def __init__(self, duration=1.5, fail=False):
        self.duration = duration
        self.fail = fail
        self.calls = []

    def synthesize(self, text, out_wav, speaker="coder", voice_name=None):
        self.calls.append((text, speaker))
        if self.fail:
            raise RuntimeError("piper exploded")
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        Path(out_wav).write_bytes(b"RIFF")
        from tts_client import Narration
        return Narration(audio_path=Path(out_wav), duration=self.duration)


class RecordingGate:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    def acquire(self):
        self.acquired += 1
        gate = self

        class Seat:
            def release(self_inner):
                gate.released += 1
        return Seat()


class FakePlayback:
    def stop(self):
        pass


def roundtable_config(enabled=True, **live):
    cfg = {"agent": {"role": "roundtable"},
           "voice": {"speaker_names": dict(NAMES), "boss_name": "Ashiorid"},
           "roster": {"tuber_3": {"name": "Theo Palliser"}}}
    if enabled is not None:
        cfg["agent"]["live"] = {"enabled": enabled, **live}
    return cfg


@pytest.fixture
def relay(tmp_path, monkeypatch):
    path = tmp_path / "tiles"
    path.mkdir()
    monkeypatch.setenv(relay_io.TILE_RELAY_DIR_ENV, str(path))
    monkeypatch.setenv("REPLAY_STOP_FILE", str(tmp_path / "stop.json"))
    return path


@pytest.fixture
def fast(monkeypatch):
    """No real sleeping or audio anywhere in the Performer path."""
    monkeypatch.setattr(replay.time, "sleep", lambda s: None)
    played = []

    def fake_play(path):
        played.append(str(path))
        return FakePlayback()
    monkeypatch.setattr(replay, "play_wav", fake_play)
    monkeypatch.setattr(replay, "wait_extra", lambda playback, started, duration: None)
    gate = RecordingGate()
    monkeypatch.setattr(replay_pane, "build_voice_gate", lambda script, cfg, tag="": (gate, 0.0))
    yield {"played": played, "gate": gate}
    for slot in list(live_pane._renderers):
        live_pane.reset_live(slot)


def make_pack(root):
    for role in R:
        (root / "cast").mkdir(parents=True, exist_ok=True)
        (root / "profiles").mkdir(parents=True, exist_ok=True)
        (root / "cast" / f"{role.value}.yaml").write_text(
            yaml.safe_dump({"name": role.value, "system_prompt": f"You are the {role.value}."}),
            encoding="utf-8")
        (root / "profiles" / f"{role.value}.yaml").write_text(
            yaml.safe_dump({"id": role.value, "backstory": {"believed": f"I am {role.value}."}}),
            encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _fresh_office_state():
    office._reset_office_state()
    yield
    office._reset_office_state()


def seat_cfg(role, pack, live=True):
    office_block = {"pack_dir": str(pack)}
    if live is not None:
        office_block["live_transcript"] = live
    return {"role": "manager" if role is R.TECH_LEAD else role.value,
            "office_role": role.value, "system_prompt": "x", "office": office_block}


def office_line(seat, text="Noted. Waiting on the plan.", sender=None, **extra):
    return build_message(sender or seat, "roundtable", "office_line",
                         {"seat": seat, "text": text, "emotion": "neutral", **extra})


def tile_poller(relay, config, state_paths, out):
    """A director `sleep` that, while it waits, lets every tile poll once —
    the single-threaded stand-in for the 8 tile processes."""
    def sleep(_seconds):
        for slot in live_pane.seat_ids():
            live_pane.handle_live_once(slot, str(relay), state_path=state_paths[slot],
                                       config=config, out=out)
    return sleep


# ── seat side ────────────────────────────────────────────────────────────────
def test_publish_office_line_off_by_default_sends_nothing(tmp_path):
    producer = ListProducer()
    cfg = seat_cfg(R.TECH_LEAD, tmp_path, live=None)
    assert live_pane.publish_office_line(TL, cfg, producer, "hello") is None
    assert producer.sent == []


def test_publish_office_line_enabled_addresses_the_roundtable(tmp_path):
    producer = ListProducer()
    msg = live_pane.publish_office_line(TL, seat_cfg(R.TECH_LEAD, tmp_path), producer,
                                        "  Ship it.  ", "happy", "chain-1")
    assert producer.sent == [msg]
    assert msg["to"] == "roundtable" and msg["type"] == "office_line"
    assert msg["payload"] == {"seat": TL, "text": "Ship it.", "emotion": "happy"}
    assert msg["correlation_id"] == "chain-1"


@pytest.mark.parametrize("line", ["", "   ", None])
def test_publish_office_line_empty_line_skipped(tmp_path, line):
    producer = ListProducer()
    assert live_pane.publish_office_line(TL, seat_cfg(R.TECH_LEAD, tmp_path), producer, line) is None
    assert producer.sent == []


def test_publish_office_line_party_member_never_publishes(tmp_path):
    producer = ListProducer()
    cfg = seat_cfg(R.PARTY_MEMBER, tmp_path)
    assert live_pane.publish_office_line(PM, cfg, producer, "I saw everything") is None
    assert producer.sent == []


def test_publish_office_line_producer_failure_never_raises(tmp_path):
    assert live_pane.publish_office_line(
        TL, seat_cfg(R.TECH_LEAD, tmp_path), BrokenProducer(), "hi") is None


def test_office_handler_publishes_its_spoken_line(tmp_path, monkeypatch):
    """The committed line (the Tech Lead's bubble) is what goes to the roundtable."""
    monkeypatch.setattr(office, "build_gitea_client", lambda cfg: None)
    pack = make_pack(tmp_path / "pack")
    producer = ListProducer()
    llm = ScriptedLLM("tl", replies=[llm_reply("Noted, Graham. Maren, send me the plan.", "focused")])
    msg = build_directive(CEO, TL, "Add a velocity rule.", title="Velocity", day="2026-09-28")
    state = tmp_path / "tl_state.json"
    office.handle_directive(TL, seat_cfg(R.TECH_LEAD, pack), llm, producer, msg,
                            state_path=str(state))
    [line] = producer.sent
    assert line["type"] == "office_line" and line["payload"]["seat"] == TL
    assert line["payload"]["text"] == json.loads(state.read_text())["bubble"]
    assert line["correlation_id"] == msg["correlation_id"]


# ── roundtable agent side ────────────────────────────────────────────────────
def test_handlers_registered_for_live_types():
    assert MESSAGE_HANDLERS["office_line"].__name__ == "handle_office_line"
    assert MESSAGE_HANDLERS["observer_pose"].__name__ == "handle_observer_pose"


def test_handle_office_line_spools_a_valid_line(relay):
    agent = roundtable_config()["agent"]
    path = MESSAGE_HANDLERS["office_line"]("roundtable", agent, None, None, office_line(TL))
    assert path and Path(path).parent == Path(live_pane.live_spool_dir(relay))
    entry = json.loads(Path(path).read_text())
    assert entry["seat"] == TL and entry["text"] == "Noted. Waiting on the plan."
    assert live_pane.list_spool(relay) == [path]


@pytest.mark.parametrize("config,env", [
    (roundtable_config(enabled=None), True),      # dev-team roundtable: no live block
    (roundtable_config(enabled=False), True),
    ({"agent": {"role": "manager", "live": {"enabled": True}}}, True),  # not the roundtable
    (roundtable_config(), False),                 # no TILE_RELAY_DIR
])
def test_handle_office_line_gated_off_writes_nothing(relay, monkeypatch, config, env):
    if not env:
        monkeypatch.delenv(relay_io.TILE_RELAY_DIR_ENV)
    assert live_pane.handle_office_line("roundtable", config["agent"], office_line(TL)) is None
    assert not Path(live_pane.live_spool_dir(relay)).exists()


@pytest.mark.parametrize("msg", [
    office_line(TL, sender=AN),                   # a seat may only commit its own lines
    office_line(PM, text="psst"),                 # the observer never speaks
    office_line("tuber_9"),                       # not a seat
    office_line(TL, text="   "),                  # nothing to say
])
def test_handle_office_line_refuses_bad_lines(relay, msg):
    assert live_pane.handle_office_line("roundtable", roundtable_config()["agent"], msg) is None
    assert live_pane.list_spool(relay) == []


def test_spool_trims_oldest_past_the_cap(relay):
    agent = roundtable_config(spool_max=2)["agent"]
    paths = [live_pane.handle_office_line("roundtable", agent, office_line(TL, text=f"line {i}"))
             for i in range(3)]
    assert live_pane.list_spool(relay) == paths[1:]


def test_observer_pose_recorded_for_the_observer_only(relay):
    agent = roundtable_config()["agent"]
    pose = build_message(PM, BROADCAST, "observer_pose",
                         {"seat": PM, "pose": "idle_watch", "gaze_target": TL})
    written = MESSAGE_HANDLERS["observer_pose"]("roundtable", agent, None, None, pose)
    assert written["gaze_target"] == TL
    assert relay_io.read_json(live_pane.tile_pose_file(relay, PM))["gaze_target"] == TL
    spoof = build_message(TL, BROADCAST, "observer_pose", {"seat": TL, "gaze_target": CEO})
    assert live_pane.handle_observer_pose("roundtable", agent, spoof) is None
    assert not Path(live_pane.tile_pose_file(relay, TL)).exists()


def test_observer_pose_ignored_on_non_live_worker(relay):
    pose = build_message(PM, BROADCAST, "observer_pose", {"seat": PM, "gaze_target": TL})
    assert live_pane.handle_observer_pose(PM, {"role": "observer"}, pose) is None


# ── end to end: seat line -> relay -> the speaker's tile ─────────────────────
def test_office_line_flows_to_the_speakers_tile_only(relay, fast, tmp_path):
    config = roundtable_config()
    producer = ListProducer()
    live_pane.publish_office_line(TL, seat_cfg(R.TECH_LEAD, tmp_path), producer,
                                  "Maren Voss, I need that plan by noon.")
    [bus_msg] = producer.sent
    MESSAGE_HANDLERS[bus_msg["type"]]("roundtable", config["agent"], None, None, bus_msg)

    states = {s: str(relay / f"{s}.state.json") for s in live_pane.seat_ids()}
    out = io.StringIO()
    director = live_pane.LiveDirector(config, relay_dir=str(relay), tts=None,
                                      sleep=tile_poller(relay, config, states, out))
    assert director.drain_once() == 1

    assert live_pane.list_spool(relay) == []
    assert not Path(live_pane.tile_live_file(relay, TL)).exists()   # claimed by the tile
    assert list(live_pane._renderers) == [TL]                        # only the speaker drew
    assert list(live_pane._renderers[TL].lines) == ["Maren Voss, I need that plan by noon."]
    # Nobody else's avatar got the line.
    for slot, path in states.items():
        if slot != TL:
            assert not Path(path).exists() or \
                (json.loads(Path(path).read_text()).get("bubble") is None)
    # The speaker published who-talks-to-whom for every head (gaze.py);
    # "Maren Voss" in the line makes the Analyst the addressee.
    stage = gaze.read_stage(tile_pane.tile_stage_file(relay))
    assert stage["speaker"] == TL and stage["addressees"] == [AN]
    assert tile_pane.tile_stage_file(relay) and \
        json.loads(Path(states[TL]).read_text())["expression"] == "idle"


def test_voiced_line_goes_through_the_voice_gate(relay, fast):
    config = roundtable_config()
    live_pane.handle_office_line("roundtable", config["agent"], office_line(CEO, text="Good morning, team."))
    states = {s: str(relay / f"{s}.state.json") for s in live_pane.seat_ids()}
    tts = FakeTTS(duration=2.0)
    director = live_pane.LiveDirector(config, relay_dir=str(relay), tts=tts,
                                      sleep=tile_poller(relay, config, states, io.StringIO()))
    assert director.drain_once() == 1
    assert tts.calls == [("Good morning, team.", CEO)]           # voice.speakers[tuber_0]
    assert len(fast["played"]) == 1
    assert fast["gate"].acquired == 1 and fast["gate"].released == 1
    assert not Path(fast["played"][0]).exists()                  # the tile cleans up its WAV
    assert gaze.read_stage(tile_pane.tile_stage_file(relay))["duration"] == 2.0


def test_tts_failure_still_airs_the_line_silently(relay, fast):
    config = roundtable_config()
    live_pane.handle_office_line("roundtable", config["agent"], office_line(TL))
    states = {s: str(relay / f"{s}.state.json") for s in live_pane.seat_ids()}
    director = live_pane.LiveDirector(config, relay_dir=str(relay), tts=FakeTTS(fail=True),
                                      sleep=tile_poller(relay, config, states, io.StringIO()))
    assert director.drain_once() == 1
    assert fast["played"] == [] and fast["gate"].acquired == 0
    assert list(live_pane._renderers[TL].lines) == ["Noted. Waiting on the plan."]


def test_lines_air_in_commit_order(relay, fast):
    config = roundtable_config()
    for seat, text in ((CEO, "First."), (TL, "Second."), (AN, "Third.")):
        live_pane.handle_office_line("roundtable", config["agent"], office_line(seat, text=text))
    handed = []
    director = live_pane.LiveDirector(config, relay_dir=str(relay), sleep=lambda s: None)
    director.hand = lambda payload: handed.append((payload["speaker"], payload["text"])) or True
    assert director.drain_once() == 3
    assert handed == [(CEO, "First."), (TL, "Second."), (AN, "Third.")]


def test_director_yields_to_a_pending_replay(relay, fast):
    config = roundtable_config()
    for text in ("one", "two"):
        live_pane.handle_office_line("roundtable", config["agent"], office_line(TL, text=text))
    director = live_pane.LiveDirector(config, relay_dir=str(relay), sleep=lambda s: None)
    director.hand = lambda payload: True
    assert director.drain_once(should_yield=lambda: True) == 0
    assert len(live_pane.list_spool(relay)) == 2                    # left for later


def test_stale_line_dropped_never_aired(relay, fast):
    config = roundtable_config(max_age_s=10)
    live_pane.handle_office_line("roundtable", config["agent"], office_line(TL))
    director = live_pane.LiveDirector(config, relay_dir=str(relay), sleep=lambda s: None,
                                      clock=lambda: 10 ** 12)
    director.hand = lambda payload: pytest.fail("a stale line must not be handed")
    assert director.drain_once() == 0 and live_pane.list_spool(relay) == []


def test_unclaimed_line_is_withdrawn(relay, fast):
    config = roundtable_config(consume_timeout_s=0)
    director = live_pane.LiveDirector(config, relay_dir=str(relay), sleep=lambda s: None)
    payload = director.prepare({"seat": TL, "text": "hello?", "line_id": "L1"})
    assert director.hand(payload) is False
    assert not Path(live_pane.tile_live_file(relay, TL)).exists()


def test_director_prepare_uses_the_seat_display_name(relay):
    director = live_pane.LiveDirector(roundtable_config(), relay_dir=str(relay))
    assert director.prepare({"seat": TL, "text": "x", "line_id": "a"})["name"] == "Anselm Brody"
    assert director.prepare({"seat": "tuber_3", "text": "x", "line_id": "b"})["name"] == "Theo Palliser"


# ── the observer tile: pose / gaze, never text ───────────────────────────────
def test_observer_tile_discards_a_line_and_shows_no_text(relay, fast):
    config = roundtable_config()
    state = str(relay / f"{PM}.state.json")
    relay_io.atomic_write_json(live_pane.tile_live_file(relay, PM), {
        "type": "live_line", "line_id": "x", "speaker": PM, "text": "I am the spy",
        "duration": 1.0})
    assert live_pane.handle_live_once(PM, str(relay), state_path=state, config=config) is False
    assert not Path(live_pane.tile_live_file(relay, PM)).exists()
    assert PM not in live_pane._renderers and not Path(state).exists()
    frame = "\n".join(live_pane.draw_idle(PM, state, config=config, out=io.StringIO()))
    assert "I am the spy" not in frame and "watching" in frame
    assert json.loads(Path(state).read_text()).get("bubble") is None


def test_director_never_hands_the_observer_a_line(relay, fast):
    config = roundtable_config()
    live_pane.enqueue_line(str(relay), {"seat": PM, "text": "psst", "line_id": "p",
                                        "received_at": __import__("time").time()})
    director = live_pane.LiveDirector(config, relay_dir=str(relay), sleep=lambda s: None)
    director.hand = lambda payload: pytest.fail("the observer must never get a line")
    assert director.drain_once() == 0


def _observer(relay, clock):
    return live_pane.ObserverGaze(PM, tile_pane.tile_stage_file(relay),
                                  live_pane.tile_pose_file(relay, PM),
                                  clock=clock, mono=lambda: 0.0)


def test_observer_gaze_follows_the_live_speaker(relay):
    gaze.write_stage(tile_pane.tile_stage_file(relay), AN, [TL], 3.0, started_at=1000.0)
    live_pane.write_pose(str(relay), PM, "idle_watch", CEO, now=1000.0)
    watcher = _observer(relay, clock=lambda: 1001.0)
    assert watcher.target() == AN                                  # speaker beats pose
    (yaw, pitch), mouth = watcher.sample()
    assert mouth == 0.0                                            # never talks


def test_observer_gaze_uses_fresh_pose_between_lines(relay):
    live_pane.write_pose(str(relay), PM, "idle_watch", CEO, now=1000.0)
    assert _observer(relay, clock=lambda: 1010.0).target() == CEO
    stale = _observer(relay, clock=lambda: 1000.0 + live_pane.POSE_TTL_S + 1)
    assert stale.target() is None                                  # rest pose
    assert stale.sample()[1] == 0.0


def test_make_gaze_source_observer_only_on_live_roundtable(relay):
    stage = tile_pane.tile_stage_file(relay)
    live = live_pane.make_gaze_source(PM, stage, roundtable_config())
    assert isinstance(live.__self__, live_pane.ObserverGaze)
    assert isinstance(live_pane.make_gaze_source(TL, stage, roundtable_config()).__self__,
                      gaze.StageGaze)
    dev = live_pane.make_gaze_source(PM, stage, roundtable_config(enabled=None))
    assert isinstance(dev.__self__, gaze.StageGaze)


# ── dev-team roundtable unchanged ────────────────────────────────────────────
def test_dev_team_roundtable_has_no_live_feed(relay, monkeypatch):
    config = roundtable_config(enabled=None)
    assert live_pane.LiveDirector.from_config(config) is None
    assert live_pane.observer_slot_of(config) is None
    relay_io.atomic_write_json(live_pane.tile_live_file(relay, TL), {"type": "live_line"})
    assert live_pane.handle_live_once(TL, str(relay), config=config) is False
    assert Path(live_pane.tile_live_file(relay, TL)).exists()      # untouched
    calls = []
    monkeypatch.setattr(tile_pane, "draw_idle_screen",
                        lambda slot, state_path=None, out=None: calls.append(slot) or [])
    live_pane.draw_idle(PM, None, config=config)
    assert calls == [PM]                                           # stock idle screen


def test_director_from_config_on_live_roundtable(relay, monkeypatch):
    monkeypatch.setattr("tts_client.build_tts_client", lambda cfg: None)
    director = live_pane.LiveDirector.from_config(roundtable_config())
    assert director is not None and director.relay_dir == str(relay) and director.tts is None


def test_office_roundtable_config_enables_live_with_observer():
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "config/workers/office/roundtable.yaml").read_text())
    settings = live_pane.live_settings(cfg)
    assert settings["enabled"] is True and settings["observer_slot"] == PM == "tuber_7"
    dev = yaml.safe_load((root / "config/workers/roundtable.yaml").read_text())
    assert live_pane.live_settings(dev)["enabled"] is False
