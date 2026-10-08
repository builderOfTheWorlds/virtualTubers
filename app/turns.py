"""Turn arbiter for the live agent table (build plan P3.4; agent_dnd_architecture
§4-5). Two-pass round (plan §1 row 4): DIRECTING -> THINK (all seats in
parallel, private) -> SPEAK (one seat at a time, turn order) -> ADJUDICATING.

The arbiter is STEP-DRIVEN: it never sleeps or waits. It advances only on
`on_message(msg)` (think_done / character_reply / operator_override) and on
`tick()` (deadline expiry, retry of a failed GM call). Ordering authority is
the arbiter alone; the bus is transport + audit. Every outbound message is
built through table.protocol.build (validated) and carries
`turn = f"r{round}.{phase}.{seat or 'gm'}"` and correlation_id = scene_id.

U6: the arbiter never receives private intent (think_done has none) and keeps
none. A seat reply is committed to the transcript only after check(); nothing
pending is ever visible in a turn_assignment.

State is a plain JSON dict persisted to a StateStore after every step, so
`Arbiter.resume(store, scene_id, ...)` continues a scene after a restart.
"""
import copy
import inspect
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from table import protocol
from table.state_store import InMemoryStateStore, RedisStateStore  # noqa: F401  (re-export)

log = logging.getLogger(__name__)

LOADED = "loaded"
DIRECTING = "directing"
THINK = "think"
SPEAK = "speak"
ADJUDICATING = "adjudicating"
RESOLVED = "resolved"
ABORTED = "aborted"
TERMINAL = (RESOLVED, ABORTED)

GM_FAILURES_BEFORE_STUCK = 2
_SEEN_IDS_MAX = 256


@dataclass
class Direction:
    text: str
    expects: list = field(default_factory=list)
    must_resolve: str = ""


@dataclass
class Verdict:
    verdict: str                      # 'pass' | 'reject'
    retake_seat: Optional[str] = None
    notes: str = ""
    state_delta: dict = field(default_factory=dict)
    resolved: bool = False


class GMPort(Protocol):
    def direct(self, contract: dict, transcript: list, round: int) -> Direction: ...

    def adjudicate(self, contract: dict, transcript: list, round: int) -> Verdict: ...

    def overrule(self, contract: dict, transcript: list, seat: str, reason: str) -> str: ...


class StateStore(Protocol):
    def load(self, key: str) -> Optional[dict]: ...

    def save(self, key: str, value: dict) -> None: ...


class GMUnavailable(Exception):
    """A GM port call failed; the step is retried on the next tick()."""


class Arbiter:
    def __init__(self, contract, seats, gm, check, send, clock, *, gm_seat="tuber_0",
                 think_s=60, speak_s=45, store=None, arbiter_id="arbiter",
                 broadcast_to="broadcast", operator_to="operator", on_commit=None, _state=None):
        # broadcast_to MUST be message_bus.BROADCAST ("broadcast"): MessageConsumer.poll_new
        # drops any message whose `to` is neither the worker id nor "broadcast", so a
        # custom audience like "table" would never reach a seat (found in review).
        self.gm = gm
        self.check = check
        # 2026-10-08: check(seat, text, prior=[committed texts]) when it accepts `prior`
        # (repeats rule); plain check(seat, text) still works.
        try:
            self._check_takes_prior = "prior" in inspect.signature(check).parameters
        except (TypeError, ValueError):
            self._check_takes_prior = False
        # on_commit(entry, scene_id): called the moment a line is committed (the
        # roundtable feed must not wait for the GM's next synchronous calls).
        self.on_commit = on_commit
        self.send = send
        self.clock = clock
        self.think_s = think_s
        self.speak_s = speak_s
        self.store = store if store is not None else InMemoryStateStore()
        self.arbiter_id = arbiter_id
        self.broadcast_to = broadcast_to
        self.operator_to = operator_to
        if _state is not None:
            self._s = _state
            return
        scene_id = contract.get("scene_id")
        if not isinstance(scene_id, str) or not scene_id:
            raise ValueError("contract.scene_id must be a non-empty str")
        players = [s for s in seats if s != gm_seat]
        if not players:
            raise ValueError("at least one non-GM seat is required")
        self._s = {
            "scene_id": scene_id,
            "contract": copy.deepcopy(contract),
            "seats": list(players),
            "gm_seat": gm_seat,
            "max_rounds": int(contract.get("max_rounds", 1)),
            "max_retries": int(contract.get("max_retries_per_turn", 2)),
            "phase": LOADED,
            "round": 0,
            "transcript": [],
            "direction": None,
            "think_done": [],
            "deadline": None,
            "speak_queue": [],
            "speaker": None,
            "retries": {},
            "pending_overrule": None,
            "adj_retake_used": False,
            "gm_failures": 0,
            "stuck": False,
            "resolved": None,
            "state_delta": {},
            "seen_ids": [],
        }

    # ------------------------------------------------------------------ API
    @property
    def state(self):
        return copy.deepcopy(self._s)

    @property
    def phase(self):
        return self._s["phase"]

    @classmethod
    def resume(cls, store, scene_id, gm, check, send, clock, **kwargs):
        saved = store.load(scene_id)
        if saved is None:
            raise KeyError(f"no arbiter state for scene {scene_id!r}")
        kwargs.pop("store", None)
        return cls(saved["contract"], saved["seats"], gm, check, send, clock,
                   gm_seat=saved["gm_seat"], store=store, _state=saved, **kwargs)

    def start(self):
        s = self._s
        if s["phase"] != LOADED:
            return
        self._emit("scene_start", self.broadcast_to, {
            "scene_id": s["scene_id"], "contract": s["contract"], "round": 1,
            "max_rounds": s["max_rounds"], "seats": list(s["seats"]),
        }, phase=LOADED, round_=1)
        self._begin_round(1)
        self._save()

    def on_message(self, msg):
        s = self._s
        if not isinstance(msg, dict) or s["phase"] in TERMINAL:
            return
        type_ = msg.get("type")
        if type_ not in ("think_done", "character_reply", "operator_override"):
            return
        try:
            payload = protocol.parse(msg)
        except protocol.ProtocolError as exc:
            log.info("arbiter ignore invalid %s: %s", type_, exc)
            return
        if payload["scene_id"] != s["scene_id"]:
            return
        msg_id = msg.get("id")
        if msg_id and msg_id in s["seen_ids"]:
            return
        if type_ == "operator_override":
            self._remember(msg_id)
            self._on_override(payload)
            self._save()
            return
        seat = payload["seat"]
        if seat not in s["seats"] or payload["round"] != s["round"]:
            return
        if msg.get("from") not in (None, seat):
            return
        self._remember(msg_id)
        if type_ == "think_done":
            self._on_think_done(seat)
        else:
            self._on_reply(seat, payload)
        self._save()

    def tick(self):
        s = self._s
        if s["phase"] in TERMINAL:
            return
        now = self.clock()
        if s["phase"] == THINK and s["deadline"] is not None and now >= s["deadline"]:
            missing = [x for x in s["seats"] if x not in s["think_done"]]
            log.info("arbiter think deadline scene=%s missing=%s", s["scene_id"], missing)
            self._begin_speak()
        elif s["phase"] == SPEAK and not s["stuck"]:
            if s["pending_overrule"]:
                self._overrule(s["pending_overrule"]["seat"], s["pending_overrule"]["reason"])
            elif s["deadline"] is not None and now >= s["deadline"]:
                self._fail_turn(s["speaker"], "deadline")
        elif s["phase"] == DIRECTING and not s["stuck"]:
            self._direct()
        elif s["phase"] == ADJUDICATING and not s["stuck"]:
            self._adjudicate()
        self._save()

    # ------------------------------------------------------------ internals
    def _save(self):
        self.store.save(self._s["scene_id"], self._s)

    def _remember(self, msg_id):
        if msg_id:
            self._s["seen_ids"] = (self._s["seen_ids"] + [msg_id])[-_SEEN_IDS_MAX:]

    def _turn(self, phase=None, seat=None, round_=None):
        r = self._s["round"] if round_ is None else round_
        return f"r{r}.{phase or self._s['phase']}.{seat or 'gm'}"

    def _emit(self, type_, to, payload, *, seat=None, phase=None, round_=None):
        msg = protocol.build(type_, self.arbiter_id, to, payload,
                             turn=self._turn(phase, seat, round_),
                             correlation_id=self._s["scene_id"])
        self.send(msg)
        return msg

    def _commit(self, entry):
        self._s["transcript"].append(entry)
        if self.on_commit is not None:
            try:
                self.on_commit(dict(entry), self._s["scene_id"])
            except Exception as exc:  # noqa: BLE001 - presentation never breaks the table
                log.error("arbiter on_commit failed scene=%s: %s", self._s["scene_id"], exc)

    def _gm_call(self, name, *args):
        s = self._s
        try:
            result = getattr(self.gm, name)(copy.deepcopy(s["contract"]), *args)
        except Exception as exc:  # noqa: BLE001 - any GM failure is a stuck candidate
            s["gm_failures"] += 1
            log.warning("arbiter GM %s failed (%d) scene=%s: %s",
                        name, s["gm_failures"], s["scene_id"], exc)
            if s["gm_failures"] >= GM_FAILURES_BEFORE_STUCK and not s["stuck"]:
                s["stuck"] = True
                self._emit("scene_stuck", self.operator_to, {
                    "scene_id": s["scene_id"],
                    "reason": f"gm {name} failed {s['gm_failures']}x: {exc}",
                    "state": self._snapshot(),
                })
            raise GMUnavailable(name) from exc
        s["gm_failures"] = 0
        return result

    def _snapshot(self):
        s = self._s
        return {"phase": s["phase"], "round": s["round"], "speaker": s["speaker"],
                "transcript_len": len(s["transcript"]), "max_rounds": s["max_rounds"]}

    def _begin_round(self, round_):
        s = self._s
        s.update(round=round_, phase=DIRECTING, think_done=[], speak_queue=[],
                 speaker=None, retries={}, adj_retake_used=False, deadline=None,
                 direction=None, pending_overrule=None)
        self._direct()

    def _direct(self):
        s = self._s
        try:
            d = self._gm_call("direct", copy.deepcopy(s["transcript"]), s["round"])
        except GMUnavailable:
            return
        expects = [x for x in (d.expects or []) if x in s["seats"]]
        s["direction"] = {"text": d.text, "expects": expects, "must_resolve": d.must_resolve or ""}
        self._emit("scene_direction", self.broadcast_to, {
            "scene_id": s["scene_id"], "round": s["round"], "speaker": s["gm_seat"],
            "text": d.text, "expects": expects, "must_resolve": d.must_resolve or "",
        })
        self._commit({"speaker": "gm", "text": d.text, "kind": "direction"})
        s["phase"] = THINK
        s["deadline"] = self.clock() + self.think_s
        self._emit("think_request", self.broadcast_to, {
            "scene_id": s["scene_id"], "round": s["round"], "seats": list(s["seats"]),
            "committed_transcript": copy.deepcopy(s["transcript"]),
            "deadline_s": self.think_s,
        })

    def _on_think_done(self, seat):
        s = self._s
        if s["phase"] != THINK or seat in s["think_done"]:
            return
        s["think_done"].append(seat)
        if all(x in s["think_done"] for x in s["seats"]):
            self._begin_speak()

    def _begin_speak(self):
        s = self._s
        expects = (s["direction"] or {}).get("expects") or []
        s["speak_queue"] = [x for x in s["seats"] if x in expects] if expects else list(s["seats"])
        s["phase"] = SPEAK
        self._next_speaker()

    def _next_speaker(self):
        s = self._s
        if not s["speak_queue"]:
            s["speaker"] = None
            s["deadline"] = None
            s["phase"] = ADJUDICATING
            self._adjudicate()
            return
        seat = s["speak_queue"].pop(0)
        s["speaker"] = seat
        s["deadline"] = self.clock() + self.speak_s
        self._emit("turn_assignment", seat, {
            "scene_id": s["scene_id"], "round": s["round"], "seat": seat,
            "order_pos": s["seats"].index(seat),
            "committed_transcript": copy.deepcopy(s["transcript"]),
            "instruction": (s["direction"] or {}).get("must_resolve", ""),
            "deadline_s": self.speak_s, "max_retries": s["max_retries"],
        }, seat=seat)

    def _on_reply(self, seat, payload):
        s = self._s
        if s["phase"] != SPEAK or seat != s["speaker"] or s["pending_overrule"]:
            return
        if not payload["took"]:
            self._fail_turn(seat, f"no_take: {payload['reason'] or 'declined'}")
            return
        if self._check_takes_prior:
            result = self.check(seat, payload["text"],
                                prior=[e["text"] for e in s["transcript"]])
        else:
            result = self.check(seat, payload["text"])
        if not result.ok:
            self._fail_turn(seat, result.reason or (result.code or "check_failed"))
            return
        self._commit({"speaker": seat, "text": payload["text"], "kind": "reply"})
        self._next_speaker()

    def _fail_turn(self, seat, reason):
        s = self._s
        retry = s["retries"].get(seat, 0) + 1
        s["retries"][seat] = retry
        if retry > s["max_retries"]:
            s["pending_overrule"] = {"seat": seat, "reason": reason}
            self._overrule(seat, reason)
            return
        s["deadline"] = self.clock() + self.speak_s
        self._emit("retake", seat, {
            "scene_id": s["scene_id"], "round": s["round"], "seat": seat,
            "reason": reason, "retry": retry, "max": s["max_retries"],
        }, seat=seat)

    def _overrule(self, seat, reason):
        s = self._s
        try:
            text = self._gm_call("overrule", copy.deepcopy(s["transcript"]), seat, reason)
        except GMUnavailable:
            return
        s["pending_overrule"] = None
        self._commit({"speaker": seat, "text": text, "kind": "overrule"})
        self._emit("gm_overrule", seat, {
            "scene_id": s["scene_id"], "round": s["round"], "seat": seat,
            "committed_text": text, "note": reason,
        }, seat=seat)
        self._next_speaker()

    def _adjudicate(self):
        s = self._s
        try:
            v = self._gm_call("adjudicate", copy.deepcopy(s["transcript"]), s["round"])
        except GMUnavailable:
            return
        # REQUIRED has only verdict+notes; extra keys ride along (validated envelope).
        self._emit("adjudication", self.broadcast_to, {
            "scene_id": s["scene_id"], "round": s["round"], "verdict": v.verdict,
            "notes": v.notes or "", "retake_seat": v.retake_seat,
            "resolved": bool(v.resolved), "state_delta": dict(v.state_delta or {}),
        })
        if v.verdict == "pass" and v.resolved:
            self._resolve(True, v.state_delta or {})
            return
        if (v.verdict == "reject" and v.retake_seat in s["seats"]
                and not s["adj_retake_used"]):
            s["adj_retake_used"] = True
            s["phase"] = SPEAK
            s["speak_queue"] = []
            s["speaker"] = v.retake_seat
            self._fail_turn(v.retake_seat, f"adjudication: {v.notes or 'rejected'}")
            return
        if s["round"] >= s["max_rounds"]:
            self._resolve(False, v.state_delta or {})
            return
        self._begin_round(s["round"] + 1)

    def _resolve(self, resolved, state_delta):
        s = self._s
        s["phase"] = RESOLVED
        s["resolved"] = bool(resolved)
        s["state_delta"] = dict(state_delta)
        s["deadline"] = None
        s["speaker"] = None
        s["stuck"] = False
        self._emit("scene_resolve", self.broadcast_to, {
            "scene_id": s["scene_id"], "resolved": bool(resolved),
            "state_delta": dict(state_delta), "record_id": None,
        })

    def _on_override(self, payload):
        s = self._s
        action = payload["action"]
        log.info("arbiter operator_override scene=%s action=%s", s["scene_id"], action)
        if action == "accept_all":
            self._resolve(True, {})
        elif action == "skip_scene":
            self._resolve(False, {})
        elif action == "abort_session":
            s["phase"] = ABORTED
            s["deadline"] = None
            s["speaker"] = None
            s["stuck"] = False
