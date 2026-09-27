"""
tests/e2e_harness.py
In-memory end-to-end harness for multi-agent flows (docs/e2e_tests.md).

Runs the REAL handler code (app/agent_handlers/, and app/replay_pane.py's
duet director/follower paths) across several simulated workers, with no
tmux, Kafka, Redis, Postgres, LLM, coding tool, pytest subprocess or network:

- InMemoryBus    routes build_message() dicts exactly like the real consumer
                 (app/message_bus.py MessageConsumer.poll_new: a worker sees a
                 message iff msg["to"] is its worker_id or "broadcast" -- the
                 sender included) and calls MESSAGE_HANDLERS with the same
                 signature app/agent.py main() uses. "operator" has no
                 consumer: those messages land in bus.operator_inbox.
- ScriptedLLM    deterministic replies, scripted failures.
- FakeCodingBackend / FakeTestRunner   scripted TaskResult / TestRunResult.
- Per-worker relay files: every simulated worker has its own REPLAY_*_FILE
                 "environment". relay_io.resolve_path is patched to look the
                 env var up in the CURRENT simulated worker's env first (a
                 thread-local set while a worker's handler or pane runs), so
                 two "containers" never share a relay file even when their
                 panes run concurrently in threads.
- DuetStage      optional: runs replay_pane.perform_request for workers whose
                 "pane" is enabled, in threads, with a lockstep fake Performer.

This module is a helper, not a test file (no test_ prefix), so pytest never
collects it; tests import it as `from e2e_harness import ...` (tests/ is on
sys.path via pytest's rootdir-relative import mode).
"""
import collections
import itertools
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import relay_io
from agent_handlers import MESSAGE_HANDLERS
from agent_handlers import coder as coder_handlers
from agent_handlers import tester as tester_handlers
from coding_backend import TaskResult
from message_bus import BROADCAST, build_message
from test_runner import TestRunResult

OPERATOR = "operator"

RELAY_FILES = {
    "request": relay_io.REPLAY_REQUEST_FILE_ENV,
    "stop": relay_io.REPLAY_STOP_FILE_ENV,
    "cue": relay_io.REPLAY_CUE_FILE_ENV,
    "ready": relay_io.REPLAY_READY_FILE_ENV,
}


class BusDidNotSettle(AssertionError):
    """The bus was still busy after max_steps deliveries -- almost always a
    message loop (e.g. a retry cap that never trips)."""


class LLMDown(RuntimeError):
    """What ScriptedLLM raises for a scripted failure."""


# ── simulated-worker context (thread-local) ──────────────────────────────────
_ctx = threading.local()


def current_worker():
    return getattr(_ctx, "worker", None)


@contextmanager
def acting_as(worker):
    """Run the body as `worker`: relay paths resolve to its own files."""
    previous = getattr(_ctx, "worker", None)
    _ctx.worker = worker
    try:
        yield worker
    finally:
        _ctx.worker = previous


def _per_worker_resolve_path(env_var, default):
    """Drop-in for relay_io.resolve_path: the current simulated worker's env
    wins, then the real process env, then the default."""
    worker = current_worker()
    if worker is not None and worker.env.get(env_var):
        return worker.env[env_var]
    return os.environ.get(env_var) or default


# ── fakes ────────────────────────────────────────────────────────────────────
def llm_reply(line, emotion="neutral"):
    """A well-formed structured reply (emotion.parse_structured_reply shape)."""
    return json.dumps({"line": line, "emotion": emotion})


class ScriptedLLM:
    """Deterministic llm_client stand-in.

    - `replies`: consumed in order; each item is a raw reply string or an
      exception instance (raised). When exhausted, a default structured reply
      naming the worker is returned.
    - `fail_on`: substrings; any prompt containing one raises LLMDown (checked
      before `replies`), so a test can break exactly one handler's LLM call.
    - `down=True`: every call raises LLMDown.
    Every call's user prompt is recorded in `.prompts`.
    """

    def __init__(self, name="worker", replies=(), fail_on=(), down=False):
        self.name = name
        self.replies = collections.deque(replies)
        self.fail_on = tuple(fail_on)
        self.down = down
        self.prompts = []

    def complete(self, system_prompt, messages):
        prompt = messages[-1]["content"] if messages else ""
        self.prompts.append(prompt)
        if self.down or any(marker in prompt for marker in self.fail_on):
            raise LLMDown(f"{self.name} LLM unavailable")
        if self.replies:
            reply = self.replies.popleft()
            if isinstance(reply, BaseException):
                raise reply
            return reply
        return llm_reply(f"{self.name} narrates call #{len(self.prompts)}", "happy")


def ok_result(commit, backend="fake", **overrides):
    fields = dict(backend=backend, success=True, commit=commit, committed=True,
                  files_changed=1, insertions=3, deletions=1, duration_s=0.1, output="ok")
    fields.update(overrides)
    return TaskResult(**fields)


def failed_result(error, backend="fake", **overrides):
    fields = dict(backend=backend, success=False, commit=None, committed=False,
                  duration_s=0.1, output="", error=error)
    fields.update(overrides)
    return TaskResult(**fields)


class FakeCodingBackend:
    """coding_backend stand-in: `run_task` returns scripted TaskResults, then
    successes with deterministic commit ids (<worker>-c1, <worker>-c2, ...)."""

    name = "fake"

    def __init__(self, owner, results=(), workspace=None):
        self.owner = owner
        self.results = collections.deque(results)
        self.workspace = workspace or f"/data/repos/{owner}"
        self.tasks = []

    def run_task(self, task):
        self.tasks.append(task)
        if self.results:
            return self.results.popleft()
        return ok_result(f"{self.owner}-c{len(self.tasks)}")


def pytest_pass():
    return TestRunResult(ran=True, passed=True, exit_code=0, summary="3 passed")


def pytest_fail(*failed):
    failed = list(failed) or ["tests/test_calc.py::test_divide"]
    return TestRunResult(ran=True, passed=False, exit_code=1, failed_tests=failed,
                         summary=f"{len(failed)} failed")


class FakeTestRunner:
    """Replaces test_runner.workspace_testable/run_pytest as the tester
    handler module sees them. `outcomes` is a list (shared by every
    workspace) or a {workspace: list} dict; items are TestRunResult, True
    (pass) or False (fail). When a list runs out, `default` applies."""

    __test__ = False  # not a pytest test class

    def __init__(self, outcomes=(), default=True):
        self.outcomes = outcomes
        self.default = default
        self.runs = []  # workspaces, in call order
        self._queues = {}

    def _next(self, workspace):
        if isinstance(self.outcomes, dict):
            queue = self._queues.setdefault(
                workspace, collections.deque(self.outcomes.get(workspace, ())))
        else:
            queue = self._queues.setdefault(None, collections.deque(self.outcomes))
        outcome = queue.popleft() if queue else self.default
        if isinstance(outcome, TestRunResult):
            return outcome
        return pytest_pass() if outcome else pytest_fail()

    def workspace_testable(self, workspace):
        return True

    def run_pytest(self, workspace, *args, **kwargs):
        self.runs.append(workspace)
        return self._next(workspace)


# ── workers + bus ────────────────────────────────────────────────────────────
@dataclass(eq=False)
class SimWorker:
    worker_id: str
    role: str
    agent_config: dict
    llm: ScriptedLLM
    coding_backend: object = None
    state_path: str = None
    env: dict = field(default_factory=dict)
    received: list = field(default_factory=list)

    def relay_path(self, kind):
        """Path of this worker's request/stop/cue/ready relay file."""
        return self.env[RELAY_FILES[kind]]

    def read_relay(self, kind):
        return relay_io.read_json(self.relay_path(kind))


class BusProducer:
    """What a handler / pane gets as `producer`: send() publishes to the bus."""

    def __init__(self, bus, owner):
        self.bus = bus
        self.owner = owner

    def send(self, message):
        self.bus.publish(message)
        return message


class InMemoryBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._queue = collections.deque()
        self.workers = {}
        self.log = []              # every message published, in order
        self.deliveries = []       # (recipient worker_id, message)
        self.operator_inbox = []   # messages addressed to "operator"
        self.undelivered = []      # addressed to nobody that consumes

    def add_worker(self, worker):
        self.workers[worker.worker_id] = worker
        return worker

    def producer_for(self, worker_id):
        return BusProducer(self, worker_id)

    def publish(self, message):
        with self._lock:
            self._queue.append(message)
            self.log.append(message)

    def inject(self, from_, to, type_, payload=None):
        """External injection (message-api POST /messages): a chain starter."""
        message = build_message(from_, to, type_, payload or {})
        self.publish(message)
        return message

    def pending(self):
        with self._lock:
            return len(self._queue)

    def recipients(self, message):
        to = message.get("to")
        return [w for w in self.workers.values() if to in (w.worker_id, BROADCAST)]

    def deliver(self, message):
        if message.get("to") == OPERATOR:
            self.operator_inbox.append(message)
            return
        recipients = self.recipients(message)
        if not recipients:
            self.undelivered.append(message)
        for worker in recipients:
            worker.received.append(message)
            self.deliveries.append((worker.worker_id, message))
            handler = MESSAGE_HANDLERS.get(message.get("type"))
            if handler is None:
                continue
            with acting_as(worker):
                handler(worker.worker_id, worker.agent_config, worker.llm,
                        self.producer_for(worker.worker_id), message, worker.state_path,
                        coding_backend=worker.coding_backend)

    def step(self):
        with self._lock:
            if not self._queue:
                return False
            message = self._queue.popleft()
        self.deliver(message)
        return True

    def run_until_quiet(self, max_steps=200):
        """Deliver FIFO until nothing is queued. Returns the number of
        messages delivered; raises BusDidNotSettle past max_steps."""
        steps = 0
        while self.step():
            steps += 1
            if steps > max_steps:
                raise BusDidNotSettle(f"bus still busy after {max_steps} deliveries: "
                                      f"{self.triples()[-10:]}")
        return steps

    # ── queries ──────────────────────────────────────────────────────────
    def triples(self, messages=None):
        return [(m["from"], m["to"], m["type"]) for m in (self.log if messages is None else messages)]

    def of_type(self, type_):
        return [m for m in self.log if m["type"] == type_]

    def by_id(self, message_id):
        return next((m for m in self.log if m["id"] == message_id), None)

    def chain(self, correlation_id):
        return [m for m in self.log if m["correlation_id"] == correlation_id]

    def lineage(self, message):
        """Types from the chain root down to `message`, following causation_id."""
        path = []
        seen = set()
        while message is not None and message["id"] not in seen:
            seen.add(message["id"])
            path.append(message["type"])
            message = self.by_id(message["causation_id"]) if message.get("causation_id") else None
        return list(reversed(path))

    def assert_chain_consistent(self, root):
        """Every message on root's chain links (causation_id) to an EARLIER
        message on the same chain; root itself starts the chain."""
        assert root["correlation_id"] == root["id"] and root["causation_id"] is None
        position = {m["id"]: i for i, m in enumerate(self.log)}
        chain = self.chain(root["id"])
        assert chain and chain[0] is root
        for message in chain[1:]:
            parent = self.by_id(message["causation_id"])
            assert parent is not None, f"{message['type']} has dangling causation_id"
            assert parent["correlation_id"] == root["id"], f"{message['type']} parent off-chain"
            assert position[parent["id"]] < position[message["id"]]
        return chain


class E2EHarness:
    """Wires workers onto an InMemoryBus with every external dependency faked.
    Construct inside a test with pytest's tmp_path + monkeypatch."""

    def __init__(self, tmp_path, monkeypatch, test_runner=None):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.bus = InMemoryBus()
        self.tmux_calls = []
        self.test_runner = test_runner or FakeTestRunner()

        # Relay files: per-worker env (see module docstring). Point the real
        # env vars at a scratch dir too, so anything resolved OUTSIDE a
        # simulated worker can never touch the /tmp defaults.
        monkeypatch.setattr(relay_io, "resolve_path", _per_worker_resolve_path)
        unrouted = tmp_path / "_unrouted"
        unrouted.mkdir()
        for kind, env_var in RELAY_FILES.items():
            monkeypatch.setenv(env_var, str(unrouted / f"replay_{kind}.json"))

        # tmux: the coder's demo helpers look these names up in their own module.
        for name in ("select_pane", "send_keys", "send_raw", "send_command"):
            monkeypatch.setattr(coder_handlers, name, self._tmux_recorder(name))

        # Tester: real pytest subprocess -> scripted verdicts.
        monkeypatch.setattr(tester_handlers, "workspace_testable", self.test_runner.workspace_testable)
        monkeypatch.setattr(tester_handlers, "run_pytest", self.test_runner.run_pytest)

    def _tmux_recorder(self, name):
        def record(*args, **kwargs):
            self.tmux_calls.append((name, args))
        return record

    def add_worker(self, worker_id, role, llm=None, coding_backend=None, **agent_config):
        root = self.tmp_path / worker_id
        root.mkdir()
        env = {env_var: str(root / f"replay_{kind}.json") for kind, env_var in RELAY_FILES.items()}
        worker = SimWorker(
            worker_id=worker_id,
            role=role,
            agent_config={"role": role, "system_prompt": f"You are {worker_id}.", **agent_config},
            llm=llm or ScriptedLLM(worker_id),
            coding_backend=coding_backend,
            state_path=str(root / "agent_state.json"),
            env=env,
        )
        return self.bus.add_worker(worker)

    def add_coder(self, worker_id="coder", llm=None, backend=None, results=()):
        backend = backend if backend is not None else FakeCodingBackend(worker_id, results=results)
        return self.add_worker(worker_id, "coder", llm=llm, coding_backend=backend)

    def add_tester(self, worker_id="tester", llm=None):
        return self.add_worker(worker_id, "tester", llm=llm)

    def add_manager(self, worker_id="manager", llm=None):
        return self.add_worker(worker_id, "manager", llm=llm)

    def dev_team(self, coders=("coder",), manager_llm=None, coder_llm=None, tester_llm=None):
        """Standard team: the given coders + "tester" + "manager"."""
        team = {c: self.add_coder(c, llm=coder_llm) for c in coders}
        team["tester"] = self.add_tester(llm=tester_llm)
        team["manager"] = self.add_manager(llm=manager_llm)
        return team

    def assign(self, coder_id, task):
        """Operator -> message-api -> task_assignment (a new chain)."""
        return self.bus.inject(OPERATOR, coder_id, "task_assignment", {"task": task})


# ── duet: real replay_pane director/follower paths, run concurrently ────────
class LockstepPerformer:
    """replay.Performer stand-in. Director (has on_scene_start): cue scene i,
    then wait until every other live pane has performed scene i -- standing
    in for "the director is busy performing scene i" so the follower is
    never out-raced by the end message. Follower (has wait_for_scene): wait
    for the cue ratchet, record the scene. No audio, no terminal output."""

    stage = None  # set by DuetStage

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.worker = current_worker()
        self.show = None
        self.performed = []
        self.completed = None
        self.stage.performers.append(self)

    def perform(self, script, show=None, start=0, limit=None):
        self.show = show or []
        on_scene_start = self.kwargs.get("on_scene_start")
        wait_for_scene = self.kwargs.get("wait_for_scene")
        for index in range(len(self.show)):
            if on_scene_start is not None:
                on_scene_start(index)
                self.stage.wait_for_others(self.worker, index)
            if wait_for_scene is not None and wait_for_scene(index) == -1:
                self.completed = False
                return False
            self.performed.append(index)
            self.stage.record_progress(self.worker, index)
        self.completed = True
        return True


class FakeAudio:
    """Duck-typed tts_client.Narration: the director only reads .duration."""

    def __init__(self, duration):
        self.duration = duration
        self.audio_path = None


class DuetStage:
    """Runs the real replay_pane.perform_request for workers whose pane is
    enabled, one thread per request, alongside the bus pump. Fakes: episode
    library, narration store (in-memory airings), voice prep, voice gate,
    Kafka producer (-> the in-memory bus) and Performer (LockstepPerformer).
    """

    def __init__(self, harness, episodes, ready_timeout_s=3.0, cue_timeout_s=3.0):
        import replay_pane
        from revoice import plan_scenes

        self.harness = harness
        self.bus = harness.bus
        self.episodes = episodes
        self.airings = {}
        self.panes = {}           # worker_id -> SimWorker
        self.threads = {}         # worker_id -> live Thread
        self.results = collections.defaultdict(list)
        self.errors = []
        self.performers = []
        self.progress = collections.defaultdict(lambda: -1)
        self._cond = threading.Condition()
        self._airing_ids = itertools.count(1)
        self.lockstep_timeout_s = 3.0
        mp = harness.monkeypatch

        mp.setattr(replay_pane.episode_store, "available", lambda: True)
        mp.setattr(replay_pane.episode_store, "load_episode", lambda name: episodes.get(name))
        mp.setattr(replay_pane.episode_store, "list_episodes", lambda: sorted(episodes))
        mp.setattr(replay_pane.narration_store, "available", lambda: True)
        mp.setattr(replay_pane.narration_store, "load_airing", lambda airing_id: self.airings.get(airing_id))

        def fake_prepare_voiced_show(script, config, workdir, **kwargs):
            scenes = plan_scenes(script.get("events", []))
            for i, scene in enumerate(scenes):
                scene["narration"] = f"line {i} by {scene['speaker']}"
                scene["audio"] = FakeAudio(0.01)
            return scenes

        def fake_persist(message_id, show, config, episode, worker_name):
            airing_id = f"airing-{next(self._airing_ids)}"
            self.airings[airing_id] = [
                {"scene_index": i, "scene_kind": s["kind"], "speaker": s["speaker"],
                 "text": s.get("narration"), "audio": None, "audio_duration_s": 0.01,
                 "message_id": airing_id}
                for i, s in enumerate(show)
            ]
            return airing_id

        mp.setattr(replay_pane, "prepare_voiced_show", fake_prepare_voiced_show)
        mp.setattr(replay_pane, "publish_narration", lambda *a, **kw: "narration-msg")
        mp.setattr(replay_pane, "persist_narration", fake_persist)
        mp.setattr(replay_pane, "build_voice_gate", lambda *a, **kw: (None, 0.0))
        mp.setattr(replay_pane, "_build_bus_producer",
                   lambda config: self.bus.producer_for(config["message_bus"]["worker_id"]))
        LockstepPerformer.stage = self
        mp.setattr(replay_pane, "Performer", LockstepPerformer)

        mp.setenv(replay_pane.REPLAY_READY_TIMEOUT_ENV, str(ready_timeout_s))
        mp.setattr(replay_pane, "REPLAY_READY_POLL_INTERVAL_S", 0.005)
        mp.setattr(replay_pane, "REPLAY_CUE_POLL_INTERVAL_S", 0.005)
        mp.setattr(replay_pane, "REPLAY_FIRST_CUE_TIMEOUT_S", cue_timeout_s)
        mp.setattr(replay_pane, "REPLAY_WATCHDOG_MIN_S", cue_timeout_s)
        mp.setattr(replay_pane, "REPLAY_WATCHDOG_GRACE_S", 0.0)
        self._replay_pane = replay_pane

    def enable_pane(self, worker):
        self.panes[worker.worker_id] = worker

    # ── lockstep bookkeeping ─────────────────────────────────────────────
    def record_progress(self, worker, index):
        with self._cond:
            self.progress[worker.worker_id] = max(self.progress[worker.worker_id], index)
            self._cond.notify_all()

    def _others_caught_up(self, me, index):
        for worker_id, thread in self.threads.items():
            if worker_id == me.worker_id or not thread.is_alive():
                continue
            if self.progress[worker_id] < index:
                return False
        return True

    def wait_for_others(self, me, index):
        with self._cond:
            if not self._cond.wait_for(lambda: self._others_caught_up(me, index),
                                       timeout=self.lockstep_timeout_s):
                self.errors.append(f"lockstep timeout: {me.worker_id} scene {index}")

    # ── panes ────────────────────────────────────────────────────────────
    def _pane_main(self, worker, request):
        with acting_as(worker):
            try:
                ok = self._replay_pane.perform_request(
                    request, worker.worker_id, None,
                    config={"message_bus": {"worker_id": worker.worker_id}, "voice": {}})
                self.results[worker.worker_id].append(ok)
            except Exception as exc:  # surfaced by the test via stage.errors
                self.errors.append(f"{worker.worker_id}: {exc!r}")
            finally:
                with self._cond:
                    self._cond.notify_all()

    def _poll_panes(self):
        started = False
        for worker_id, worker in self.panes.items():
            thread = self.threads.get(worker_id)
            if thread is not None and thread.is_alive():
                continue
            with acting_as(worker):
                found, request, error = relay_io.consume_json(relay_io.resolve_replay_request_file())
            if not found or error is not None or not isinstance(request, dict):
                continue
            thread = threading.Thread(target=self._pane_main, args=(worker, request),
                                      name=f"pane-{worker_id}", daemon=True)
            with self._cond:
                self.threads[worker_id] = thread
            thread.start()
            started = True
        return started

    def run(self, timeout_s=8.0, max_steps=500):
        """Pump the bus and the panes until nothing is queued, no pane is
        running and no pane has a request waiting."""
        deadline = time.monotonic() + timeout_s
        steps = 0
        while True:
            while self.bus.step():
                steps += 1
                if steps > max_steps:
                    raise BusDidNotSettle(f"duet bus still busy after {max_steps} deliveries")
            started = self._poll_panes()
            busy = any(t.is_alive() for t in self.threads.values())
            if not started and not busy and not self.bus.pending():
                return steps
            if time.monotonic() > deadline:
                raise BusDidNotSettle(f"duet did not settle in {timeout_s}s: {self.bus.triples()}")
            time.sleep(0.002)

    def performer_for(self, worker_id):
        return [p for p in self.performers if p.worker.worker_id == worker_id]
