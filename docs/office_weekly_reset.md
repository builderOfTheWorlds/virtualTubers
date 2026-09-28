# office.weekly_reset — Sunday loop reset (office side)

## Overview

`app/office/weekly_reset.py` (OB-31) runs the ashiorid_office weekly loop reset at Sunday 00:00
America/New_York. When week **W** opens, the week that just ended is **N = W − 1**. Weeks come from
`office.clock.office_time` (see [office_clock.md](office_clock.md)) and use the **v4 LoopClock
numbering**: week 1 starts at the epoch Sunday (2026-09-27 00:00 NY) and week N+1 starts each
following Sunday 00:00, so the first week's trunk is `loop/1`.

**The v4 reset is authoritative.** The office keeps its own Fraud-Stop repo steps and the
session-state clearing, then runs the v4 `weekly-reset` job
([character_generator_updater_v4.md](charcterProfileGenerationNotes/character_generator_updater_v4.md)
§5, WP-20). That job archives the characters' week and publishes the **only**
`character_refresh`; the office no longer publishes one.

Steps, in this fixed order:

| # | Step id | What it does |
|---|---|---|
| 1a | `repo_archive` | Fetch. If the worktree is dirty, commit it as a WIP commit. Keep `loop/<N>` as `archive/week-<N>` and push it. If `loop/<N>` is missing, the base branch is archived instead (logged WARN). Week 1 has no previous week, so the step is skipped. |
| 1b | `repo_reset` | Fetch. Create `loop/<W>` from the `loop-seed` tag, then `reset_hard_to(loop-seed)`, which also runs `git clean -fd`. Push the branch without force. |
| 1c | `repo_prune_branches` | Delete the head branch of every merged PR from this repo, except protected branches and branches that are still the head of an open PR. A 404 counts as already deleted. |
| 1d | `repo_close_issues` | Close open issues labelled `loop-<N>`. Each issue's labels are checked again locally, because Gitea may ignore an unknown label filter. Skipped for week 1. |
| 2 | `clear_session_state` | Delete the agents' on-disk session state (details below). |
| 3 | `v4_weekly_reset` | Run the v4 `weekly-reset` job (`CommandV4Hook`, below). It publishes `character_refresh`. A failing or missing command fails the step, and the next run retries it. |

The old step 3 `character_refresh` (the office's own broadcast) is **retired**
(`STEP_REFRESH_RETIRED`, `RETIRED_STEPS`). A ledger that recorded it still loads; the entry is
kept and never counts towards completion.

### The v4 hook: `CommandV4Hook`

| | |
|---|---|
| Default command | `python services/character-updater/main.py weekly-reset --at <iso>` (`sys.executable`, the script under the repo root, cwd = repo root) |
| Override | `--v4-command "…"` or env `OFFICE_V4_RESET_CMD`, shlex-split, e.g. `docker compose run --rm character-jobs weekly-reset` |
| `--at` | The run's instant (`--at` of the office run, or now) is appended as `--at <iso>`, or substituted for a literal `{at}` token in the command |
| Timeout | `--v4-timeout` / env `OFFICE_V4_RESET_TIMEOUT_S`, default 1800 s |
| Kafka | `--bootstrap` (or `$KAFKA_BOOTSTRAP_SERVERS`) is passed to the job's environment as `KAFKA_BOOTSTRAP_SERVERS`: the job's producer publishes the refresh |
| Success | exit 0 (`status: done`) or 2 (`status: nothing_to_do`: already done, locked by a running copy, or not due — the first week's reset has no week 0 to close) |
| Failure | any other exit code (the last 300 chars of stderr/stdout go in the error), the default script missing (`not installed`), the command not found, or a timeout → `ResetStepError` |

With the office run at Sunday 00:00 of week W, the v4 job (no `--force`) resets week W − 1 and
publishes `character_refresh` for week W: the same W the office opened. Tests inject the hook
(`v4_hook=callable(week, closing_week, campaign, at)`, or `CommandV4Hook(runner=…)`).

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

The workers' in-process memory can only be cleared by the worker itself when it receives the v4
job's `character_refresh`: `agent_handlers.office.handle_character_refresh` clears the office
state (`_refresh_office_state`: directives, phase, chores, observer rotation; the day runner
stays) and, on each seat with its own Fraud-Stop clone, runs `git fetch` + `git checkout
loop_branch(payload.week)`. The Tester's read-only mount and seats without a clone skip git.
Repeating it is harmless.

### Step ledger (idempotency)

The step ledger is a JSON file, written atomically with `relay_io.atomic_write_json(fsync=True)`.
It mirrors v4 `loop_weeks.reset_steps`, keyed by the week being **opened** (W):

```json
{"version": 2, "campaign": "ashiorid_office",
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
- `completed_at` is set once all 6 steps (`STEPS`) are done. Retired steps never count.
- **v1 ledgers** (0-based week numbers, written before the v4 numbering) are migrated on load:
  every week key moves up by one (`"1"` → `"2"`), each record gets `week` updated and
  `v1_week` set, and the next write saves `version: 2`. A dry run or `--status` never writes. An
  unknown version or a non-integer v1 week key raises `LedgerError`.
- A corrupt or unreadable ledger raises `LedgerError`. It is never treated as empty, because that
  would repeat a reset.
- A run for any instant inside week W completes whatever is missing for W. This covers a reset
  missed on Sunday. Cron should fire just after Sunday 00:00.
- Delivery is at least once: if the process dies after a step's side effect but before the
  ledger write, that step runs again. Every step tolerates this:
  - branches that already exist are reused
  - 404 deletes are ignored
  - closing an issue twice is harmless
  - the v4 job is idempotent itself (`loop_weeks.reset_steps`) and exits 2 when already done;
    a repeated `character_refresh` is a no-op for the seats

### `character_refresh` message (published by the v4 job)

The office does not build this message any more. The v4 job sends (plan §3.2):

```json
{"type": "character_refresh", "from": "character-updater", "to": "broadcast",
 "payload": {"campaign": "ashiorid_office", "week": 2, "characters": ["*"],
             "reason": "weekly_reset"}}
```

There is no `branch` key: seats derive it with `loop_branch(payload.week)` (`loop/2`).
`handle_character_refresh` still accepts a legacy `branch` key, used only when the week is
missing or invalid.

### The week trunk for everyone else: `week_for`, `loop_branch`, `current_loop_branch`

One week computation (`office.clock.office_time`), three helpers:

- `week_for(now, epoch, tz)`: the loop week (v4 numbering) containing `now` (tz-aware, default
  now; `epoch` a date or `YYYY-MM-DD`). `WeeklyReset.week_for` and the CLI use it.
- `loop_branch(week, fmt)`: `loop/<week>`; `ValueError` unless `week` is an int ≥ 1.
  `handle_character_refresh` maps `payload.week` through it.
- `current_loop_branch(now, epoch, tz)`: `loop_branch(week_for(...))`. The branch the office
  seats target (`agent_handlers.office._base_branch`, `agent.office.base_branch: auto`) is
  always the one this reset created.

They raise `ValueError` for a naive `now`, a non-Sunday epoch, an unknown zone or an instant
before the epoch.

```python
current_loop_branch(datetime(2026, 9, 28, 12, tzinfo=ZoneInfo("America/New_York")))  # "loop/1"
current_loop_branch(datetime(2026, 10, 5, 12, tzinfo=ZoneInfo("America/New_York")))  # "loop/2"
loop_branch(3)                                                                          # "loop/3"
```

### Dry run

With `--dry-run`, every action is logged at INFO (`weekly_reset would ... action=...`) and
returned in `actions`. Nothing mutates:

- no fetch, checkout, commit, reset or push
- no branch delete or issue close
- no v4 job run (the `call_v4_weekly_reset` action shows the command it would run)
- no ledger write

Read-only Gitea calls (`list_prs`, `list_issues`) still run, so the preview is real. Steps that
are already recorded in the ledger are skipped in a dry run too.

## Signature

```python
class ResetConfig:  # frozen dataclass
    campaign: str = "ashiorid_office"; base_branch: str = "main"; seed_tag: str = "loop-seed"
    week_branch_fmt: str = "loop/{week}"; archive_branch_fmt: str = "archive/week-{week}"
    issue_label_fmt: str = "loop-{week}"; session_state_paths: tuple = ()
    repo_full_name: str | None = None; sender: str = "office_clock"  # git actor only

WEEK_BRANCH_FMT = "loop/{week}"
STEPS = ("repo_archive", "repo_reset", "repo_prune_branches", "repo_close_issues",
         "clear_session_state", "v4_weekly_reset")
RETIRED_STEPS = ("character_refresh",)
LEDGER_VERSION = 2
REFRESH_SENDER = "character-updater"
DEFAULT_EPOCH = office.clock.DEFAULT_EPOCH  # date(2026, 9, 27), week 1

def week_for(now=None, epoch=DEFAULT_EPOCH, tz="America/New_York") -> int
def loop_branch(week: int, fmt=WEEK_BRANCH_FMT) -> str
def current_loop_branch(now=None, epoch=DEFAULT_EPOCH, tz="America/New_York",
                        fmt=WEEK_BRANCH_FMT) -> str

class CommandV4Hook:
    def __init__(self, command=None, *, script=DEFAULT_V4_SCRIPT, timeout_s=1800.0,
                 runner=None, env=None, cwd=REPO_ROOT)
    def argv(self, at) -> list[str]
    def describe(self, at) -> str
    def __call__(self, week, closing_week, campaign="ashiorid_office", at=None) -> dict

class WeeklyReset:
    def __init__(self, git, gitea, ledger, config=None, v4_hook=None,
                 epoch=DEFAULT_EPOCH, tz="America/New_York", workspace=None)
    def week_for(self, at: datetime) -> int
    def run(self, at: datetime | None = None, dry_run: bool = False) -> ResetResult

class StepLedger:
    def __init__(self, path, campaign="ashiorid_office")
    def is_done(self, week, step) -> bool
    def mark_done(self, week, step, detail=None) -> None
    def mark_failed(self, week, step, error) -> None
    def record(self, week, create=False) -> dict | None

def is_protected_branch(name: str, config: ResetConfig | None = None) -> bool
def main(argv=None, git=None, gitea=None, v4_hook=None) -> int
```

## Parameters

| Name | Type | Required | Notes |
|---|---|---|---|
| `git` | `git_client.GitClient` (or a fake) | yes | Must be on the Fraud-Stop working copy that the agents commit in (the Engineer's `WORKSPACE_PATH`). With no `remote_url`, fetch and push are skipped (local-only mode). |
| `gitea` | `gitea_client.GiteaClient` (or a fake) | yes | Must point at the same repo (`gitea_admin/fraud-stop`). Needs `write:repository` and `write:issue`. |
| `ledger` | `StepLedger` or path | yes | The CLI default is `data/office/weekly_reset_ledger.json`, which is gitignored. Keep it outside the Fraud-Stop worktree, or `git clean` deletes it. |
| `config` | `ResetConfig` | no | Branch, tag and label names, plus the state paths. |
| `v4_hook` | `callable(week, closing_week, campaign, at) -> dict` | no | Defaults to `CommandV4Hook()` (the v4 job as a subprocess). Must raise to fail the step. |
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
| `--v4-command CMD` | `$OFFICE_V4_RESET_CMD` or `python services/character-updater/main.py weekly-reset` | The v4 job command (shlex-split); `--at <iso>` is appended or fills `{at}`. |
| `--v4-timeout S` | `$OFFICE_V4_RESET_TIMEOUT_S` or 1800 | Seconds before the v4 job is killed (the step fails). |
| `--bootstrap` | `$KAFKA_BOOTSTRAP_SERVERS` | Passed to the v4 job as `KAFKA_BOOTSTRAP_SERVERS`. (`--topic` is still accepted for old cron lines and ignored.) |
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

- `office.clock`: `office_time`, `segment_start`, `DEFAULT_EPOCH`, `FIRST_WEEK`
- `office.protocol`: `CLOCK_SENDER` (git actor name)
- `subprocess` / `shlex`: the v4 job command
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
from office.weekly_reset import CommandV4Hook, ResetConfig, WeeklyReset

git = GitClient("/work/fraud-stop", "office_clock")
gitea = GiteaClient("http://192.168.1.120:3300", "gitea_admin", "fraud-stop")
runner = WeeklyReset(git, gitea, "data/office/weekly_reset_ledger.json",
                     config=ResetConfig(repo_full_name="gitea_admin/fraud-stop"),
                     v4_hook=CommandV4Hook("docker compose run --rm character-jobs weekly-reset"))
result = runner.run(at=datetime(2026, 10, 4, tzinfo=ZoneInfo("America/New_York")))  # opens week 2
print(result.ok, result.ran, result.failed_step)
```

```bash
# Preview week 2's reset (Sunday 2026-10-04), then run it, then check the ledger
../.venv/bin/python -m office.weekly_reset --dry-run --at 2026-10-04T00:00-04:00 --workspace /work/fraud-stop
../.venv/bin/python -m office.weekly_reset --at 2026-10-04T00:00-04:00 --workspace /work/fraud-stop \
    --v4-command "docker compose run --rm character-jobs weekly-reset"
../.venv/bin/python -m office.weekly_reset --status --at 2026-10-04T00:00-04:00
```

## Error Handling

- Any exception in a step (`GitError`, `GiteaError`, `ResetStepError`, `OSError`, or a hook
  error) is logged at ERROR. It is recorded as `last_error`, unless the run is a dry run, and it
  stops the run. Later steps do not run.
- `ResetStepError` is raised in these cases:
  - a fetch or push fails
  - the v4 job command is missing, times out or exits with a code other than 0 / 2
  - `clear_session_state` is asked to delete a protected directory
- `LedgerError` is raised when the ledger is unreadable, is not JSON, has no `weeks`, belongs
  to another campaign, has an unknown version, or is a v1 ledger with a non-integer week key.
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
- the CLI, including the `--v4-command` / `OFFICE_V4_RESET_CMD` / `--v4-timeout` / `--bootstrap` wiring
- `CommandV4Hook`: default argv, overrides, `{at}` substitution, exit codes 0 / 2 / other,
  missing script, command not found, timeout (with a fake `runner`)
- the v1 → v2 ledger migration and a ledger with the retired `character_refresh` step
- `week_for` / `loop_branch` / `current_loop_branch` with v4 week numbers

## Changelog

- **v1.0.0** (2026-09-28): first version (OB-31).
- **v1.1.0** (2026-09-28): `current_loop_branch(now, epoch, tz)` and `WEEK_BRANCH_FMT` shared
  with `agent_handlers.office` (office PRs target `loop/<W>`); `character_refresh` now has a
  worker-side handler (`handle_character_refresh`).
- **v2.0.0** (2026-09-28): the v4 reset is authoritative. Weeks use the v4 LoopClock numbering
  (week 1 = the epoch week; first trunk `loop/1`). The office no longer publishes
  `character_refresh` (step retired, `RETIRED_STEPS`; `emit` removed from `WeeklyReset` and
  `main`); `v4_weekly_reset` runs the v4 job through `CommandV4Hook` (default `python
  services/character-updater/main.py weekly-reset --at <iso>`, `--v4-command` /
  `OFFICE_V4_RESET_CMD`, `--v4-timeout`), replacing `noop_v4_hook`. Ledger v2 with a v1
  migration (week keys + 1). New `week_for` and `loop_branch` helpers.
