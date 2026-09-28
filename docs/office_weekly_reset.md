# office.weekly_reset — Sunday loop reset (office side)

## Overview

`app/office/weekly_reset.py` (OB-31) runs the ashiorid_office weekly loop reset at Sunday 00:00
America/New_York. When week **W** opens, the week that just ended is **N = W − 1**. Weeks come from
`office.clock.office_time` (see [office_clock.md](office_clock.md)).

Steps, in this fixed order:

| # | Step id | What it does |
|---|---|---|
| 1a | `repo_archive` | Fetch. If the worktree is dirty, commit it as a WIP commit. Keep `loop/<N>` as `archive/week-<N>` and push it. If `loop/<N>` is missing, the base branch is archived instead (logged WARN). Week 0 has no previous week, so the step is skipped. |
| 1b | `repo_reset` | Fetch. Create `loop/<W>` from the `loop-seed` tag, then `reset_hard_to(loop-seed)`, which also runs `git clean -fd`. Push the branch without force. |
| 1c | `repo_prune_branches` | Delete the head branch of every merged PR from this repo, except protected branches and branches that are still the head of an open PR. A 404 counts as already deleted. |
| 1d | `repo_close_issues` | Close open issues labelled `loop-<N>`. Each issue's labels are checked again locally, because Gitea may ignore an unknown label filter. Skipped for week 0. |
| 2 | `clear_session_state` | Delete the agents' on-disk session state (details below). |
| 3 | `character_refresh` | Broadcast a `character_refresh` message on the bus. |
| 4 | `v4_weekly_reset` | Call the injected v4 `weekly-reset` hook. The default is a no-op stub that returns `{"stub": true}`, until WS-F lands. |

### Branch protection

`is_protected_branch(name, config)` is never deleted by the reset. It covers:

- an empty name or `HEAD`
- the base branch (`main`)
- `loop-seed`
- anything under `archive/`
- anything under `loop/` (the week trunks, including the new `loop/<W>`)

PR heads from another repo (forks) are also skipped when `repo_full_name` is set.

### Session state

The reset's `git clean -fd` keeps ignored files, and aider adds `.aider*` to `.gitignore`. So
`clear_session_state` deletes these by default:

- `<workspace>/.aider.chat.history.md`
- `<workspace>/.aider.input.history`
- every `--state-path` you pass

Directories are removed with `rmtree`. The step refuses to remove:

- the filesystem root
- `$HOME`
- the workspace itself
- any directory that contains a `.git`

A refusal fails the step.

The workers' in-process memory (for example `agent_handlers.office._reset_office_state`) can only
be cleared by the worker itself when it receives `character_refresh`.

### Step ledger (idempotency)

The step ledger is a JSON file, written atomically with `relay_io.atomic_write_json(fsync=True)`.
It mirrors v4 `loop_weeks.reset_steps`, keyed by the week being **opened** (W):

```json
{"version": 1, "campaign": "ashiorid_office",
 "weeks": {"2": {"week": 2,
                 "steps": {"repo_archive": {"completed_at": "2026-10-11T04:00:02+00:00",
                                             "detail": {"archive": "archive/week-1", "...": "..."}}},
                 "last_error": {"step": "repo_reset", "error": "ResetStepError: git push of loop/2 failed",
                                "at": "..."},
                 "completed_at": null}}}
```

How the ledger drives a run:

- A step that is already recorded is skipped.
- The first step that fails stops the run and is written to `last_error`. The next run retries it.
- `completed_at` is set once all 7 steps are done.
- A corrupt or unreadable ledger raises `LedgerError`. It is never treated as empty, because that
  would repeat a reset.
- A run for any instant inside week W completes whatever is missing for W. This covers a reset
  missed on Sunday. Cron should fire just after Sunday 00:00.
- Delivery is at least once: if the process dies after a step's side effect but before the
  ledger write, that step runs again. Every step tolerates this:
  - branches that already exist are reused
  - 404 deletes are ignored
  - closing an issue twice is harmless
  - workers should treat a repeated `character_refresh` as a no-op

### `character_refresh` message

There is no `character_refresh` builder in `office/protocol.py`, so the message is built here
with `message_bus.build_message`. Its `correlation_id` is the run id:

```json
{"type": "character_refresh", "from": "office_clock", "to": "broadcast",
 "payload": {"campaign": "ashiorid_office", "week": 2, "closing_week": 1,
             "characters": ["*"], "reason": "weekly_reset", "branch": "loop/2"}}
```

This is the v4 shape (`character_generator_updater_v4.md`), with two extra fields: `closing_week`,
and `branch` (the new trunk the workers should check out).

### Dry run

With `--dry-run`, every action is logged at INFO (`weekly_reset would ... action=...`) and
returned in `actions`. Nothing mutates:

- no fetch, checkout, commit, reset or push
- no branch delete or issue close
- no bus message and no v4 hook call
- no ledger write

Read-only Gitea calls (`list_prs`, `list_issues`) still run, so the preview is real. Steps that
are already recorded in the ledger are skipped in a dry run too.

## Signature

```python
class ResetConfig:  # frozen dataclass
    campaign: str = "ashiorid_office"; base_branch: str = "main"; seed_tag: str = "loop-seed"
    week_branch_fmt: str = "loop/{week}"; archive_branch_fmt: str = "archive/week-{week}"
    issue_label_fmt: str = "loop-{week}"; session_state_paths: tuple = ()
    repo_full_name: str | None = None; sender: str = "office_clock"

class WeeklyReset:
    def __init__(self, git, gitea, ledger, config=None, emit=None, v4_hook=None,
                 epoch=date(2026, 9, 27), tz="America/New_York", workspace=None)
    def week_for(self, at: datetime) -> int
    def run(self, at: datetime | None = None, dry_run: bool = False) -> ResetResult

class StepLedger:
    def __init__(self, path, campaign="ashiorid_office")
    def is_done(self, week, step) -> bool
    def mark_done(self, week, step, detail=None) -> None
    def mark_failed(self, week, step, error) -> None
    def record(self, week, create=False) -> dict | None

def is_protected_branch(name: str, config: ResetConfig | None = None) -> bool
def noop_v4_hook(week, closing_week, campaign="ashiorid_office") -> dict
def main(argv=None, git=None, gitea=None, emit=None, v4_hook=None) -> int
```

## Parameters

| Name | Type | Required | Notes |
|---|---|---|---|
| `git` | `git_client.GitClient` (or a fake) | yes | Must be on the Fraud-Stop working copy that the agents commit in (the Engineer's `WORKSPACE_PATH`). With no `remote_url`, fetch and push are skipped (local-only mode). |
| `gitea` | `gitea_client.GiteaClient` (or a fake) | yes | Must point at the same repo (`gitea_admin/fraud-stop`). Needs `write:repository` and `write:issue`. |
| `ledger` | `StepLedger` or path | yes | The CLI default is `data/office/weekly_reset_ledger.json`, which is gitignored. Keep it outside the Fraud-Stop worktree, or `git clean` deletes it. |
| `config` | `ResetConfig` | no | Branch, tag and label names, plus the state paths. |
| `emit` | `callable(msg)` | no | For example `MessageProducer.send`. If it is `None`, the `character_refresh` step fails and is retried on the next run. |
| `v4_hook` | `callable(week, closing_week, campaign) -> dict` | no | Defaults to `noop_v4_hook`. |
| `epoch` | `date` (a Sunday) | no | The loop epoch. The CLI reads env `OFFICE_EPOCH` and falls back to 2026-09-27. |
| `tz` | `str` | no | Defaults to `America/New_York`. |
| `at` | aware `datetime` | no | The run resets the week that contains `at`. Defaults to now. |

## Return Value

`ResetResult` has these fields:

- `week` and `closing_week`
- `dry_run`
- `run_id`
- `ran` and `skipped` (lists of step ids)
- `failed_step` and `error`
- `actions`: the logged actions
- `details`: step → detail

`ok` is `failed_step is None`. `as_dict()` gives the JSON that the CLI prints.

## CLI

```bash
cd app && ../.venv/bin/python -m office.weekly_reset --help
# or
.venv/bin/python app/office/weekly_reset.py --help
```

| Flag | Default | Meaning |
|---|---|---|
| `--dry-run` | off | Log every action and change nothing. |
| `--at ISO` | now | Force the reset for the week containing this tz-aware instant. OB-42 uses it. |
| `--epoch YYYY-MM-DD` | `$OFFICE_EPOCH` or 2026-09-27 | The loop epoch Sunday. |
| `--tz` | `$OFFICE_TZ` or America/New_York | Time zone for the clock. |
| `--ledger PATH` | `$OFFICE_RESET_LEDGER` or `data/office/weekly_reset_ledger.json` | Where the step ledger lives. |
| `--workspace PATH` | `$WORKSPACE_PATH` | Fraud-Stop working copy. Required. |
| `--remote-url` | `$GIT_SERVER_URL` | Git remote. With none set, the run is local-only. |
| `--gitea-url` / `--owner` / `--repo` | `http://192.168.1.120:3300` / `gitea_admin` / `fraud-stop` | Gitea repo to act on. |
| `--token-env` | `GITEA_TOKEN_OFFICE` | The env var **name** that holds the token. The token itself is never passed or logged. |
| `--base-branch` / `--seed-tag` | `main` / `loop-seed` | Base branch and seed tag names. |
| `--state-path PATH` | (repeatable) | Extra session-state file or directory to delete. |
| `--bootstrap` / `--topic` | `$KAFKA_BOOTSTRAP_SERVERS` / `$KAFKA_TOPIC` or `vtuber.messages` | Kafka connection for `character_refresh`. |
| `--status` | — | Print the ledger record for the week and exit. |
| `-v` | — | DEBUG logging. |

Exit codes:

- 0: ok
- 1: a step failed (it is retried on the next run)
- 2: bad setup (no workspace, or an unreadable ledger)

Cron, just after the reset instant (the host runs in New York time, or set `CRON_TZ`):

```cron
2 0 * * 0  cd ~/codeProjects/virtualTubers/app && ../.venv/bin/python -m office.weekly_reset --workspace /work/fraud-stop
```

## Dependencies

- `office.clock`: `office_time` and `segment_start`
- `office.protocol`: `CLOCK_SENDER`
- `message_bus`: `build_message`, `BROADCAST` and `MessageProducer` (CLI only)
- `relay_io.atomic_write_json`
- `git_client`: `GitClient` and `GitError`
- `gitea_client`: `GiteaClient` and `GiteaError`
- standard library only otherwise

## Usage Examples

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from git_client import GitClient
from gitea_client import GiteaClient
from message_bus import MessageProducer
from office.weekly_reset import ResetConfig, WeeklyReset

git = GitClient("/work/fraud-stop", "office_clock")
gitea = GiteaClient("http://192.168.1.120:3300", "gitea_admin", "fraud-stop")
runner = WeeklyReset(git, gitea, "data/office/weekly_reset_ledger.json",
                     config=ResetConfig(repo_full_name="gitea_admin/fraud-stop"),
                     emit=MessageProducer("kafka:9092", "vtuber.messages").send)
result = runner.run(at=datetime(2026, 10, 11, tzinfo=ZoneInfo("America/New_York")))
print(result.ok, result.ran, result.failed_step)
```

```bash
# Preview week 2's reset, then run it, then check the ledger
../.venv/bin/python -m office.weekly_reset --dry-run --at 2026-10-11T00:00-04:00 --workspace /work/fraud-stop
../.venv/bin/python -m office.weekly_reset --at 2026-10-11T00:00-04:00 --workspace /work/fraud-stop
../.venv/bin/python -m office.weekly_reset --status --at 2026-10-11T00:00-04:00
```

## Error Handling

- Any exception in a step (`GitError`, `GiteaError`, `ResetStepError`, `OSError`, or a hook
  error) is logged at ERROR. It is recorded as `last_error`, unless the run is a dry run, and it
  stops the run. Later steps do not run.
- `ResetStepError` is raised in these cases:
  - a fetch or push fails
  - no bus producer is configured
  - `clear_session_state` is asked to delete a protected directory
- `LedgerError` is raised when the ledger is unreadable, is not JSON, has no `weeks`, or belongs
  to another campaign.
- `ValueError` comes from `office_time` for a naive `at` or an instant before the epoch. The CLI
  rejects a naive `--at` at parse time.
- Tokens: the clients read tokens from env by name. `git_client` and `gitea_client` redact URLs
  and scrub errors, and this module never reads a token.

## Testing

`tests/test_office_weekly_reset.py` uses fake git and Gitea clients. It covers:

- the full reset
- skipping each step through the ledger
- failure followed by resume
- a dry run that makes no mutating calls
- branch protection
- state-path guards
- the CLI

## Changelog

- **v1.0.0** (2026-09-28): first version (OB-31).
