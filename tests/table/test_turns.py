"""P3.4 tests for app/turns.py: the step-driven turn arbiter (two-pass round).

Fake clock, fake GM, in-memory store. No Kafka / Redis / LLM.
"""
import pytest

from table import protocol
from table.commit_check import CheckResult
from table.state_store import InMemoryStateStore, RedisStateStore
from message_bus import build_message
from turns import (ABORTED, ADJUDICATING, DIRECTING, RESOLVED, SPEAK, THINK, Arbiter,
                   Direction, Verdict)

SCENE = "s1"
SEATS = ["tuber_0", "tuber_1", "tuber_2"]  # tuber_0 is the GM seat
PLAYERS = ["tuber_1", "tuber_2"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeGM:
    def __init__(self, verdicts=None, expects=None, fail=None):
        self.verdicts = list(verdicts or [Verdict("pass", None, "ok", {"door": "open"}, True)])
        self.expects = expects or []
        self.fail = dict(fail or {})   # method -> remaining failures
        self.calls = []

    def _maybe_fail(self, name):
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            raise RuntimeError(f"{name} down")

    def direct(self, contract, transcript, round):
        self.calls.append(("direct", round))
        self._maybe_fail("direct")
        return Direction(f"Round {round}: the door creaks.", list(self.expects), "open the door")

    def adjudicate(self, contract, transcript, round):
        self.calls.append(("adjudicate", round))
        self._maybe_fail("adjudicate")
        return self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]

    def overrule(self, contract, transcript, seat, reason):
        self.calls.append(("overrule", seat, reason))
        self._maybe_fail("overrule")
        return f"{seat} nods silently."


def contract(max_rounds=1, max_retries=2):
    return {"scene_id": SCENE, "max_rounds": max_rounds, "max_retries_per_turn": max_retries}


class Harness:
    def __init__(self, gm=None, check=None, max_rounds=1, max_retries=2, store=None):
        self.clock = Clock()
        self.sent = []
        self.gm = gm or FakeGM()
        self.store = store or InMemoryStateStore()
        self.check = check or (lambda seat, text: CheckResult(True, None, ""))
        self.arb = Arbiter(contract(max_rounds, max_retries), SEATS, self.gm, self.check,
                           self.sent.append, self.clock, store=self.store)

    def of(self, type_):
        return [m for m in self.sent if m["type"] == type_]

    def last(self, type_):
        return self.of(type_)[-1]

    def think_all(self, seats=PLAYERS, round_=None):
        r = self.arb.state["round"] if round_ is None else round_
        for seat in seats:
            self.arb.on_message(think_done(seat, r))

    def reply(self, seat, text, round_=None, **kw):
        r = self.arb.state["round"] if round_ is None else round_
        self.arb.on_message(reply(seat, text, r, **kw))


def think_done(seat, round_, scene=SCENE):
    return protocol.build("think_done", seat, "arbiter",
                          {"scene_id": scene, "round": round_, "seat": seat, "ok": True})


def reply(seat, text, round_, scene=SCENE, took=True, from_=None):
    return protocol.build("character_reply", from_ or seat, "arbiter",
                          {"scene_id": scene, "round": round_, "seat": seat, "text": text,
                           "took": took, "reason": ""})


def override(action):
    return protocol.build("operator_override", "operator", "arbiter",
                          {"scene_id": SCENE, "action": action, "note": "op"})


def assert_all_valid(sent):
    for m in sent:
        protocol.parse(m)
        assert m["correlation_id"] == SCENE
        assert isinstance(m["turn"], str) and m["turn"].startswith("r")
        for key in protocol.PRIVATE_KEYS:
            assert key not in str(m["payload"].keys())


# --------------------------------------------------------------- happy path
def test_happy_path_one_round_two_seats():
    h = Harness()
    h.arb.start()
    assert [m["type"] for m in h.sent] == ["scene_start", "scene_direction", "think_request"]
    assert h.arb.phase == THINK
    tr = h.last("think_request")["payload"]
    assert tr["seats"] == PLAYERS
    assert tr["committed_transcript"] == [
        {"speaker": "gm", "text": "Round 1: the door creaks.", "kind": "direction"}]
    h.think_all()
    assert h.arb.phase == SPEAK
    ta = h.last("turn_assignment")
    assert ta["to"] == "tuber_1" and ta["turn"] == "r1.speak.tuber_1"
    h.reply("tuber_1", "I'll try the latch.")
    h.reply("tuber_2", "Careful, it may be trapped.")
    assert h.arb.phase == RESOLVED
    res = h.last("scene_resolve")["payload"]
    assert res == {"scene_id": SCENE, "resolved": True, "state_delta": {"door": "open"},
                   "record_id": None}
    kinds = [(e["speaker"], e["kind"]) for e in h.arb.state["transcript"]]
    assert kinds == [("gm", "direction"), ("tuber_1", "reply"), ("tuber_2", "reply")]
    assert h.of("scene_direction")[0]["turn"] == "r1.directing.gm"
    assert h.of("think_request")[0]["turn"] == "r1.think.gm"
    assert h.of("adjudication")[0]["turn"] == "r1.adjudicating.gm"
    assert_all_valid(h.sent)


def test_order_and_committed_transcript_by_value():
    h = Harness()
    h.arb.start()
    h.think_all(["tuber_2", "tuber_1"])  # think order irrelevant
    # an out-of-turn reply from tuber_2 is ignored
    h.reply("tuber_2", "Me first!")
    assert h.arb.state["speaker"] == "tuber_1"
    assert [m["to"] for m in h.of("turn_assignment")] == ["tuber_1"]
    h.reply("tuber_1", "Line one.")
    h.reply("tuber_2", "Line two.")
    tas = h.of("turn_assignment")
    assert [m["to"] for m in tas] == ["tuber_1", "tuber_2"]
    assert [m["payload"]["order_pos"] for m in tas] == [0, 1]
    assert tas[0]["payload"]["committed_transcript"] == h.arb.state["transcript"][:1]
    assert tas[1]["payload"]["committed_transcript"] == h.arb.state["transcript"][:2]
    assert "Me first!" not in str(h.sent)
    assert_all_valid(h.sent)


def test_expects_limits_speakers():
    h = Harness(gm=FakeGM(expects=["tuber_2"]))
    h.arb.start()
    h.think_all()
    assert [m["to"] for m in h.of("turn_assignment")] == ["tuber_2"]
    h.reply("tuber_2", "Only me.")
    assert h.arb.phase == RESOLVED


# --------------------------------------------------------------- failures
def test_commit_check_failure_retakes_with_reason_then_commits():
    def check(seat, text):
        if "*" in text:
            return CheckResult(False, "stage_direction", "stage_direction: *waves*")
        return CheckResult(True, None, "")
    h = Harness(check=check)
    h.arb.start()
    h.think_all()
    h.reply("tuber_1", "*waves* Hello")
    rt = h.last("retake")
    assert rt["to"] == "tuber_1"
    assert rt["payload"]["reason"] == "stage_direction: *waves*"
    assert rt["payload"]["retry"] == 1 and rt["payload"]["max"] == 2
    assert all(e["text"] != "*waves* Hello" for e in h.arb.state["transcript"])
    h.reply("tuber_1", "Hello.")
    assert h.arb.state["transcript"][-1] == {"speaker": "tuber_1", "text": "Hello.",
                                             "kind": "reply"}
    assert h.last("turn_assignment")["to"] == "tuber_2"
    assert "*waves*" not in str(h.last("turn_assignment")["payload"])
    assert_all_valid(h.sent)


def test_speak_deadline_retakes_with_deadline_reason():
    h = Harness()
    h.arb.start()
    h.think_all()
    h.clock.t += 44
    h.arb.tick()
    assert not h.of("retake")
    h.clock.t += 2
    h.arb.tick()
    assert h.last("retake")["payload"]["reason"] == "deadline"
    assert h.arb.state["speaker"] == "tuber_1"


def test_overrule_after_max_retries():
    h = Harness(check=lambda s, t: CheckResult(False, "meta", "meta: ooc"), max_retries=2)
    h.arb.start()
    h.think_all()
    for _ in range(3):
        h.reply("tuber_1", "OOC stuff")
    assert len(h.of("retake")) == 2
    ov = h.last("gm_overrule")["payload"]
    assert ov["seat"] == "tuber_1" and ov["committed_text"] == "tuber_1 nods silently."
    assert ov["note"] == "meta: ooc"
    assert h.arb.state["transcript"][-1] == {"speaker": "tuber_1",
                                             "text": "tuber_1 nods silently.",
                                             "kind": "overrule"}
    assert h.last("turn_assignment")["to"] == "tuber_2"
    assert_all_valid(h.sent)


def test_think_deadline_with_missing_seat_proceeds():
    h = Harness()
    h.arb.start()
    h.think_all(["tuber_2"])
    assert h.arb.phase == THINK
    h.clock.t += 59
    h.arb.tick()
    assert h.arb.phase == THINK
    h.clock.t += 1
    h.arb.tick()
    assert h.arb.phase == SPEAK
    assert h.last("turn_assignment")["to"] == "tuber_1"
    # a late think_done is ignored
    h.arb.on_message(think_done("tuber_1", 1))
    assert h.arb.phase == SPEAK


def test_adjudication_reject_retakes_seat_once():
    gm = FakeGM(verdicts=[Verdict("reject", "tuber_1", "contradicts the door", {}, False),
                          Verdict("pass", None, "fine", {"x": 1}, True)])
    h = Harness(gm=gm)
    h.arb.start()
    h.think_all()
    h.reply("tuber_1", "The door is a wall.")
    h.reply("tuber_2", "Okay.")
    assert h.arb.phase == SPEAK
    rt = h.last("retake")
    assert rt["to"] == "tuber_1" and rt["payload"]["reason"] == "adjudication: contradicts the door"
    assert rt["payload"]["retry"] == 1
    adj = h.of("adjudication")[0]["payload"]
    assert adj["verdict"] == "reject" and adj["retake_seat"] == "tuber_1"
    h.reply("tuber_2", "ignored, not my turn")
    h.reply("tuber_1", "The door is a door.")
    assert h.arb.phase == RESOLVED
    assert h.last("scene_resolve")["payload"]["state_delta"] == {"x": 1}
    assert_all_valid(h.sent)


def test_max_rounds_resolves_unresolved():
    gm = FakeGM(verdicts=[Verdict("pass", None, "not yet", {}, False)])
    h = Harness(gm=gm, max_rounds=2)
    h.arb.start()
    for r in (1, 2):
        assert h.arb.state["round"] == r
        h.think_all()
        h.reply("tuber_1", "a")
        h.reply("tuber_2", "b")
    assert h.arb.phase == RESOLVED
    assert h.last("scene_resolve")["payload"]["resolved"] is False
    assert len(h.of("scene_direction")) == 2
    assert h.of("scene_direction")[1]["turn"] == "r2.directing.gm"
    # round 2 think_request carries the full committed transcript from round 1
    assert len(h.of("think_request")[1]["payload"]["committed_transcript"]) == 4
    assert_all_valid(h.sent)


# --------------------------------------------------------------- override
@pytest.mark.parametrize("action,phase,resolved", [
    ("accept_all", RESOLVED, True), ("skip_scene", RESOLVED, False),
    ("abort_session", ABORTED, None)])
def test_operator_override(action, phase, resolved):
    h = Harness()
    h.arb.start()
    h.think_all()
    h.arb.on_message(override(action))
    assert h.arb.phase == phase
    if resolved is None:
        assert not h.of("scene_resolve")
    else:
        assert h.last("scene_resolve")["payload"]["resolved"] is resolved
    n = len(h.sent)
    h.reply("tuber_1", "too late")
    h.arb.tick()
    assert len(h.sent) == n
    assert_all_valid(h.sent)


# --------------------------------------------------------------- ignoring
def test_stale_duplicate_unknown_messages_ignored():
    h = Harness()
    h.arb.start()
    h.arb.on_message(think_done("tuber_1", 1, scene="other"))
    h.arb.on_message(think_done("tuber_1", 5))
    h.arb.on_message(think_done("tuber_9", 1))
    h.arb.on_message(think_done("tuber_0", 1))  # GM seat is not a player seat
    h.arb.on_message(build_message("tuber_1", "arbiter", "task", {}))
    h.arb.on_message({"type": "think_done", "payload": {"scene_id": SCENE}})  # invalid
    h.arb.on_message("garbage")
    assert h.arb.state["think_done"] == []
    td = think_done("tuber_1", 1)
    h.arb.on_message(td)
    h.arb.on_message(td)
    assert h.arb.state["think_done"] == ["tuber_1"]
    h.arb.on_message(think_done("tuber_2", 1))
    assert h.arb.phase == SPEAK
    r = reply("tuber_1", "Hi.", 1)
    h.arb.on_message(r)
    h.arb.on_message(r)  # duplicate delivery
    assert [e["speaker"] for e in h.arb.state["transcript"]] == ["gm", "tuber_1"]
    h.arb.on_message(reply("tuber_2", "spoofed", 1, from_="tuber_1"))  # wrong sender
    h.arb.on_message(reply("tuber_2", "stale", 0))
    assert h.arb.state["speaker"] == "tuber_2" and len(h.arb.state["transcript"]) == 2


def test_redirected_types_are_ignored():
    h = Harness()
    h.arb.start()
    n = len(h.sent)
    for m in list(h.sent):
        h.arb.on_message(m)   # its own outbound echoes are not inputs
    assert len(h.sent) == n and h.arb.phase == THINK


# --------------------------------------------------------------- resume
def test_restart_resume_mid_speak_finishes_scene():
    h = Harness()
    h.arb.start()
    h.think_all()
    h.reply("tuber_1", "First line.")
    assert h.arb.phase == SPEAK
    sent2 = []
    arb2 = Arbiter.resume(h.store, SCENE, h.gm, h.check, sent2.append, h.clock)
    assert arb2.state["speaker"] == "tuber_2"
    assert arb2.state["transcript"] == h.arb.state["transcript"]
    arb2.on_message(reply("tuber_2", "Second line.", 1))
    assert arb2.phase == RESOLVED
    assert [m["type"] for m in sent2] == ["adjudication", "scene_resolve"]
    # the deadline survives the restart too
    h2 = Harness()
    h2.arb.start()
    h2.think_all()
    arb3 = Arbiter.resume(h2.store, SCENE, h2.gm, h2.check, sent2.append, h2.clock)
    h2.clock.t += 46
    arb3.tick()
    assert sent2[-1]["type"] == "retake" and sent2[-1]["payload"]["reason"] == "deadline"
    assert_all_valid(sent2)


def test_resume_unknown_scene_raises():
    with pytest.raises(KeyError):
        Arbiter.resume(InMemoryStateStore(), "nope", FakeGM(), None, None, Clock())


# --------------------------------------------------------------- stuck
def test_scene_stuck_on_repeated_gm_failure():
    gm = FakeGM(fail={"direct": 5})
    h = Harness(gm=gm)
    h.arb.start()
    assert h.arb.phase == DIRECTING and not h.of("scene_stuck")
    h.arb.tick()  # second failure -> stuck
    stuck = h.of("scene_stuck")
    assert len(stuck) == 1 and stuck[0]["to"] == "operator"
    assert "direct" in stuck[0]["payload"]["reason"]
    assert h.arb.phase == DIRECTING and h.arb.state["stuck"] is True
    h.arb.tick()
    h.arb.tick()
    assert len(h.of("scene_stuck")) == 1 and h.arb.phase == DIRECTING
    h.arb.on_message(override("skip_scene"))
    assert h.arb.phase == RESOLVED
    assert_all_valid(h.sent)


def test_single_gm_failure_recovers_on_tick():
    gm = FakeGM(fail={"adjudicate": 1})
    h = Harness(gm=gm)
    h.arb.start()
    h.think_all()
    h.reply("tuber_1", "a")
    h.reply("tuber_2", "b")
    assert h.arb.phase == ADJUDICATING
    h.arb.tick()
    assert h.arb.phase == RESOLVED and not h.of("scene_stuck")


def test_no_private_intent_in_state():
    h = Harness()
    h.arb.start()
    h.think_all()
    for key in protocol.PRIVATE_KEYS:
        assert f"'{key}'" not in str(h.arb.state)


# --------------------------------------------------------------- stores
class TinyRedis:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v.encode("utf-8")


def test_in_memory_store_roundtrip_is_by_value():
    s = InMemoryStateStore()
    v = {"a": [1]}
    s.save("k", v)
    v["a"].append(2)
    assert s.load("k") == {"a": [1]} and s.load("missing") is None


def test_redis_store_with_fake_client_and_resume():
    client = TinyRedis()
    store = RedisStateStore(prefix="table:", client=client)
    store.save("k", {"x": 1})
    assert "table:k" in client.d and store.load("k") == {"x": 1}
    assert store.load("nope") is None
    h = Harness(store=store)
    h.arb.start()
    h.think_all()
    arb2 = Arbiter.resume(store, SCENE, h.gm, h.check, [].append, h.clock)
    assert arb2.phase == SPEAK



def test_table_broadcasts_use_the_bus_broadcast_address():
    """Seats only receive `to` in (their worker id, "broadcast") (message_bus.poll_new)."""
    from message_bus import BROADCAST
    import inspect
    import turns
    assert inspect.signature(turns.Arbiter.__init__).parameters["broadcast_to"].default == BROADCAST
