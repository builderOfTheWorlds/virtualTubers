# task_backlog (manager idle dispatch)

## Overview

Normally a task only arrives when the operator POSTs a `task_assignment`
(message-api); after the manager's `manager_report` the team sits idle. The
**task backlog** is an opt-in queue the manager pulls from when the team is
idle, so the dev-team show can run on its own.

Two parts:

- `app/task_backlog.py` — pluggable **sources** (`file`, `gitea`) with one
  interface: `next_task()`, `mark_started(id)`, `mark_done(id, outcome)`.
- `app/agent_handlers/manager.py` — `BacklogDispatcher` + the
  `manager_idle_tick` hook the agent loop calls every enabled tick
  (`agent_handlers.IDLE_TICK_HOOKS["manager"]`).

**Off by default** (`agent.backlog.enabled: false`, nothing enabled in
`config/workers/manager.yaml`).

### Chain lifecycle

A **chain** is one task's correlation id (docs/message_bus.md "Correlation
IDs"). The dispatcher tracks in-flight chains:

| Event (seen by the manager) | Effect on the chain |
|---|---|
| backlog dispatch (`task_assignment` → coder, **new** correlation id) | starts, in flight; `mark_started` |
| `task_complete` | activity only (the handler still sends **nothing**) |
| `bug_report`, re-delegated fix (retry < `MAX_BUG_RETRIES`) | activity only — a retry does not end the chain |
| `bug_report` escalation (retry cap, or manager LLM failure) | ends → `mark_done(escalation)` |
| `test_passed` | ends → `mark_done(milestone)` (even if the narration LLM fails) |
| `clarification_request` | ends → `mark_done(blocker)`; **not** retried, never auto-reassigned |
| no manager-visible activity for `stale_after_s` | dropped with a WARN → `mark_done(escalation)` + an `escalation` `manager_report` (`reason: "stale"`) |

Chains the backlog did not start (an operator-posted task the manager first
sees via `bug_report`/`task_complete`) are tracked as *foreign* chains:
they block backlog dispatch until they end (so the backlog never talks over
the operator's task mid bug loop) but never call `mark_done`.

### Dispatch rule

On each enabled tick the dispatcher first drops stale chains, then
dispatches when **all** hold:

1. `agent.backlog.enabled` and the worker's role is `manager`;
2. zero chains in flight;
3. `cooldown_s` has passed since the last chain ended, the last poll, or
   startup (so an empty backlog is polled at most once per cooldown).

It narrates the pick (LLM line, fallback text on LLM failure), writes
`agent_state` `thinking` → `speaking`, sends

```json
{"type": "task_assignment", "from": "manager", "to": "<coder>",
 "payload": {"task": "<title>. <body>", "retry_count": 0,
             "backlog_id": "<id>", "backlog_source": "file|gitea"}}
```

(new chain: `correlation_id == id`, `causation_id: null`) to the next coder
in `coders` (round-robin), then calls `mark_started`. The coder's replies
(`task_complete` to the manager, `commit_notification` to the tester) stay
on that chain, so the `test_passed`/`bug_report` that comes back ends it.

**Worker on/off:** the agent loop `continue`s on a disabled tick before
handlers and the idle hook run, so nothing is dispatched (and the
dispatcher isn't even built) while the manager is disabled.

### Sources

**`file`** — `file_path` is a YAML/JSON list of `{id, title, body?}` (or a
mapping with a `tasks:` list); see `config/backlog.example.yaml`. Served in
file order; re-read every poll (append tasks live). Done-state is written
atomically (`relay_io.atomic_write_json`, fsync) to `state_path`:
`{"started": {id: {...}}, "done": {id: {"outcome", "at", "correlation_id"}}}`.
A done task (any outcome) is never served again; a started-but-unfinished
task (restart mid-chain) is served again. Delete an id from `done` to
re-run it.

**`gitea`** — open issues carrying `label` on `{base_url}/{owner}/{repo}`,
oldest (lowest number) first, skipping PRs and issues already labelled
`in_progress_label` or `blocked_label`.
- `mark_started`: add `in_progress_label`, comment "Picked up ... correlation_id".
- `mark_done`: comment the outcome + correlation id, remove
  `in_progress_label`; milestone → close the issue if `close_on_success`;
  escalation/blocker → add `blocked_label` (a human removes it to re-queue).
- Label names are resolved to ids via `GET /repos/{o}/{r}/labels`; a label
  missing on the repo is skipped with a WARN (comments still land). Issue
  ids handled this process are remembered in memory too.
- Token: env `GITEA_TOKEN` only (`read:issue` + `write:issue`), passed to
  `worker-manager` only. Never in config, never logged. No token → WARN,
  `next_task()` returns None.
- stdlib `urllib` only; 10 s timeout. Any HTTP/network error → WARN,
  `next_task()` returns None / the write is skipped. Never raises.

## Signature

```python
# app/task_backlog.py
OUTCOMES = ("milestone", "escalation", "blocker")

class BacklogSource:
    name: str
    def next_task(self) -> dict | None   # {"id", "title", "body", "source"}
    def mark_started(self, task_id: str, correlation_id: str | None = None) -> None
    def mark_done(self, task_id: str, outcome: str, correlation_id: str | None = None) -> None

class FileBacklogSource(BacklogSource):
    def __init__(self, file_path="/config/backlog.yaml",
                 state_path="/data/world-state/backlog_state.json")

class GiteaBacklogSource(BacklogSource):
    def __init__(self, base_url, owner, repo, label="stream-task",
                 close_on_success=False, in_progress_label="in-progress",
                 blocked_label="needs-human", token=None, opener=None, timeout_s=10)

def build_backlog(backlog_config: dict | None) -> BacklogSource | None

# app/agent_handlers/manager.py
class BacklogDispatcher:
    def __init__(self, source, coders=("coder",), cooldown_s=60,
                 stale_after_s=1800, clock=None)
    def tick(self, worker_id, agent_config, llm_client, producer, state_path=None) -> dict | None
    def note_activity(self, correlation_id) -> None
    def end_chain(self, correlation_id, outcome) -> dict | None
    def drop_stale(self, producer=None, worker_id="manager") -> list[str]

def manager_idle_tick(worker_id, agent_config, llm_client, producer, state_path=None) -> dict | None
```

## Parameters

Config (`agent.backlog`, annotated in `config/worker.yaml`):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Master switch. |
| `source` | `file` | `file` \| `gitea` (anything else → WARN, disabled). |
| `file_path` | `/config/backlog.yaml` | file source task list (mount it into the manager). |
| `state_path` | `/data/world-state/backlog_state.json` | file source done-state (persistent volume). |
| `gitea.base_url` / `owner` / `repo` | — | Gitea repo (required for gitea). |
| `gitea.label` | `stream-task` | Issues to pull. |
| `gitea.in_progress_label` | `in-progress` | Added on start, removed on done. |
| `gitea.blocked_label` | `needs-human` | Added on escalation/blocker; such issues are skipped. |
| `gitea.close_on_success` | `false` | Close the issue on a milestone. |
| `coders` | `[coder]` | Worker ids to assign to, round-robin. |
| `cooldown_s` | `60` | Gap after chain end / poll / startup before the next pull. |
| `stale_after_s` | `1800` | Idle chain → dropped as escalation. |

Env: `GITEA_TOKEN` (gitea source only).

## Return Value

`next_task()` → task dict or None. `mark_*` → None. `tick()` /
`manager_idle_tick()` → the sent `task_assignment` envelope, or None.

## Dependencies

`yaml`, stdlib `urllib`/`json`, `relay_io` (atomic state writes),
`message_bus.build_message` / `correlation_of`, `agent_handlers.common`
(`_complete_with_emotion`, `_send_manager_report`), `agent_state.write_state`.

## Usage Examples

Run the seeded sandbox tasks unattended (file source):

```yaml
# config/workers/manager.yaml
agent:
  backlog:
    enabled: true
    source: file
    file_path: /config/backlog.yaml
    coders: [coder-native, coder-opencode]
```

```yaml
# docker-compose.yml, worker-manager volumes
- ${REPO_ROOT:-.}/config/backlog.example.yaml:/config/backlog.yaml:ro
```

Pull from Gitea issues labelled `stream-task`:

```yaml
agent:
  backlog:
    enabled: true
    source: gitea
    gitea: {base_url: "http://192.168.1.120:3300", owner: gitea_admin,
            repo: virtualTubers, label: stream-task, close_on_success: true}
```

```bash
# .env
GITEA_TOKEN=<issues token with read:issue + write:issue>
```

Directly:

```python
from task_backlog import FileBacklogSource
src = FileBacklogSource("config/backlog.example.yaml", "/tmp/state.json")
task = src.next_task()            # {"id": "sandbox-divide-by-zero", ...}
src.mark_done(task["id"], "milestone", correlation_id="...")
```

## Error Handling

- Sources never raise from `next_task`/`mark_*` for I/O, parse or network
  problems (WARN + None / skipped write). `mark_done` raises `ValueError`
  for an outcome outside `OUTCOMES` (programming error).
- `BacklogDispatcher` catches `mark_started`/`mark_done` exceptions (ERROR
  log); `manager_idle_tick` catches everything (ERROR log) so the agent loop
  never crashes on the backlog.

## Changelog

- v1.0.0 (2026-09-27) — Created: `file` and `gitea` sources, manager idle
  dispatch with per-chain tracking, cooldown, stale guard, round-robin
  coders; `IDLE_TICK_HOOKS` in the agent loop.
