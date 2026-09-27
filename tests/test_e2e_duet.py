"""End-to-end duet replay (docs/duet_replay.md) across simulated workers.

Two levels:
- bus/relay-file level: the real agent_handlers.replay_relay handlers on
  each worker, driven by director-side messages injected on the bus, with
  every worker's relay files checked through relay_io.
- full path: the real replay_pane.perform_request director + follower code
  running concurrently (one thread per pane) against the same in-memory
  bus, with a lockstep fake Performer (tests/e2e_harness.py DuetStage).
"""
import pytest

import relay_io
from e2e_harness import OPERATOR, DuetStage, E2EHarness

pytestmark = pytest.mark.integration

DIRECTOR, FOLLOWER_A, FOLLOWER_B = "tuber_1", "tuber_2", "tuber_3"
EPISODE = "duet_ep"
EPISODES = {
    EPISODE: {
        "source": EPISODE,
        "events": [
            {"type": "user_message", "text": "ship the login fix"},   # scene 0: boss
            {"type": "assistant_text", "text": "on it boss"},          # scene 1: coder
        ],
    },
}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    h = E2EHarness(tmp_path, monkeypatch)
    for worker_id in (DIRECTOR, FOLLOWER_A, FOLLOWER_B):
        h.add_worker(worker_id, "coder")
    return h


def invite_payload(airing_id="airing-7", cast=None):
    return {"airing_id": airing_id, "episode": EPISODE,
            "cast": cast or {"boss": FOLLOWER_A, "coder": FOLLOWER_B},
            "speed": 1.0, "worker_name": "KODI-7", "director": DIRECTOR}


# ── bus / relay-file level ───────────────────────────────────────────────────
def test_duet_relay_invite_ready_cue_ratchet_end(harness):
    bus = harness.bus
    director, a, b = (bus.workers[w] for w in (DIRECTOR, FOLLOWER_A, FOLLOWER_B))

    # 1. invite -> each follower's request file (follow mode), nobody else's.
    for follower in (FOLLOWER_A, FOLLOWER_B):
        bus.inject(DIRECTOR, follower, "replay_invite", invite_payload())
    bus.run_until_quiet()
    for follower in (a, b):
        assert follower.read_relay("request") == {**invite_payload(), "mode": "follow"}
    assert director.read_relay("request") is None

    # 2. ready: the director's ready file unions senders for this airing.
    bus.inject(FOLLOWER_A, DIRECTOR, "replay_ready", {"airing_id": "airing-7"})
    bus.run_until_quiet()
    assert director.read_relay("ready") == {"airing_id": "airing-7", "workers": [FOLLOWER_A]}
    bus.inject(FOLLOWER_B, DIRECTOR, "replay_ready", {"airing_id": "airing-7"})
    bus.inject(FOLLOWER_B, DIRECTOR, "replay_ready", {"airing_id": "airing-7"})  # duplicate
    bus.run_until_quiet()
    assert director.read_relay("ready") == {"airing_id": "airing-7",
                                            "workers": [FOLLOWER_A, FOLLOWER_B]}
    assert a.read_relay("ready") is None  # ready only lands where it is addressed

    # 3. cue ratchet: overwrite-latest, a skipped cue just jumps ahead.
    for scene_index in (0, 1, 3):
        for follower in (FOLLOWER_A, FOLLOWER_B):
            bus.inject(DIRECTOR, follower, "replay_cue",
                       {"airing_id": "airing-7", "scene_index": scene_index})
        bus.run_until_quiet()
        for follower in (a, b):
            assert follower.read_relay("cue") == {"airing_id": "airing-7", "type": "cue",
                                                  "scene_index": scene_index}

    # 4. end replaces the last cue.
    for follower in (FOLLOWER_A, FOLLOWER_B):
        bus.inject(DIRECTOR, follower, "replay_end", {"airing_id": "airing-7", "reason": "finished"})
    bus.run_until_quiet()
    for follower in (a, b):
        assert follower.read_relay("cue") == {"airing_id": "airing-7", "type": "end",
                                              "reason": "finished"}
    assert director.read_relay("cue") is None

    # The relay handlers never answer on the bus.
    assert all(m["from"] in (DIRECTOR, FOLLOWER_A, FOLLOWER_B) for m in bus.log)
    assert len(bus.log) == 2 + 3 + 6 + 2
    assert bus.operator_inbox == []


def test_ready_for_a_new_airing_replaces_stale_ready_file(harness):
    bus = harness.bus
    director = bus.workers[DIRECTOR]
    bus.inject(FOLLOWER_A, DIRECTOR, "replay_ready", {"airing_id": "old-airing"})
    bus.inject(FOLLOWER_B, DIRECTOR, "replay_ready", {"airing_id": "new-airing"})
    bus.run_until_quiet()
    assert director.read_relay("ready") == {"airing_id": "new-airing", "workers": [FOLLOWER_B]}


def test_invite_is_dropped_when_follower_already_has_a_pending_request(harness):
    """Refusal precondition: a busy follower never gets queued (so it never
    sends replay_ready); the director's ready wait is what refuses."""
    bus = harness.bus
    follower = bus.workers[FOLLOWER_A]
    pending = {"episode": "someone-elses-show"}
    relay_io.atomic_write_json(follower.relay_path("request"), pending)

    bus.inject(DIRECTOR, FOLLOWER_A, "replay_invite", invite_payload())
    bus.run_until_quiet()

    assert follower.read_relay("request") == pending
    assert bus.of_type("replay_ready") == []


# ── full path: real director + follower pane code, concurrently ─────────────
def duet_request(harness, cast):
    return harness.bus.inject(OPERATOR, DIRECTOR, "replay_request",
                              {"episode": EPISODE, "cast": cast, "speed": 1000})


def test_full_duet_director_and_follower_perform_in_lockstep(harness):
    stage = DuetStage(harness, EPISODES)
    director, follower = harness.bus.workers[DIRECTOR], harness.bus.workers[FOLLOWER_A]
    stage.enable_pane(director)
    stage.enable_pane(follower)
    cast = {"boss": FOLLOWER_A, "coder": DIRECTOR}

    duet_request(harness, cast)
    stage.run()

    assert stage.errors == []
    assert stage.results == {DIRECTOR: [True], FOLLOWER_A: [True]}
    assert harness.bus.triples() == [
        (OPERATOR, DIRECTOR, "replay_request"),
        (DIRECTOR, OPERATOR, "operator_reply"),          # "queued" ack from the agent
        (DIRECTOR, FOLLOWER_A, "replay_invite"),
        (FOLLOWER_A, DIRECTOR, "replay_ready"),
        (DIRECTOR, FOLLOWER_A, "replay_cue"),
        (DIRECTOR, FOLLOWER_A, "replay_cue"),
        (DIRECTOR, FOLLOWER_A, "replay_end"),
    ]
    invite = harness.bus.of_type("replay_invite")[0]["payload"]
    airing_id = invite["airing_id"]
    assert airing_id in stage.airings
    assert invite["cast"] == cast and invite["director"] == DIRECTOR
    assert [m["payload"] for m in harness.bus.of_type("replay_cue")] == [
        {"airing_id": airing_id, "scene_index": 0}, {"airing_id": airing_id, "scene_index": 1}]
    assert harness.bus.of_type("replay_end")[0]["payload"] == {"airing_id": airing_id,
                                                              "reason": "finished"}

    # Ownership: exactly one performer voices each scene.
    (d_perf,) = stage.performer_for(DIRECTOR)
    (f_perf,) = stage.performer_for(FOLLOWER_A)
    assert d_perf.performed == f_perf.performed == [0, 1]
    assert [s["owned"] for s in d_perf.show] == [False, True]
    assert [s["owned"] for s in f_perf.show] == [True, False]
    assert d_perf.show[0]["audio"] is None  # unowned audio stripped on the director

    # Relay files as each "container" sees them afterwards.
    assert director.read_relay("ready") == {"airing_id": airing_id, "workers": [FOLLOWER_A]}
    assert follower.read_relay("cue") == {"airing_id": airing_id, "type": "end", "reason": "finished"}
    assert director.read_relay("request") is None and follower.read_relay("request") is None


def test_full_duet_refuses_when_follower_never_readies(harness):
    """Duets never degrade: the follower's pane is down, so the director
    times out on replay_ready and airs NOTHING (no solo fallback)."""
    stage = DuetStage(harness, EPISODES, ready_timeout_s=0.3)
    director, follower = harness.bus.workers[DIRECTOR], harness.bus.workers[FOLLOWER_A]
    stage.enable_pane(director)  # follower's pane never runs

    duet_request(harness, {"boss": FOLLOWER_A, "coder": DIRECTOR})
    stage.run()

    assert stage.errors == []
    assert stage.results == {DIRECTOR: [False]}
    assert stage.performers == []  # nothing performed anywhere
    assert harness.bus.triples() == [
        (OPERATOR, DIRECTOR, "replay_request"),
        (DIRECTOR, OPERATOR, "operator_reply"),
        (DIRECTOR, FOLLOWER_A, "replay_invite"),
        (DIRECTOR, FOLLOWER_A, "replay_end"),
        (DIRECTOR, OPERATOR, "operator_reply"),
    ]
    assert harness.bus.of_type("replay_cue") == []
    airing_id = harness.bus.of_type("replay_invite")[0]["payload"]["airing_id"]
    assert harness.bus.of_type("replay_end")[0]["payload"] == {"airing_id": airing_id,
                                                              "reason": "ready_timeout"}
    refusal = harness.bus.operator_inbox[-1]["payload"]
    assert "timed out waiting for duet followers" in refusal["error"]
    # The follower's agent still got the end: its stale invite can't start a show
    # that the director already abandoned (the cue file says end).
    assert follower.read_relay("cue") == {"airing_id": airing_id, "type": "end",
                                          "reason": "ready_timeout"}
    assert follower.read_relay("request")["mode"] == "follow"
