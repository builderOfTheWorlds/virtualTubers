# End-to-end flow tests (fakes only)

## Overview

Unit tests call one handler at a time. These tests run a **whole multi-agent
flow**: several simulated workers on one in-memory bus, each dispatching
messages through the real `MESSAGE_HANDLERS` (`app/agent_handlers/`) and, for
duets, the real `replay_pane.perform_request` director/follower code. Only
the edges are faked: no tmux, Kafka, Redis, Postgres, LLM, coding tool,
pytest subprocess or network.

| File | Covers |
|---|---|
| `tests/e2e_harness.py` | The harness (not collected: no `test_` prefix) |
| `tests/test_e2e_dev_loop.py` | Dev-team loop (docs/agent_flow_reference.md §5.1): happy path, bug then fix, retry cap, backend failure, LLM failures, two concurrent tasks |
| `tests/test_e2e_duet.py` | Duet replay (§5.3, docs/duet_replay.md): relay-file protocol at bus level, plus the full director + follower pane path in threads |

All are marked `@pytest.mark.integration` (not `slow`), so CI's
`-m "not slow"` runs them. The whole set takes under 1 s.

## Running

```bash
# just the e2e tests
.venv/bin/python -m pytest -q -p no:warnings tests/test_e2e_dev_loop.py tests/test_e2e_duet.py

# everything marked integration
.venv/bin/python -m pytest -q -m integration tests
```

Two tests are `xfail(strict=True)`. Each one records a bug these scenarios
found in production code (see "Known bugs" below). When a bug is fixed, its
test starts passing, strict xfail turns that into a failure, and whoever
fixed it removes the marker.

## How the harness works

### `InMemoryBus`

- `publish(msg)` queues a message and appends it to `bus.log`.
  `inject(from, to, type, payload)` builds a chain-starting message the way
  message-api does.
- Routing works exactly like `MessageConsumer.poll_new`. A worker receives a
  message when `msg["to"]` is its `worker_id` or `"broadcast"`, and that
  includes its own broadcasts. The filter matches worker ids only, so role
  names route only when they equal the id (`manager`, `tester`), just as in
  production. Messages to `"operator"` go to `bus.operator_inbox`.
  Messages that no worker consumes go to `bus.undelivered`.
- Delivery calls `handler(worker_id, agent_config, llm, producer, msg,
  state_path, coding_backend=...)`, the same call `app/agent.py main()` makes.
- `run_until_quiet(max_steps=200)` delivers messages in FIFO order until the
  queue is empty. If it goes past `max_steps` it raises `BusDidNotSettle`,
  which catches message loops.
- Queries: `triples()` returns `(from, to, type)` in send order.
  `of_type(t)`, `chain(correlation_id)`, and `lineage(msg)` give the list of
  types from the root down to `msg` through `causation_id`.
  `assert_chain_consistent(root)` checks that every message on root's chain
  links to an earlier message on the same chain.

### Fakes

| Fake | Replaces | Script it with |
|---|---|---|
| `ScriptedLLM(name, replies=[...], fail_on=[...], down=False)` | `llm_client` | `replies`: raw strings or exception instances, used in order. `fail_on`: raise `LLMDown` when the prompt contains a substring. `down`: every call fails. Prompts are recorded in `.prompts` |
| `FakeCodingBackend(owner, results=[...])` | `coding_backend` | `ok_result(commit)` / `failed_result(error)`. When the script runs out, it returns successes with commits `<owner>-c1`, `-c2`, ... |
| `FakeTestRunner(outcomes, default)` | `tester.workspace_testable` / `tester.run_pytest` | A list shared by all workspaces, or `{"/data/repos/<coder>": [...]}`. Items are `True`, `False` or a `TestRunResult` |
| tmux recorder | `agent_handlers.coder.select_pane/send_keys/send_raw/send_command` | Calls are recorded in `harness.tmux_calls` |

### Relay files, one set per "container"

Each `SimWorker` has its own `REPLAY_{REQUEST,STOP,CUE,READY}_FILE` env
(`worker.env`) under `tmp_path/<worker_id>/`. The harness monkeypatches
`relay_io.resolve_path` so that the current simulated worker's env comes
first. That worker is tracked in a thread-local (`acting_as(worker)`), which
the bus sets while a handler runs and each duet pane thread sets for itself.
Two panes running at the same time therefore never share a file, the same as
two containers. The real env vars point at a scratch dir, so nothing ever
touches `/tmp/replay_*.json`. Read a worker's files with
`worker.read_relay("cue")` or `worker.relay_path("ready")`.

### `DuetStage`: real director + follower panes

`DuetStage(harness, episodes, ready_timeout_s=3.0)` fakes these pieces:

- the episode library
- `narration_store` (airings stored in memory)
- voice prep: `revoice.plan_scenes` scenes with fake audio
- the voice gate
- the Kafka producer, replaced by the bus
- `Performer`, replaced by `LockstepPerformer`

It also shrinks the poll intervals and watchdogs. `stage.enable_pane(worker)`
starts a pane for that worker. `stage.run()` pumps the bus and polls each
enabled pane's request file with `relay_io.consume_json`. When a request
turns up, it runs `replay_pane.perform_request` in a thread. The run ends
when no message is queued, no pane is running and no request is waiting.

In `LockstepPerformer`, the director waits after each `on_scene_start(i)`
until every other live pane has performed scene `i`. This stands in for the
real scene duration, so the follower is never out-raced by `replay_end`.
Results are in `stage.results`, `stage.performer_for(id)` and
`stage.errors`. A test must assert that `stage.errors == []`, because pane
exceptions are captured there.

## Adding a scenario

```python
import pytest
from e2e_harness import E2EHarness, FakeTestRunner, ScriptedLLM, OPERATOR

pytestmark = pytest.mark.integration

def test_my_flow(tmp_path, monkeypatch):
    h = E2EHarness(tmp_path, monkeypatch,
                   test_runner=FakeTestRunner(outcomes=[False, True]))
    h.dev_team(manager_llm=ScriptedLLM("manager", fail_on=["reported a"]))
    root = h.assign("coder", "add modulo")

    h.bus.run_until_quiet()

    assert h.bus.triples()[-1] == ("manager", OPERATOR, "manager_report")
    h.bus.assert_chain_consistent(root)
```

```python
from e2e_harness import DuetStage, E2EHarness, OPERATOR

def test_my_duet(tmp_path, monkeypatch):
    h = E2EHarness(tmp_path, monkeypatch)
    d, f = h.add_worker("tuber_1", "coder"), h.add_worker("tuber_2", "coder")
    stage = DuetStage(h, {"ep": {"source": "ep", "events": [
        {"type": "user_message", "text": "hi"}, {"type": "assistant_text", "text": "yo"}]}})
    stage.enable_pane(d); stage.enable_pane(f)
    h.bus.inject(OPERATOR, "tuber_1", "replay_request",
                 {"episode": "ep", "cast": {"boss": "tuber_2", "coder": "tuber_1"}})
    stage.run()
    assert stage.errors == [] and stage.results == {"tuber_1": [True], "tuber_2": [True]}
```

Rules of thumb:

- Assert the exact `triples()` sequence plus the payload fields that matter.
  For dev-team chains, also call `assert_chain_consistent(root)`.
- Patch a name in the module that **looks it up** (docs/agent_handlers.md,
  "Where to patch"), never in `agent`.
- If a scenario exposes a production bug, don't fix it in the test PR. Mark
  the test `xfail(strict=True, reason="BUG <file>:<line> ...")`.
- Keep every scenario well under a second. Where the code polls, patch the
  timeouts; the harness already does this for duets.

## Known bugs found by these tests

| Test | Bug |
|---|---|
| `test_manager_llm_down_on_test_passed_still_reports_milestone` | `app/agent_handlers/manager.py:125` `handle_test_passed` returns without a `manager_report` when the manager's LLM fails. A passing task is then never reported. `bug_report` and `clarification_request` fall back to a `(narration unavailable: ...)` report instead. |
| `test_narration_only_coder_llm_failure_reaches_manager` | `app/agent_handlers/coder.py:149`: when the coder's narration LLM fails (with no coding backend), it sends `clarification_request` to the task's **sender**. For an operator-assigned task that is `operator`, so the manager never raises a blocker. Compare the backend-failure path (`coder.py:116`, always `manager`) and docs/agent_flow_reference.md §3. |

The dev-loop tests also pin down behaviour that disagrees with
docs/agent_flow_reference.md §3. The coder sends `task_complete` to the
**task's sender**, not always to the manager. For an operator-assigned task
it goes to `operator`, so the manager only acknowledges `task_complete` for
fix re-assignments that it sent itself.

## Changelog

- v1.0.0 (2026-09-27): Initial harness, 6 dev-loop scenarios (plus LLM-failure
  variants) and 5 duet scenarios; 2 strict xfails that document bugs.
