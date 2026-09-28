# agent_handlers (package)

## Overview

`app/agent_handlers/` holds every bus message handler the agent loop
(`app/agent.py`, docs/agent.md) dispatches, split by role/concern into small
focused modules so later work (a correlation ID threaded through handlers,
a manager backlog, campaign integration) lands on one small file rather
than a 1,200-line `agent.py`. The package's `__init__.py` builds the
`MESSAGE_HANDLERS` dispatch table (message type → handler) that `main()`
looks each incoming message up in.

Behaviour, message types, payloads and log lines are documented in
docs/agent.md — this page only covers the layout and the contracts that
matter when editing or testing it.

| Module | Handles | Also contains |
|---|---|---|
| `__init__.py` | — | `MESSAGE_HANDLERS`, `IDLE_TICK_HOOKS` |
| `common.py` | — | `_complete_with_emotion` (structured LLM narration, via `emotion.py`), `_send_manager_report` |
| `relay_files.py` | — | agent → replay pane relay files. Thin aliases onto `app/relay_io.py` (docs/relay_io.md, the one race-safe implementation shared with `replay_pane.py` / `tile_pane.py`): `REPLAY_{REQUEST,STOP,CUE,READY}_FILE_ENV` + `DEFAULT_*` paths, `_resolve_replay_*_file`, `_atomic_write_json` (= `relay_io.atomic_write_json`), `_read_json_file` (= `relay_io.read_json`), and `_write_replay_request(request, if_absent=False)` (`if_absent=True` uses `relay_io.atomic_create_json`, so "don't clobber a pending request" is one atomic step) |
| `coder.py` | `task_assignment` | tmux demos `demo_editor_note`, `demo_filetree_ls`, `show_commit_in_filetree` |
| `tester.py` | `commit_notification`, `retest_request` | `_run_tests_and_report`, `_decide_test_outcome` + stub constants, `_resolve_workspace`, `WORKSPACE_MOUNT_PATTERN`, `_severity_from_failures` |
| `manager.py` | `bug_report`, `test_passed`, `task_complete`, `clarification_request` | `MAX_BUG_RETRIES`; task backlog `BacklogDispatcher`, `manager_idle_tick`, `_backlog_activity` / `_backlog_end`, `_reset_backlog` |
| `operator.py` | `operator_message` (office Party Member: silent ack, no LLM — U6) | — |
| `viewer.py` | `viewer_joined` (office Party Member: rerun only, no greeting — U6) | `_pick_rerun_episode` |
| `replay_relay.py` | `replay_request`, `replay_stop`, `replay_invite`, `replay_ready`, `replay_cue`, `replay_end` | `_is_valid_cast` |
| `office.py` | `directive`, `functional_plan`, `technical_plan`, `test_request`, `status_report`, `phase_change` | idle hooks `ceo_idle_tick` / `office_manager_idle_tick` / `observer_idle_tick`; `issue_directive`, `set_day_runner`; office_role hooks `engineer_prepare` / `engineer_handoff` / `tech_lead_after_test_passed`; `lane_commit`, `collect_garbage` — see "Office handlers" below |

Dependency direction is one-way: handler modules import from `common` /
`relay_files` (`operator` and `viewer` also import `office.office_role_of` for the
U6 Party Member gate) (and from sibling app modules such as `message_bus`,
`agent_state`, `tmux_control`, `test_runner`, `episode_store`, `relay_io`,
`task_backlog`); `common` and `relay_files` import nothing from the package; nothing in the package
imports `agent`.

### Correlation contract (v1.1.0)

Every bus send made **while handling `msg`** in `coder.py`, `tester.py`,
`manager.py`, `operator.py` and `common._send_manager_report` passes
`**reply_ids(msg)` to `build_message` (docs/message_bus.md "Correlation
IDs"): the new message keeps `msg`'s `correlation_id` (falling back to
`msg["id"]` for older senders) and gets `causation_id = msg["id"]`. The
manager's bug-fix re-assignment therefore keeps the original task's chain.
`_send_manager_report(..., cause=msg)` takes the triggering message as a
keyword (default `None` = new chain). New dev-team sends must follow the
same rule. `viewer.py`, `replay_relay.py` and `relay_files.py` are not
threaded (their messages start their own chains; the duet protocol uses
`airing_id`).

### Idle-tick hooks (v1.2.0)

`IDLE_TICK_HOOKS` maps a role to a hook the agent loop calls once per
**enabled** tick, after the message handlers:

```python
hook(worker_id, agent_config, llm_client, producer, state_path=None) -> None
```

A hook must never raise. `"manager": manager_idle_tick` (below) plus the three office hooks (`ceo`, `office_manager`, `observer` — see "Office handlers"). The manager hook
drives the opt-in task backlog (docs/task_backlog.md). Because handlers are
stateless functions, the backlog's in-flight chain tracker is one
module-level `BacklogDispatcher` in `manager.py` (built lazily from
`agent.backlog` on the first tick; `None` when disabled). The manager
handlers report to it: `handle_bug_report` / `handle_task_complete` →
`_backlog_activity(msg)` (retries keep the chain open), `handle_test_passed`
→ `_backlog_end(msg, "milestone")`, a bug escalation →
`"escalation"`, `handle_clarification_request` → `"blocker"`. Both are
no-ops while the backlog is disabled. `task_complete` still sends nothing,
and a clarification still never re-assigns. Tests reset the tracker with
`agent_handlers.manager._reset_backlog()`.

## Signature

Every handler shares one signature:

```python
def handle_<type>(worker_id: str, agent_config: dict, llm_client,
                  producer, msg: dict, state_path: str | None = None,
                  coding_backend=None) -> None
```

```python
MESSAGE_HANDLERS: dict[str, Callable]  # 21 entries (15 dev-team/replay + 6 office), see __init__.py
```

## Parameters

Identical for every handler — see docs/agent.md "Parameters".
`coding_backend` is only used by `handle_task_assignment`; the rest accept
and ignore it so dispatch stays uniform.

## Return Value

Handlers return `None` (side effects only: bus sends, avatar state writes,
relay-file writes, tmux keystrokes, console prints).

## Dependencies

- `message_bus.build_message`, `message_bus.reply_ids` / `correlation_of`
  (dev-team handlers), `agent_state.write_state` (most handlers)
- `emotion` (`common.py`), `tmux_control` (`coder.py`),
  `test_runner` (`tester.py`), `episode_store` (`viewer.py`),
  `relay_io` (`relay_files.py`, docs/relay_io.md), `task_backlog`
  (`manager.py`, docs/task_backlog.md)
- stdlib: `json`, `os`, `random`, `time`

## Usage Examples

```python
# The agent loop (app/agent.py main()):
from agent_handlers import MESSAGE_HANDLERS

handler = MESSAGE_HANDLERS.get(msg["type"])
if handler:
    handler(worker_id, agent_config, llm_client, producer, msg, state_path,
            coding_backend=coding_backend)
```

```python
# Tests: patch the module that LOOKS THE NAME UP, not `agent`.
from agent_handlers import tester
from agent_handlers.tester import handle_commit_notification

def test_pass(monkeypatch):
    monkeypatch.setattr(tester, "_decide_test_outcome", lambda: (True, None))
    handle_commit_notification("tester", {"role": "tester"}, llm, producer, msg)
```

Where to patch:

| To stub | Patch |
|---|---|
| tmux calls / demo helpers | `agent_handlers.coder` (`select_pane`, `send_*`, `demo_*`) |
| test outcome / pytest run | `agent_handlers.tester` (`_decide_test_outcome`, `workspace_testable`, `run_pytest`) |
| relay-file writes in replay handlers | `agent_handlers.replay_relay` (`_atomic_write_json`, `_write_replay_request`) |
| relay-file writes in viewer_joined | `agent_handlers.viewer` (`_write_replay_request`) |
| episode library | `agent_handlers.viewer.episode_store` (the shared `episode_store` module object) |
| relay-file paths | env vars `REPLAY_REQUEST_FILE` / `REPLAY_STOP_FILE` / `REPLAY_CUE_FILE` / `REPLAY_READY_FILE` |

`app/agent.py` re-exports all these names for `from agent import ...`
callers, but `monkeypatch.setattr(agent, ...)` only rebinds the re-export
and does **not** reach the handlers.

## Error Handling

Unchanged from before the split — see docs/agent.md "Error Handling"
(LLM failures are caught per handler; role mismatches log and no-op; relay
file write failures log and never raise out of the tick loop).

## Party Member silence (v1.4.0, decision U6)

The ashiorid_office Party Member never speaks. Both any-role handlers check
`office.office_role_of(agent_config)` (from `agent.office_role`) first:

- **`handle_operator_message`** on a `party_member` worker makes **no LLM
  call** and writes no bubble (state `idle`, action "listened to the
  operator"). It still answers the operator, with a non-text acknowledgement
  that keeps the correlation chain:

  ```json
  {"type": "operator_reply", "to": "operator",
   "payload": {"silent": true, "office_role": "party_member"}}
  ```

- **`handle_viewer_joined`** on a `party_member` worker still queues the
  rerun (step 1, unchanged), then **omits the greeting**: no LLM call, no
  bubble, nothing on the bus.

Every other role, a worker without `office_role`, and an unknown
`office_role` value (logged by `office_role_of`) behave as before. Tests:
`tests/test_party_member_silence.py`.

## Office handlers (v1.3.0, OB-21)

`office.py` runs the ashiorid_office chain (build plan E2/E3/E6) on the same
bus and the same handler signature. The Tech Lead, Engineer and Tester reuse
the dev-team handlers (`agent.role` manager / coder / tester). Small
`office_role` hooks in those handlers route the chain onto office seats.
Without `agent.office_role`, every dev-team path behaves exactly as before.

```
CEO ──directive──▶ TL (ack only), Analyst, Marketing, Office Manager
Analyst ──functional_plan──▶ TL          (docs/requirements/ PR)
Marketing / OM ──status_report──▶ CEO    (marketing/ PR, CHANGELOG.md PR)
TL ──technical_plan──▶ CEO               (docs/design/ PR; COMMENT-review + merge Analyst PR)
TL ──task_assignment──▶ Engineer         (existing type, coder handler)
Engineer: coding run on office/engineer/task-<chain8> ──test_request──▶ Tester  (src/ PR)
Tester ──test_passed | bug_report──▶ TL   (CI comment on the Engineer PR)
TL ──status_report(done)──▶ CEO          (COMMENT-review + merge Engineer PR)
CEO closes the directive issue.
```

A bug report from the Tester follows the existing manager loop. The Tech Lead
re-assigns the fix to `coder_id`. The Engineer re-uses the same branch and
opens no new PR, because the branch is keyed on the chain.

Each office handler does five steps:

1. It checks the role and the rank with `office.protocol.validate_message`.
   A violation logs `event=rank_violation ... outcome=retake`, and the
   message is dropped.
2. It builds the persona prompt with `office.brief_stub` (docs/brief_stub.md).
   When the cast file is missing it falls back to `agent.system_prompt`.
3. It makes one LLM call through `_complete_with_emotion`. A failure uses a
   fallback line, so the chain never stops on it.
4. It does the lane action through `git_client` / `gitea_client`. Every
   written path goes through `lane_allows` first. A git or Gitea failure is
   logged, and the message still goes out.
5. It sends the next protocol message with `reply_to=msg`, so the day's
   directive stays on one `correlation_id`.

| Handler | Office roles | Lane action | Sends |
|---|---|---|---|
| `handle_directive` | analyst, tech_lead, marketing, office_manager | Analyst `docs/requirements/<day>-<slug>.md`; Marketing `marketing/<day>-<slug>.md`; OM appends to `CHANGELOG.md`; TL none | Analyst: `functional_plan`; Marketing/OM: `status_report` (on_track); TL: nothing |
| `handle_functional_plan` | tech_lead | COMMENT review + merge of the Analyst PR; `docs/design/<day>-<slug>.md` PR | `technical_plan` → CEO, then `task_assignment` → Engineer |
| `handle_technical_plan` | ceo | comment on the directive issue | nothing |
| `handle_test_request` | tester | CI result comment on the PR | `test_passed` / `bug_report` → TL (reused `tester._run_tests_and_report`) |
| `handle_status_report` | ceo, tech_lead | CEO: comment + close the issue on the TL's `done` | nothing |
| `handle_phase_change` | all | none | nothing. Speaking roles say one line. The Party Member only changes pose and never calls the LLM. |

Idle hooks (`IDLE_TICK_HOOKS`, keyed by `agent.role`):

- `ceo_idle_tick` calls the day runner that `set_day_runner(fn)` installed.
  Since OB-30 it installs one itself: on the first CEO tick, if the worker
  config has an `agent.office.day_runner` block (and not `enabled: false`),
  it builds the runner with `office.day_runner.build_day_runner` and installs
  it with `set_day_runner`, once per process (`_reset_office_state()`
  re-arms it; a build failure logs `day_runner_install_failed`). A runner
  installed explicitly with `set_day_runner` always wins. Without the block
  it stays a no-op. The runner calls
  `issue_directive(worker_id, agent_config, llm, producer, text, title=, day=)`;
  see docs/office_day_runner.md.
- `office_manager_idle_tick` rotates `coffee` → `cleanup` →
  `garbage_collection` every `chores.interval_s` (default 1800 s).
  Garbage collection deletes the head branches of **closed** PRs. It only
  touches branches that start with `office/` and that no open PR still uses.
  It never touches the base branch or `loop/*`. A 404 means the branch is
  already gone.
- `observer_idle_tick` runs every `observer.every_ticks` ticks (default 3).
  It broadcasts `observer_pose` `{seat, pose: "idle_watch", gaze_target}` and
  writes an avatar state with `bubble=None`. The gaze target is the live
  speaker from `observer.stage_path` (gaze.py) when one is set, else a
  rotation over the seats. It has no LLM and no text.

office_role hooks in the reused handlers (each one is gated on
`agent.office_role`):

- `coder.handle_task_assignment` calls `office.engineer_prepare` before the
  coding run and `office.engineer_handoff` in place of
  `commit_notification`. The handoff checks the lane of `git diff
  --name-only` (src/ only). A violation sends a `clarification_request` to
  the assigner. Otherwise it pushes the branch, opens or re-uses the PR,
  and sends `test_request`.
- `coder.handle_task_assignment`, backend-failure path: the
  `clarification_request` goes to `agent.manager_id` (default `"manager"`).
- `tester._run_tests_and_report(..., report_to="manager", extra=None)` now
  returns the message it sent. The office Tester passes the TL seat and
  the chain context (pr, branch, commit, directive_id, issue, title).
- `manager.handle_test_passed` calls `office.tech_lead_after_test_passed`
  after its `manager_report`. That hook COMMENT-reviews and merges the
  Engineer PR and sends `status_report(done)` to the CEO.

The Tech Lead reviews with **COMMENT**, never APPROVE. Gitea returns 422 on
approving a PR opened with the same shared token (OB-23).

Worker config (`agent:` block; OB-22 writes the real files):

```yaml
agent:
  role: analyst               # handler family (E2)
  office_role: analyst        # OfficeRole value
  manager_id: tuber_1         # coder only: backend-failure blocker recipient
  office:
    pack_dir: /campaigns/ashiorid_office   # else env OFFICE_PACK_DIR, else repo default
    max_backstory_chars: 4000              # optional
    workspace: /data/repos/fraud-stop      # enables lane commits (Engineer: coding backend workspace)
    remote_url: ssh://git@192.168.1.120:2222/gitea_admin/fraud-stop.git
    base_branch: main
    author_name: "Maren Voss"
    merge_prs: true                        # TL merges after its COMMENT review
    narrate_phase_change: true
    gitea:                                 # absent or enabled: false = no Gitea calls
      base_url: http://192.168.1.120:3300
      owner: gitea_admin
      repo: fraud-stop
      token_env: GITEA_TOKEN_OFFICE        # the Party Member always gets GITEA_TOKEN_OBSERVER, read-only
    chores: {interval_s: 1800, rotation: [coffee, cleanup, garbage_collection]}
    observer: {every_ticks: 3, stage_path: /tmp/relay/stage.json}
```

Extra payload keys ride along on the protocol messages. The protocol
validates only the keys it knows about:

- `functional_plan` and `technical_plan` carry `directive`, `title`,
  `issue`, `day` and `pr`.
- `functional_plan` also carries `branch`.
- `technical_plan` also carries `requirements_merged`.
- `test_request` carries `task`, `retry_count`, `coder_id`, `narration`,
  `pr`, `directive_id`, `issue`, `title` and `directive`.
- `status_report` carries `issue`, `directive_id` and `pr`. The TL's report
  also carries `task` and `merged`.
- `task_assignment` from the TL carries `directive_id`, `title`, `issue`
  and `directive`.

Where to patch in tests: `agent_handlers.office.build_gitea_client`,
`agent_handlers.office.build_git_client` and
`agent_handlers.office._changed_paths`. Reset the per-process state with
`office._reset_office_state()`. See tests/test_agent_handlers_office.py; its
fake-bus e2e runs the whole chain.

## Changelog

- v1.4.0 (2026-09-28): U6 Party Member silence in `operator_message` (silent
  `operator_reply` `{"silent": true}`, no LLM) and `viewer_joined` (rerun
  queued, greeting omitted). `ceo_idle_tick` description updated for the
  OB-30 day-runner auto-install.

- v1.3.0 (2026-09-27): Office handlers (`office.py`, OB-21). 6 new message
  types and 3 new idle hooks are registered. The coder, tester and manager
  get office_role hooks, which are no-ops without `agent.office_role`. The
  tester's `_run_tests_and_report` gains `report_to=` / `extra=` and returns
  the sent message. The coder's backend-failure blocker goes to
  `agent.manager_id` (default `"manager"`).

- v1.2.1 (2026-09-27) — Docs only: `relay_files.py` is now aliases onto
  `app/relay_io.py` (race-safe relay IO); dependency list updated.

- v1.2.0 (2026-09-27) — `IDLE_TICK_HOOKS` (per-role per-tick hook) and the
  manager's task backlog (`BacklogDispatcher`, `manager_idle_tick`); the
  manager handlers report chain activity/end to it. No change to message
  types, sends, or behaviour while `agent.backlog` is disabled.
- v1.1.0 (2026-09-27) — Correlation IDs threaded through the dev-team
  handlers (`coder`, `tester`, `manager`, `operator`, `common`); structured
  log lines there gain `correlation_id=`; `_send_manager_report` gains
  `cause=`. The manager logs a `re-delegating fix ... correlation_id=` line
  before a bug-fix re-assignment. Message types and payloads unchanged.
- v1.0.0 (2026-09-27) — Created by splitting `app/agent.py` (pure
  refactor, zero behaviour change). Function bodies moved verbatim;
  `agent.py` keeps `main()` and backward-compatible re-exports.
