# agent.py

## Overview

The worker's agent loop: `perceive()` (poll the Kafka bus for messages
addressed to this worker), `think()` (call the configured LLM with the
worker's system prompt), and `act()` (reply on the bus and update the
avatar state). Incoming messages are dispatched through the
`MESSAGE_HANDLERS` table, which covers all 8 message types from
`docs/VTuber_AI_Dev_Team_Concept.md` §3.4 (`status_update` itself is
send-only heartbeat traffic; `operator_message` is `message-api`'s default
type, standing in for direct operator chat). Every tick still publishes a
`status_update` heartbeat, unchanged from the earlier stub.

The handlers form a collaboration graph so a single ticket flows through
the whole team: the coder answers a `task_assignment` with `task_complete`
to the sender **and** hands the commit to the tester
(`commit_notification`); the tester "runs tests" (a weighted-random stub —
`_decide_test_outcome`, no real test execution yet) and reports
`test_passed` or `bug_report` to the manager; the manager celebrates a
pass with a `manager_report` to the operator (payload discriminator
`report_type: "milestone" | "blocker" | "escalation"` — one type, not
three) or re-delegates a bug fix to the coder as a fresh
`task_assignment`. The bug↔fix loop is bounded: `retry_count` travels
statelessly in the message payloads around the whole loop
(`task_assignment` → `commit_notification` → `bug_report` → re-delegated
`task_assignment` with `retry_count + 1`), and once it reaches
`MAX_BUG_RETRIES` (3) the manager escalates to the operator instead of
resending. Handlers are role-gated on `agent_config["role"]`: a message
type arriving at the wrong role logs and no-ops rather than crashing
(routing is by hardcoded worker-id string, so the gate is load-bearing).
Two deliberate non-actions: `handle_task_complete` acknowledges but sends
nothing on the bus (the coder's own `commit_notification` already drives
the tester — sending anything here would duplicate the test run), and
`handle_clarification_request` always escalates a `"blocker"` report to
the operator and never auto-resends the task (avoids a retry storm against
a broken LLM endpoint). `retest_request` behaves identically to
`commit_notification` but currently has no automatic sender — it's an
operator lever via `message-api`. Any role (no gate) answers a direct
`operator_message` with an `operator_reply` addressed to `"operator"`.

On each task-handling lifecycle event (`thinking`/`focused` before the LLM
call, `speaking`/`happy`/`frustrated` with the narration/error after it),
`act()` also
writes to the small local state file `agent_state.py` owns — this is what
lets the avatar pane (`avatar.py`) show the right expression and speech
bubble instead of auto-cycling on a blind timer. See `docs/agent_state.md`.

On `task_assignment`, `act()` also calls two scripted (non-LLM)
`tmux_control.py`-driven demo actions — a first, deliberately simple
exercise of "the agent acting on its own tmux UI" ahead of any real
LLM-driven tool use (see `docs/tmux_control.md`):
- `demo_editor_note` focuses the editor pane and types a fixed
  `# TODO: <task>` comment.
- `demo_filetree_ls` focuses the filetree pane (an interactive shell as of
  `config/panels/filetree.yaml`'s `bash -c "tree ...; exec bash"` — no
  longer a `watch` loop, which can't accept keystrokes as commands) and runs
  `ls`, then refocuses the editor pane.

(`handle_operator_message` is deliberately lightweight and skips both
demos — LLM reply only.)

This is the "think + narrate" slice of the agent brain — it proves the
full team round trip (operator → coder → tester → manager → back to the
operator on stream) end to end, now visibly landing on the avatar and
editor panes as well as the Kafka feed pane. The LLM itself still can't choose what to type or
which pane to touch — `llm_client.complete()` returns free-form narration
text only, no structured tool calls — see `docs/VTuber_AI_Dev_Team_Concept.md`
Phase 1 roadmap for what's next.

## Signature

Module-level constants — the test-outcome stub's tuning knobs, kept at
module level specifically so tuning (or replacing the stub with real test
execution) is a one-edit change:

```python
TEST_PASS_PROBABILITY = 0.7
BUG_SEVERITIES = ["low", "medium", "high", "critical"]
BUG_SEVERITY_WEIGHTS = [10, 40, 35, 15]
MAX_BUG_RETRIES = 3
```

Functions (every `handle_*` shares the same signature and is looked up in
the `MESSAGE_HANDLERS` dict — message type → handler — by `main()`'s loop):

```python
def resolve(env_name: str, config_value, default=None)

def _decide_test_outcome() -> tuple[bool, str | None]

def demo_editor_note(worker_id: str, task: str) -> None

def demo_filetree_ls(worker_id: str) -> None

def handle_task_assignment(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

def _run_tests_and_report(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

def handle_commit_notification(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

def handle_retest_request(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

def _send_manager_report(worker_id: str, producer: MessageProducer, report_type: str, task: str, narration: str, extra: dict | None = None)

def handle_bug_report(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

def handle_test_passed(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

def handle_task_complete(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

def handle_clarification_request(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

def handle_operator_message(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

def main() -> None
```

## Parameters

- `env_name` / `config_value` / `default` — `resolve` picks an environment
  variable over a config value over a default, used for `WORKER_ID`,
  `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`.
- `worker_id` (str) — this worker's ID, used as `from` on outgoing messages.
- `agent_config` (dict) — `config["agent"]`; `system_prompt` feeds the LLM
  call and `role` gates the collaboration handlers:
  `commit_notification`/`retest_request` require `role: tester`,
  `bug_report`/`test_passed`/`task_complete`/`clarification_request`
  require `role: manager`, and `handle_task_assignment` only sends the
  follow-on `commit_notification` when `role: coder`. On a mismatch the
  handler logs and no-ops. `handle_operator_message` has no gate — any
  role answers.
- `llm_client` — an `OllamaClient`/`ClaudeClient` from `llm_client.build_llm_client`.
- `producer` (`MessageProducer`) — used to publish the reply.
- `msg` (dict) — the received message envelope; `msg["from"]` is who to
  reply to. Payload fields read vary per handler: `task` (all task-shaped
  handlers), `retry_count` (defaults to 0; threaded through the coder →
  tester → manager loop unmodified and incremented only when the manager
  re-delegates a fix), `severity`/`repro` (`bug_report`), `error`
  (`clarification_request`), `message` (`operator_message`).
- `report_type` / `task` / `narration` / `extra` (`_send_manager_report`) —
  the payload discriminator (`"milestone" | "blocker" | "escalation"`),
  the task description, the (possibly fallback) narration, and an optional
  dict merged into the payload (e.g. `severity`, `retry_count`,
  `blocked_worker`). Always addressed manager → `"operator"` as type
  `manager_report` — deliberately not `status_update`, which the feed
  hides by default.
- `state_path` (str | None) — where to write avatar state
  (`agent_state.write_state`); `None` skips the write (used by tests that
  don't care about the avatar side effect).
- `task` (str, `demo_editor_note`) — the task description; flattened to one
  line and typed as a comment.
- `--config` (CLI flag, default `/config/worker.yaml`) — path to the worker's
  YAML config.

## Return Value

- All `handle_*` functions, `_run_tests_and_report`, `demo_editor_note`,
  `demo_filetree_ls` — `None`; side effects only (Kafka publish + console
  `print`; avatar state writes; tmux pane focus/keystrokes).
- `_decide_test_outcome` — `(passed, severity)`: `(True, None)` on pass,
  otherwise `(False, severity)` with severity drawn from `BUG_SEVERITIES`
  weighted by `BUG_SEVERITY_WEIGHTS`. Factored out (not inlined in the
  tester handlers) so tests can monkeypatch it.
- `_send_manager_report` — the published message envelope
  (`producer.send`'s return value).
- `main` — never returns; runs the tick loop until the process is killed.

## Dependencies

- `message_bus` (`load_worker_config`, `build_message`, `MessageProducer`, `MessageConsumer`)
- `llm_client` (`build_llm_client`)
- `agent_state` (`resolve_state_path`, `write_state`)
- `tmux_control` (`select_pane`, `send_keys`, `send_raw`, `send_command`, `TmuxError`)
- Python standard library: `os`, `random` (the `_decide_test_outcome`
  stub — deliberately unseeded live; tests monkeypatch, never assert
  statistically), `time`, `argparse`

## Usage Examples

```bash
python3 app/agent.py --config config/workers/coder.yaml
```

```bash
# Inject a task for the coder worker to narrate (see docs/message_api.md)
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "fix the login bug"}}'
```

The coder's console output and Kafka feed pane show the LLM's in-character
narration, followed by a `task_complete` message back to whoever sent the
task (`from` field on the original message, `"operator"` when sent via
`message-api`) — and, because the coder's `role` is `coder`, a
`commit_notification` to the tester that kicks off the rest of the team
flow (tester → `test_passed`/`bug_report` → manager → `manager_report` to
the operator).

```bash
# Simulate the tester finding a bug — the manager triages it and either
# re-delegates a fix to the coder (retry_count + 1) or, once retry_count
# reaches MAX_BUG_RETRIES, escalates a manager_report to the operator
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "manager", "type": "bug_report", "payload": {"task": "fix the login bug", "severity": "high", "repro": "log in with an expired token", "retry_count": 0}}'
```

```bash
# Direct operator chat — any worker (no role gate) answers with an
# operator_reply addressed to "operator", no tmux demo side effects
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "tester", "type": "operator_message", "payload": {"message": "status?"}}'
```

## Error Handling

- Every handler wraps its LLM call the same way: the exception is caught
  and logged instead of crashing the tick loop — one bad LLM call doesn't
  take the worker off stream. What happens next varies by handler:
  - `handle_task_assignment` publishes a `clarification_request` (with the
    error text) back to the sender.
  - `_run_tests_and_report` (tester) publishes a `clarification_request`
    to `"manager"` — the same contract shape the coder uses, so one
    manager-side handler covers both origins.
  - `handle_bug_report` and `handle_clarification_request` **escalate
    anyway**, sending the `manager_report` (`"escalation"`/`"blocker"`)
    with a fallback narration string — a blocker must not vanish because
    two LLM calls failed back to back.
  - `handle_test_passed` and `handle_task_complete` just log and set the
    avatar to `frustrated`; nothing downstream depends on their sends.
  - `handle_operator_message` still answers the operator, with an
    `operator_reply` carrying `{"error": ...}` instead of a narration.
- Role-mismatch no-ops: a role-gated handler receiving a message type
  meant for another role (e.g. a `bug_report` addressed to the tester)
  logs `ignoring <type> (role=..., expected ...)` and returns — no LLM
  call, no send, never a crash. Routing is by hardcoded worker-id string,
  so this gate is load-bearing for `WORKER_ID`/`role` mismatches.
- Malformed/missing config (`load_worker_config`), an unreachable Kafka
  broker (`MessageProducer`/`MessageConsumer` construction), or an unknown
  `llm.provider` (`build_llm_client`) are all fatal at startup and left
  uncaught, matching `message_bus.py`'s fail-fast convention — Docker's
  `restart: unless-stopped` handles the retry.
- `demo_editor_note` and `demo_filetree_ls` each catch `TmuxError`/`OSError`
  around every tmux call (pane not found, no tmux session yet, or the
  `tmux` binary missing entirely — e.g. running `agent.py` outside the
  container) and just log it — these are cosmetic demo actions, so neither
  may ever take the tick loop down.

## Changelog

- v2.0.0 (2026-07-03) — Coders write REAL code and testers run REAL tests.
  All handlers gained a trailing `coding_backend=None` kwarg (uniform
  dispatch; existing positional call sites unaffected).
  `handle_task_assignment` on a backend-equipped coder runs
  `coding_backend.run_task()` BEFORE narrating (so narration describes what
  actually happened), publishes a broadcast `coding_run_report` for every
  run (unpacked into Postgres `coding_backend_runs` by message-logger),
  replays the commit via `git show --stat` in the filetree pane
  (`show_commit_in_filetree`), sends failures to the manager as
  `clarification_request` blockers, and threads
  `coder_id`/`commit`/`backend` through `commit_notification`.
  `_run_tests_and_report` resolves the coder's read-only workspace mount
  (`_resolve_workspace`: config `agent.workspaces` map, else
  `/data/repos/<coder_id>`) and really runs pytest via `test_runner.py` —
  severity now comes from failure count (`_severity_from_failures`), repro
  carries failing test IDs, payloads carry `coder_id` + `real_run`; the
  weighted-random stub survives only for untestable workspaces (legacy
  narration-only coder). `handle_bug_report` re-delegates to the
  ORIGINATING coder via payload `coder_id` (falls back to `"coder"`).
  A narration-LLM failure after a successful coding run or real test
  verdict no longer discards the work — it proceeds with a
  `(narration unavailable)` fallback. Startup builds the backend via
  `build_coding_backend(config, llm_client)` inside a catch-all: a broken
  backend degrades the worker to narration-only rather than crashing it
  (deliberate exception to the fail-fast convention: the stream staying
  live matters more than the backend).

- v1.5.0 (2026-07-02) — Replaced the single `task_assignment` branch with
  the `MESSAGE_HANDLERS` dispatch table covering all 8 message types from
  the concept doc §3.4. Workers now collaborate by role: the coder also
  sends `commit_notification` to the tester; the tester
  (`handle_commit_notification`/`handle_retest_request` →
  `_run_tests_and_report`, outcome stubbed by `_decide_test_outcome` with
  `TEST_PASS_PROBABILITY`/`BUG_SEVERITIES`/`BUG_SEVERITY_WEIGHTS`) reports
  `test_passed`/`bug_report` to the manager; the manager re-delegates
  fixes with `retry_count + 1` (threaded through the whole loop; at
  `MAX_BUG_RETRIES = 3` it escalates instead) and reports to the operator
  via the new `manager_report` type
  (`_send_manager_report`, `report_type: milestone|blocker|escalation`).
  Any role answers `operator_message` with the new `operator_reply` type.
- v1.4.0 (2026-07-01) — Added `demo_filetree_ls`, called alongside
  `demo_editor_note` on every `task_assignment`: focuses the filetree pane
  and runs `ls`, then refocuses the editor. Required changing
  `config/panels/filetree.yaml` from a `watch -n2 tree` loop (which can't
  accept keystrokes as commands) to an interactive shell.
- v1.3.0 (2026-07-01) — Added `demo_editor_note`, called from
  `handle_task_assignment` on every `task_assignment`: a scripted
  `tmux_control.py`-driven action that focuses the editor pane and types a
  fixed TODO comment noting the task.
- v1.2.0 (2026-07-01) — Wired `handle_task_assignment` and `main` to write
  avatar state (`agent_state.write_state`) on `thinking`/`speaking`/
  `frustrated` transitions, so `avatar.py` reflects live agent activity
  instead of auto-cycling blind.
- v1.1.0 (2026-07-01) — Replaced the heartbeat-only stub with a real
  perceive/think/act loop: `task_assignment` messages now trigger an LLM
  call (via the new `llm_client.py`, provider-switchable between Ollama and
  Claude) and a `task_complete`/`clarification_request` reply.
- v1.0.0 — Initial stub: heartbeat `status_update` per tick, printed any
  message addressed to the worker.
