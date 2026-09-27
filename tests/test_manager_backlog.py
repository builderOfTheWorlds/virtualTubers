"""
test_manager_backlog.py
The manager's opt-in task backlog (docs/task_backlog.md): BacklogDispatcher
idle dispatch (when idle, not while a chain is in flight, cooldown, stale
drop, round-robin), chain end -> mark_done with the right outcome from the
real manager handlers (milestone / escalation / blocker; bug retries and
task_complete keep the chain open), correlation ids, and the agent loop's
idle hook (not called while the manager is disabled). Fakes throughout.
"""
from unittest.mock import MagicMock

import pytest

import agent
from agent_handlers import IDLE_TICK_HOOKS, manager
from agent_handlers.manager import (
    MAX_BUG_RETRIES,
    BacklogDispatcher,
    handle_bug_report,
    handle_clarification_request,
    handle_task_complete,
    handle_test_passed,
    manager_idle_tick,
)
from message_bus import build_message, correlation_of, reply_ids


class FakeProducer:
    def __init__(self, *args, **kwargs):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message

    def by_type(self, type_):
        return [m for m in self.sent if m["type"] == type_]


class FakeLLM:
    def __init__(self, error=None):
        self.error = error

    def complete(self, system_prompt, messages):
        if self.error:
            raise self.error
        return '{"line": "Ticket time, team!", "emotion": "happy"}'


class FakeSource:
    name = "fake"

    def __init__(self, tasks=None):
        self.tasks = list(tasks or [])
        self.started = []
        self.done = []
        self.polls = 0

    def next_task(self):
        self.polls += 1
        done_ids = {d[0] for d in self.done}
        for t in self.tasks:
            if t["id"] not in done_ids:
                return {**t, "source": "fake"}
        return None

    def mark_started(self, task_id, correlation_id=None):
        self.started.append((task_id, correlation_id))

    def mark_done(self, task_id, outcome, correlation_id=None):
        self.done.append((task_id, outcome, correlation_id))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


MANAGER = {"role": "manager", "system_prompt": "You are MAX-1."}
TASKS = [{"id": "a", "title": "Task A", "body": "details A"},
         {"id": "b", "title": "Task B", "body": ""}]


@pytest.fixture(autouse=True)
def _reset_module_backlog():
    manager._reset_backlog()
    yield
    manager._reset_backlog()


@pytest.fixture
def setup():
    """Install a dispatcher as the process's backlog (as _get_backlog would)."""
    def _make(tasks=TASKS, coders=("coder",), cooldown_s=10, stale_after_s=100):
        clock = Clock()
        source = FakeSource(tasks)
        dispatcher = BacklogDispatcher(source, coders=list(coders), cooldown_s=cooldown_s,
                                       stale_after_s=stale_after_s, clock=clock)
        manager._backlog, manager._backlog_built = dispatcher, True
        clock.now += cooldown_s  # past the startup cooldown
        return dispatcher, source, clock, FakeProducer()
    return _make


def _tick(dispatcher, producer, llm=None):
    return dispatcher.tick("manager", MANAGER, llm or FakeLLM(), producer)


def _reply(worker, to, type_, payload, cause):
    return build_message(worker, to, type_, payload, **reply_ids(cause))


# ── dispatch ─────────────────────────────────────────────────────────────────

def test_dispatches_when_idle_with_new_correlation_chain(setup):
    dispatcher, source, clock, producer = setup()
    msg = _tick(dispatcher, producer)
    assert producer.sent == [msg]
    assert msg["type"] == "task_assignment" and msg["to"] == "coder" and msg["from"] == "manager"
    assert msg["payload"]["task"] == "Task A. details A"
    assert msg["payload"]["backlog_id"] == "a"
    assert msg["payload"]["retry_count"] == 0
    # A fresh chain: its own id, no cause.
    assert msg["correlation_id"] == msg["id"] and msg["causation_id"] is None
    assert source.started == [("a", msg["correlation_id"])]
    assert list(dispatcher.inflight) == [msg["correlation_id"]]


def test_each_dispatch_gets_a_distinct_correlation_id(setup):
    dispatcher, source, clock, producer = setup()
    first = _tick(dispatcher, producer)
    dispatcher.end_chain(first["correlation_id"], "milestone")
    clock.now += 10
    second = _tick(dispatcher, producer)
    assert second["payload"]["backlog_id"] == "b"
    assert second["payload"]["task"] == "Task B"
    assert second["correlation_id"] != first["correlation_id"]


def test_no_dispatch_while_chain_in_flight(setup):
    dispatcher, source, clock, producer = setup()
    _tick(dispatcher, producer)
    clock.now += 50
    assert _tick(dispatcher, producer) is None
    assert len(producer.by_type("task_assignment")) == 1
    assert source.polls == 1


def test_no_dispatch_before_startup_cooldown():
    clock = Clock()
    dispatcher = BacklogDispatcher(FakeSource(TASKS), cooldown_s=30, clock=clock)
    producer = FakeProducer()
    assert _tick(dispatcher, producer) is None
    clock.now += 30
    assert _tick(dispatcher, producer) is not None


def test_cooldown_after_chain_end(setup):
    dispatcher, source, clock, producer = setup(cooldown_s=20)
    first = _tick(dispatcher, producer)
    dispatcher.end_chain(first["correlation_id"], "milestone")
    clock.now += 19
    assert _tick(dispatcher, producer) is None
    clock.now += 1
    assert _tick(dispatcher, producer) is not None


def test_empty_backlog_polls_at_most_once_per_cooldown(setup):
    dispatcher, source, clock, producer = setup(tasks=[], cooldown_s=10)
    assert _tick(dispatcher, producer) is None
    assert _tick(dispatcher, producer) is None
    assert source.polls == 1
    clock.now += 10
    _tick(dispatcher, producer)
    assert source.polls == 2
    assert producer.sent == []


def test_round_robin_over_coders(setup):
    tasks = [{"id": str(n), "title": f"T{n}"} for n in range(3)]
    dispatcher, source, clock, producer = setup(tasks=tasks, coders=["coder-native", "coder-aider"])
    targets = []
    for _ in range(3):
        msg = _tick(dispatcher, producer)
        targets.append(msg["to"])
        dispatcher.end_chain(msg["correlation_id"], "milestone")
        clock.now += 10
    assert targets == ["coder-native", "coder-aider", "coder-native"]


def test_llm_failure_still_dispatches_with_fallback_narration(setup):
    dispatcher, source, clock, producer = setup()
    msg = _tick(dispatcher, producer, llm=FakeLLM(error=RuntimeError("down")))
    assert msg is not None and msg["payload"]["backlog_id"] == "a"


def test_state_written_on_dispatch(setup, monkeypatch):
    dispatcher, source, clock, producer = setup()
    states = []
    monkeypatch.setattr(manager, "write_state", lambda path, state, **kw: states.append((state, kw)))
    dispatcher.tick("manager", MANAGER, FakeLLM(), producer, state_path="/tmp/x.json")
    assert [s for s, _ in states] == ["thinking", "speaking"]
    assert states[-1][1]["bubble"] == "Ticket time, team!"


# ── stale guard ──────────────────────────────────────────────────────────────

def test_stale_chain_dropped_and_marked_escalation(setup, capsys):
    dispatcher, source, clock, producer = setup(stale_after_s=100)
    msg = _tick(dispatcher, producer)
    cid = msg["correlation_id"]
    clock.now += 101
    _tick(dispatcher, producer)
    assert ("a", "escalation", cid) in source.done
    assert "WARN backlog dropping stale chain" in capsys.readouterr().out
    report = producer.by_type("manager_report")[0]
    assert report["payload"]["report_type"] == "escalation"
    assert report["payload"]["reason"] == "stale"
    assert report["correlation_id"] == cid


def test_activity_keeps_chain_fresh(setup):
    dispatcher, source, clock, producer = setup(stale_after_s=100)
    msg = _tick(dispatcher, producer)
    clock.now += 90
    dispatcher.note_activity(msg["correlation_id"])
    clock.now += 90
    _tick(dispatcher, producer)
    assert source.done == []
    assert msg["correlation_id"] in dispatcher.inflight


def test_stale_foreign_chain_dropped_without_mark_done(setup):
    dispatcher, source, clock, producer = setup(stale_after_s=100)
    dispatcher.note_activity("operator-chain")
    clock.now += 101
    dispatcher.drop_stale(producer)
    assert dispatcher.inflight == {}
    assert source.done == [] and producer.sent == []


# ── chain end via the real manager handlers ──────────────────────────────────

def _dispatch(setup, **kw):
    dispatcher, source, clock, producer = setup(**kw)
    assignment = _tick(dispatcher, producer)
    return dispatcher, source, clock, producer, assignment


def test_test_passed_ends_chain_as_milestone(setup):
    dispatcher, source, clock, producer, assignment = _dispatch(setup)
    commit = _reply("coder", "tester", "commit_notification", {"task": "Task A"}, assignment)
    passed = _reply("tester", "manager", "test_passed", {"task": "Task A"}, commit)
    handle_test_passed("manager", MANAGER, FakeLLM(), producer, passed)
    assert source.done == [("a", "milestone", assignment["correlation_id"])]
    assert dispatcher.inflight == {}
    report = producer.by_type("manager_report")[-1]
    assert report["correlation_id"] == assignment["correlation_id"]


def test_test_passed_ends_chain_even_if_llm_fails(setup):
    dispatcher, source, clock, producer, assignment = _dispatch(setup)
    passed = _reply("tester", "manager", "test_passed", {"task": "Task A"}, assignment)
    handle_test_passed("manager", MANAGER, FakeLLM(error=RuntimeError("x")), producer, passed)
    assert source.done == [("a", "milestone", assignment["correlation_id"])]


def test_bug_retry_keeps_chain_then_escalation_ends_it(setup):
    dispatcher, source, clock, producer, assignment = _dispatch(setup)
    bug = _reply("tester", "manager", "bug_report",
                 {"task": "Task A", "severity": "high", "retry_count": 0, "coder_id": "coder"},
                 assignment)
    handle_bug_report("manager", MANAGER, FakeLLM(), producer, bug)
    # Re-delegated on the same chain; still in flight, nothing marked done.
    redo = producer.by_type("task_assignment")[-1]
    assert redo["correlation_id"] == assignment["correlation_id"]
    assert source.done == [] and assignment["correlation_id"] in dispatcher.inflight
    clock.now += 50  # past the cooldown, within stale_after_s
    assert _tick(dispatcher, producer) is None  # no new dispatch while in flight
    assert len(producer.by_type("task_assignment")) == 2

    capped = _reply("tester", "manager", "bug_report",
                    {"task": "Task A", "severity": "high", "retry_count": MAX_BUG_RETRIES},
                    redo)
    handle_bug_report("manager", MANAGER, FakeLLM(), producer, capped)
    assert source.done == [("a", "escalation", assignment["correlation_id"])]
    assert dispatcher.inflight == {}


def test_bug_report_escalates_on_llm_failure_and_ends_chain(setup):
    dispatcher, source, clock, producer, assignment = _dispatch(setup)
    bug = _reply("tester", "manager", "bug_report",
                 {"task": "Task A", "severity": "low", "retry_count": 0}, assignment)
    handle_bug_report("manager", MANAGER, FakeLLM(error=RuntimeError("x")), producer, bug)
    assert source.done == [("a", "escalation", assignment["correlation_id"])]


def test_clarification_request_ends_chain_as_blocker_without_reassign(setup):
    dispatcher, source, clock, producer, assignment = _dispatch(setup)
    ask = _reply("coder", "manager", "clarification_request",
                 {"task": "Task A", "error": "backend failed"}, assignment)
    handle_clarification_request("manager", MANAGER, FakeLLM(), producer, ask)
    assert source.done == [("a", "blocker", assignment["correlation_id"])]
    # Invariant: no auto-reassign — only the original dispatch was ever sent.
    assert len(producer.by_type("task_assignment")) == 1
    # And it's not retried: the next dispatch is the NEXT task.
    clock.now += 10
    assert _tick(dispatcher, producer)["payload"]["backlog_id"] == "b"


def test_task_complete_sends_nothing_and_keeps_chain(setup):
    dispatcher, source, clock, producer, assignment = _dispatch(setup)
    before = list(producer.sent)
    done = _reply("coder", "manager", "task_complete", {"task": "Task A"}, assignment)
    clock.now += 50
    handle_task_complete("manager", MANAGER, FakeLLM(), producer, done)
    assert producer.sent == before
    assert dispatcher.inflight[assignment["correlation_id"]]["last_activity"] == clock.now
    assert source.done == []


def test_foreign_chain_blocks_dispatch_until_it_ends(setup):
    dispatcher, source, clock, producer = setup()
    operator_task = build_message("operator", "coder", "task_assignment", {"task": "op"})
    bug = _reply("tester", "manager", "bug_report",
                 {"task": "op", "severity": "low", "retry_count": 0}, operator_task)
    handle_bug_report("manager", MANAGER, FakeLLM(), producer, bug)
    assert _tick(dispatcher, producer) is None
    passed = _reply("tester", "manager", "test_passed", {"task": "op"}, bug)
    handle_test_passed("manager", MANAGER, FakeLLM(), producer, passed)
    assert source.done == []  # not a backlog task
    clock.now += 10
    assert _tick(dispatcher, producer)["payload"]["backlog_id"] == "a"


def test_handlers_unaffected_when_backlog_disabled():
    producer = FakeProducer()
    ask = build_message("coder", "manager", "clarification_request", {"task": "x", "error": "e"})
    handle_clarification_request("manager", MANAGER, FakeLLM(), producer, ask)
    assert [m["type"] for m in producer.sent] == ["manager_report"]


# ── manager_idle_tick / config ───────────────────────────────────────────────

def _file_backlog_config(tmp_path, **overrides):
    task_file = tmp_path / "backlog.yaml"
    task_file.write_text("tasks:\n  - {id: one, title: Only task}\n")
    cfg = {"enabled": True, "source": "file", "file_path": str(task_file),
           "state_path": str(tmp_path / "state.json"), "coders": ["coder-native"],
           "cooldown_s": 0, "stale_after_s": 100}
    cfg.update(overrides)
    return {**MANAGER, "backlog": cfg}


def test_idle_hook_registered_for_manager_role():
    assert IDLE_TICK_HOOKS["manager"] is manager_idle_tick


def test_manager_idle_tick_disabled_by_default():
    producer = FakeProducer()
    assert manager_idle_tick("manager", MANAGER, FakeLLM(), producer) is None
    assert producer.sent == [] and manager._backlog is None


def test_manager_idle_tick_ignores_non_manager_role(tmp_path):
    producer = FakeProducer()
    cfg = {**_file_backlog_config(tmp_path), "role": "coder"}
    assert manager_idle_tick("coder", cfg, FakeLLM(), producer) is None
    assert producer.sent == []


def test_manager_idle_tick_dispatches_from_file_config(tmp_path):
    producer = FakeProducer()
    msg = manager_idle_tick("manager", _file_backlog_config(tmp_path), FakeLLM(), producer)
    assert msg["to"] == "coder-native" and msg["payload"]["backlog_id"] == "one"


def test_manager_idle_tick_never_raises(monkeypatch):
    def boom(cfg):
        raise RuntimeError("bad backlog")
    monkeypatch.setattr(manager, "build_backlog", boom)
    assert manager_idle_tick("manager", {**MANAGER, "backlog": {"enabled": True}},
                             FakeLLM(), FakeProducer()) is None


# ── agent loop wiring ────────────────────────────────────────────────────────

class _StopLoop(Exception):
    pass


class FakeConsumer:
    def __init__(self, *args, **kwargs):
        pass

    def poll_new(self):
        return []


class FakeControl:
    def __init__(self, enabled):
        self.enabled = enabled

    def heartbeat(self, worker_id, ttl_s):
        return True

    def is_enabled(self, worker_id):
        return self.enabled


@pytest.fixture
def run_manager(monkeypatch):
    def _run(agent_config, ticks, enabled=True):
        config = {"agent": agent_config, "message_bus": {"worker_id": "manager", "topic": "t"}}
        producer = FakeProducer()
        monkeypatch.setattr("sys.argv", ["agent.py", "--config", "unused.yaml"])
        monkeypatch.delenv("WORKER_ID", raising=False)
        monkeypatch.setattr(agent, "load_worker_config", lambda path: config)
        monkeypatch.setattr(agent, "build_llm_client", lambda cfg: FakeLLM())
        monkeypatch.setattr(agent, "build_coding_backend", lambda cfg, llm: None)
        monkeypatch.setattr(agent, "resolve_state_path", lambda cfg: None)
        monkeypatch.setattr(agent, "write_state", lambda *a, **k: None)
        monkeypatch.setattr(agent, "MessageProducer", lambda *a, **k: producer)
        monkeypatch.setattr(agent, "MessageConsumer", FakeConsumer)
        monkeypatch.setattr(agent.WorkerControl, "from_config",
                            classmethod(lambda cls, cfg: FakeControl(enabled)))
        monkeypatch.setattr(agent, "resolve_runtime_dir", lambda: None)
        monkeypatch.setattr(agent, "AgentMetrics", MagicMock())
        monkeypatch.setattr(agent, "MetricsProducerWrapper", lambda p, m: p)
        llm = FakeLLM()
        llm.metrics = MagicMock()
        monkeypatch.setattr(agent, "InstrumentedLLMClient", lambda *a, **k: llm)
        sleeps = {"n": 0}

        def fake_sleep(_s):
            sleeps["n"] += 1
            if sleeps["n"] >= ticks:
                raise _StopLoop

        monkeypatch.setattr(agent.time, "sleep", fake_sleep)
        with pytest.raises(_StopLoop):
            agent.main()
        return producer

    return _run


def test_agent_loop_dispatches_backlog_task_when_enabled(run_manager, tmp_path):
    producer = run_manager({**_file_backlog_config(tmp_path), "bus_heartbeat_every": 0}, ticks=3)
    assignments = producer.by_type("task_assignment")
    assert len(assignments) == 1  # one in flight; no second dispatch
    assert assignments[0]["payload"]["backlog_id"] == "one"


def test_agent_loop_dispatches_nothing_while_manager_disabled(run_manager, tmp_path):
    producer = run_manager({**_file_backlog_config(tmp_path), "bus_heartbeat_every": 0},
                           ticks=3, enabled=False)
    assert producer.sent == []
    assert manager._backlog is None  # never even built


def test_correlation_of_dispatch_propagates_through_coder(setup, monkeypatch):
    from agent_handlers import coder
    from agent_handlers.coder import handle_task_assignment
    for name in ("demo_editor_note", "demo_filetree_ls"):
        monkeypatch.setattr(coder, name, lambda *a, **k: None)
    dispatcher, source, clock, producer, assignment = _dispatch(setup)
    coder_out = FakeProducer()
    handle_task_assignment("coder", {"role": "coder"}, FakeLLM(), coder_out, assignment)
    assert coder_out.sent
    assert {correlation_of(m) for m in coder_out.sent} == {assignment["correlation_id"]}
    task_complete = [m for m in coder_out.sent if m["type"] == "task_complete"][0]
    assert task_complete["to"] == "manager"
