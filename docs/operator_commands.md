# Operator command reference

## Overview

Everything the operator (the human "boss") can send to the worker team goes
through `message-api`'s `POST /messages` endpoint (`docs/message_api.md`),
which publishes onto the shared `vtuber.messages` Kafka topic. This doc is a
practical reference for what to actually send — as opposed to `message_api.md`,
which documents the endpoint itself, and `agent.py` (`docs/agent.md`), which
documents how each message type is handled once it arrives.

Message shape is always the same three fields:

```json
{"to": "<worker or broadcast>", "type": "<message type>", "payload": {...}}
```

`to` is one of `coder`, `coder-native`, `coder-opencode`, `coder-aider`,
`manager`, `tester`, or `broadcast` (fans out to all workers). `type`
defaults to `operator_message` if omitted.

The three `coder-*` workers carry a real coding backend
(`docs/coding_backend.md`): a `task_assignment` sent to them edits actual
files in their workspace volume, commits, and gets REALLY tested by the
tester (`docs/test_runner.md`). The legacy `coder` remains narration-only.
To A/B test the backends, send the same task to each — see
`sandbox/README.md` for canonical tasks, and query `coding_backend_runs`
in Postgres for the results.

## Primary operator commands

These two are the ones an operator uses day to day — everything else in the
message-type table (see below) is normally worker-to-worker traffic that
flows automatically once one of these kicks off a ticket.

### `task_assignment` — give a worker something to do

```json
{"to": "coder", "type": "task_assignment", "payload": {"task": "add a healthcheck endpoint"}}
```

- `to`: `coder`, `manager`, or `tester`.
- `payload.task` (str) — the task description.
- `payload.retry_count` (int, optional) — normally omitted; only meaningful
  when the manager re-delegates a bug fix (see below).

The worker narrates the task via its LLM and replies `task_complete` to
`operator`. If `to` is `coder`, this also kicks off the full pipeline: the
coder additionally sends `commit_notification` to the tester, which reports
`test_passed`/`bug_report` to the manager, which reports back to the
operator as a `manager_report`.

### `operator_message` — talk directly to a worker

```json
{"to": "tester", "payload": {"message": "status?"}}
```
(`type` can be omitted — `operator_message` is the default.)

- `to`: any worker, or `broadcast` (all three reply independently — no
  de-duplication, so a broadcast produces three replies on the feed).
- `payload.message` (str) — free-form text.

Any worker (no role gate) answers in-character with an `operator_reply`
addressed back to `operator`. This is a lightweight chat channel — no tmux
demo side effects, no pipeline handoff.

## Manual/debug commands

`message-api`'s `type` field can be overridden to inject any of the other
message types directly — useful for testing a stage of the pipeline without
running the whole thing end to end. These are normally sent automatically by
one worker to the next; the operator sending them manually skips ahead.

| Type | `to` | Payload fields | Effect |
|---|---|---|---|
| `commit_notification` | `tester` | `task`, `commit_message`, `retry_count` | Triggers a test run without going through the coder first. |
| `retest_request` | `tester` | `task`, `retry_count` | Same handler as `commit_notification` — there is no automatic sender for this type yet, so it's an operator-only lever. |
| `bug_report` | `manager` | `task`, `severity`, `repro`, `retry_count` | Triggers manager triage: re-delegates to the coder (`retry_count + 1`), or escalates to the operator once `retry_count >= 3`. |
| `test_passed` | `manager` | `task` | Triggers the manager's celebration narration + a `manager_report` (`report_type: "milestone"`) to the operator. |
| `task_complete` | `manager` | `task` | Manager acknowledges only — sends nothing back onto the bus by design (avoids duplicating the tester run the coder's own `commit_notification` already triggered). |
| `clarification_request` | `manager` | `task`, `error` | Forces an immediate `manager_report` (`report_type: "blocker"`) escalation to the operator. |

All of these are role-gated: sending one to a worker whose configured
`agent.role` doesn't match (e.g. `bug_report` to the tester) is a no-op — the
worker logs `ignoring <type> (role=..., expected ...)` and does nothing.

## Feed-only types (not operator-sendable)

Two types only ever appear as *output* — sending them yourself doesn't do
anything useful, but knowing what they mean helps reading the Kafka feed
pane:

- **`manager_report`** — manager → operator. Payload `report_type`:
  `"milestone"` (celebrating a passing suite), `"blocker"` (a worker's
  `clarification_request` escalated), or `"escalation"` (a bug hit
  `MAX_BUG_RETRIES = 3`). Highlighted cyan on the feed.
- **`operator_reply`** — any worker → operator, in response to an
  `operator_message`. Highlighted blue on the feed.
- **`status_update`** — heartbeat traffic, sent by every worker every tick.
  Hidden from the feed by default (heartbeat flood filter).

## Full pipeline example

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "add a healthcheck endpoint"}}'
```

Watch the Kafka feed pane: coder → `task_complete` + `commit_notification` →
tester → `test_passed` **or** `bug_report` → manager → `manager_report`
(milestone) or a re-delegated `task_assignment` back to the coder. A bug path
terminates within 3 retries with an `escalation` report to the operator.

## Using the PowerShell helper

`scripts/send_test_message.ps1` wraps the same endpoint:

```powershell
.\scripts\send_test_message.ps1 -To coder -Type task_assignment -Payload '{"task": "add a healthcheck endpoint"}'
.\scripts\send_test_message.ps1 -To broadcast -Type operator_message -Payload '{"message": "stream starting in 5"}'
```

## See also

- `docs/message_api.md` — the HTTP endpoint itself.
- `docs/agent.md` — how each message type is handled worker-side.
- `docs/message_bus_feed.md` — reading the Kafka feed pane (highlight colors, filters).
- `.claude/prompts/worker_interaction_kafka.md` — the implementation plan that landed this collaboration flow.
