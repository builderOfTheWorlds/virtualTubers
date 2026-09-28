"""Tests for office/weekly_reset.py (OB-31): full reset, ledger idempotency,
failure then resume, dry-run, branch protection, CLI, the v4 weekly-reset
command hook and the v1 -> v2 ledger migration. Weeks use the v4 LoopClock
numbering (week 1 = the epoch week)."""
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from git_client import GitError
from gitea_client import GiteaError
from office import weekly_reset as wr

NY = ZoneInfo("America/New_York")
EPOCH = date(2026, 9, 27)
# Sunday 2026-10-04 00:00 New York opens week 2; week 1 (the epoch week) closes.
RESET_AT = datetime(2026, 10, 4, 0, 0, tzinfo=NY)
RESET_AT_ISO = "2026-10-04T00:00:00-04:00"

MUTATING_GIT = {"fetch", "commit_all", "checkout", "checkout_new_branch", "reset_hard_to",
                "push_branch"}
MUTATING_GITEA = {"delete_branch", "close_issue"}


class FakeGit:
    def __init__(self, branches=("main", "loop/1"), tags=("loop-seed",), dirty=False,
                 remote_url="ssh://git@example/gitea_admin/fraud-stop.git"):
        self.repo_path = None
        self.remote_url = remote_url
        self.branches = set(branches)
        self.tags = set(tags)
        self.dirty = dirty
        self.current = "loop/1" if "loop/1" in self.branches else "main"
        self.pushed = []
        self.calls = []
        self.fail_push = set()
        self.fail_fetch = False

    def _call(self, name, *args):
        self.calls.append((name, *args))

    def fetch(self, ref=None, tags=True, prune=True):
        self._call("fetch")
        return not self.fail_fetch

    def is_dirty(self):
        self._call("is_dirty")
        return self.dirty

    def commit_all(self, message):
        self._call("commit_all", message)
        self.dirty = False
        return "wipsha"

    def checkout(self, ref):
        self._call("checkout", ref)
        if ref not in self.branches:
            raise GitError(f"git checkout {ref} failed: pathspec did not match")
        self.current = ref
        return "sha-" + ref

    def checkout_new_branch(self, name, start_point=None):
        self._call("checkout_new_branch", name, start_point)
        if name in self.branches:
            raise GitError(f"branch {name} already exists")
        if start_point and start_point not in self.branches | self.tags:
            raise GitError(f"invalid ref {start_point}")
        self.branches.add(name)
        self.current = name
        return "sha-" + name

    def reset_hard_to(self, ref):
        self._call("reset_hard_to", ref)
        if ref not in self.tags | self.branches:
            raise GitError(f"unknown ref {ref}")
        return "seedsha"

    def head(self):
        return "sha-" + self.current

    def push_branch(self, name, force=False):
        self._call("push_branch", name)
        if name in self.fail_push:
            return False
        self.pushed.append(name)
        return True

    def mutating_calls(self):
        return [c for c in self.calls if c[0] in MUTATING_GIT]


def _pr(ref, merged=True, full="gitea_admin/fraud-stop"):
    return {"merged": merged, "head": {"ref": ref, "repo": {"full_name": full}}}


class FakeGitea:
    def __init__(self, closed_prs=None, open_prs=None, issues=None):
        self.closed_prs = closed_prs if closed_prs is not None else [
            _pr("office/engineer/velocity-rule"),
            _pr("office/analyst/spec"),
            _pr("office/unmerged", merged=False),
            _pr("archive/week-0"),
            _pr("main"),
            _pr("loop-seed"),
            _pr("loop/1"),
            _pr("office/still-open"),
            _pr("feature/fork", full="someone/fraud-stop"),
        ]
        self.open_prs = open_prs if open_prs is not None else [_pr("office/still-open", merged=False)]
        self.issues = issues if issues is not None else [
            {"number": 3, "labels": [{"name": "loop-1"}]},
            {"number": 4, "labels": [{"name": "loop-1"}, {"name": "directive"}]},
            {"number": 9, "labels": [{"name": "loop-0"}]},
        ]
        self.calls = []
        self.deleted = []
        self.closed = []
        self.delete_errors = {}

    def list_prs(self, state="open"):
        self.calls.append(("list_prs", state))
        return list(self.open_prs if state == "open" else self.closed_prs)

    def list_issues(self, state="open", labels=None):
        self.calls.append(("list_issues", state, tuple(labels or ())))
        return [i for i in self.issues if i["number"] not in self.closed]

    def delete_branch(self, name):
        self.calls.append(("delete_branch", name))
        if name in self.delete_errors:
            raise self.delete_errors[name]
        self.deleted.append(name)
        return True

    def close_issue(self, number):
        self.calls.append(("close_issue", number))
        self.closed.append(number)
        return {"number": number, "state": "closed"}

    def mutating_calls(self):
        return [c for c in self.calls if c[0] in MUTATING_GITEA]


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "ws"
    state.mkdir()
    hist = state / ".aider.chat.history.md"
    hist.write_text("old chat", encoding="utf-8")
    git, gitea = FakeGit(), FakeGitea()
    v4_calls = []
    config = wr.ResetConfig(session_state_paths=(str(hist), str(state / ".aider.input.history")),
                            repo_full_name="gitea_admin/fraud-stop")
    ledger_path = tmp_path / "ledger.json"

    def make(**kw):
        return wr.WeeklyReset(kw.get("git", git), kw.get("gitea", gitea), str(ledger_path),
                              config=kw.get("config", config),
                              v4_hook=kw.get("v4_hook", lambda w, c, camp, at: v4_calls.append((w, c, camp, at)) or {"ok": 1}),
                              epoch=EPOCH, workspace=str(state))

    return {"git": git, "gitea": gitea, "v4": v4_calls, "make": make,
            "ledger": ledger_path, "hist": hist, "config": config}


def _ledger(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ── full reset ───────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_full_reset_runs_every_step_in_order(env):
    result = env["make"]().run(at=RESET_AT)
    assert result.ok and result.week == 2 and result.closing_week == 1
    assert result.ran == list(wr.STEPS)
    git, gitea = env["git"], env["gitea"]
    assert "archive/week-1" in git.branches and "loop/2" in git.branches
    assert git.pushed == ["archive/week-1", "loop/2"]
    assert ("checkout_new_branch", "loop/2", "loop-seed") in git.calls
    assert ("reset_hard_to", "loop-seed") in git.calls
    assert sorted(gitea.deleted) == ["office/analyst/spec", "office/engineer/velocity-rule"]
    assert gitea.closed == [3, 4]
    assert not env["hist"].exists()
    # The v4 job gets the run instant; the office itself publishes nothing.
    assert env["v4"] == [(2, 1, "ashiorid_office", RESET_AT)]
    assert wr.STEP_REFRESH_RETIRED not in wr.STEPS
    record = _ledger(env["ledger"])["weeks"]["2"]
    assert set(record["steps"]) == set(wr.STEPS)
    assert _ledger(env["ledger"])["version"] == wr.LEDGER_VERSION
    assert record["completed_at"] and record["last_error"] is None


@pytest.mark.unit
def test_archive_created_from_week_branch_before_reset(env):
    env["make"]().run(at=RESET_AT)
    names = [c for c in env["git"].calls if c[0] in ("checkout", "checkout_new_branch")]
    assert names[:3] == [("checkout", "archive/week-1"), ("checkout", "loop/1"),
                         ("checkout_new_branch", "archive/week-1", None)]


@pytest.mark.unit
def test_archive_falls_back_to_base_when_week_branch_missing(env):
    git = FakeGit(branches=("main",))
    result = env["make"](git=git).run(at=RESET_AT)
    assert result.ok
    assert result.details[wr.STEP_ARCHIVE]["source"] == "main"
    assert "archive/week-1" in git.branches


@pytest.mark.unit
def test_archive_commits_dirty_worktree_first(env):
    git = FakeGit(dirty=True)
    result = env["make"](git=git).run(at=RESET_AT)
    assert result.details[wr.STEP_ARCHIVE]["wip_commit"] == "wipsha"
    first_mutation = git.mutating_calls()[1]  # after fetch
    assert first_mutation[0] == "commit_all"


@pytest.mark.unit
def test_first_week_skips_archive_and_issue_close(env):
    at = datetime(2026, 9, 27, 0, 0, tzinfo=NY)
    git = FakeGit(branches=("main",))
    result = env["make"](git=git).run(at=at)
    assert result.ok and result.week == 1 and result.closing_week == 0
    assert result.details[wr.STEP_ARCHIVE] == {"skipped": "no previous week"}
    assert result.details[wr.STEP_CLOSE_ISSUES] == {"skipped": "no previous week"}
    assert env["gitea"].closed == []
    assert result.details[wr.STEP_RESET]["branch"] == "loop/1" and "loop/1" in git.branches
    assert "loop/0" not in git.branches


@pytest.mark.unit
@pytest.mark.parametrize("at, week", [
    (datetime(2026, 9, 27, 0, 0, tzinfo=NY), 1),               # the epoch: week 1 (v4)
    (datetime(2026, 10, 4, 0, 0, tzinfo=NY), 2),
    (datetime(2026, 10, 4, 3, 59, tzinfo=timezone.utc), 1),   # Sat 23:59 NY
    (datetime(2026, 10, 7, 12, 0, tzinfo=NY), 2),             # mid-week catch-up
    (datetime(2026, 11, 1, 0, 0, tzinfo=NY), 6),               # DST fall-back Sunday
])
def test_week_for_uses_office_clock(env, at, week):
    assert env["make"]().week_for(at) == week
    assert wr.week_for(at) == week
    assert wr.current_loop_branch(at) == f"loop/{week}"


@pytest.mark.unit
@pytest.mark.parametrize("week", [0, -1, 1.0, True, None, "2"])
def test_loop_branch_rejects_non_v4_weeks(week):
    with pytest.raises(ValueError):
        wr.loop_branch(week)


@pytest.mark.unit
def test_loop_branch_formats_the_week_trunk():
    assert wr.loop_branch(1) == "loop/1" and wr.loop_branch(12, "trunk-{week}") == "trunk-12"


# ── ledger idempotency ───────────────────────────────────────────────────────
@pytest.mark.unit
def test_rerun_skips_all_done_steps(env):
    env["make"]().run(at=RESET_AT)
    git_calls, gitea_calls = len(env["git"].calls), len(env["gitea"].calls)
    second = env["make"]().run(at=RESET_AT + timedelta(hours=5))
    assert second.ok and second.ran == [] and second.skipped == list(wr.STEPS)
    assert len(env["git"].calls) == git_calls and len(env["gitea"].calls) == gitea_calls
    assert len(env["v4"]) == 1


@pytest.mark.unit
@pytest.mark.parametrize("step", wr.STEPS)
def test_each_done_step_is_skipped(env, step):
    ledger = wr.StepLedger(env["ledger"])
    ledger.mark_done(2, step, {"pre": True})
    result = env["make"]().run(at=RESET_AT)
    assert result.ok and result.skipped == [step]
    assert step not in result.ran and len(result.ran) == len(wr.STEPS) - 1
    assert _ledger(env["ledger"])["weeks"]["2"]["steps"][step]["detail"] == {"pre": True}


@pytest.mark.unit
def test_ledger_is_per_week(env):
    env["make"]().run(at=RESET_AT)
    result = env["make"]().run(at=RESET_AT + timedelta(days=7))
    assert result.week == 3 and result.ran == list(wr.STEPS)
    assert set(_ledger(env["ledger"])["weeks"]) == {"2", "3"}


@pytest.mark.unit
def test_corrupt_ledger_raises_not_treated_as_empty(env):
    env["ledger"].write_text("{not json", encoding="utf-8")
    with pytest.raises(wr.LedgerError):
        wr.StepLedger(env["ledger"])


@pytest.mark.unit
def test_ledger_campaign_mismatch_raises(env):
    env["ledger"].write_text(json.dumps({"campaign": "hp", "weeks": {}}), encoding="utf-8")
    with pytest.raises(wr.LedgerError):
        wr.StepLedger(env["ledger"])


# ── failure then resume ──────────────────────────────────────────────────────
@pytest.mark.unit
def test_failed_push_stops_run_and_resume_retries_that_step(env):
    git = env["git"]
    git.fail_push = {"loop/2"}
    first = env["make"]().run(at=RESET_AT)
    assert not first.ok and first.failed_step == wr.STEP_RESET
    assert first.ran == [wr.STEP_ARCHIVE]
    assert env["gitea"].mutating_calls() == [] and env["v4"] == []
    record = _ledger(env["ledger"])["weeks"]["2"]
    assert list(record["steps"]) == [wr.STEP_ARCHIVE]
    assert record["last_error"]["step"] == wr.STEP_RESET and record["completed_at"] is None

    git.fail_push = set()
    second = env["make"]().run(at=RESET_AT)
    assert second.ok and second.skipped == [wr.STEP_ARCHIVE]
    assert second.ran == list(wr.STEPS[1:])
    assert second.details[wr.STEP_RESET]["already_existed"] is True
    assert git.pushed.count("archive/week-1") == 1
    assert _ledger(env["ledger"])["weeks"]["2"]["last_error"] is None


@pytest.mark.unit
def test_failed_gitea_delete_stops_before_later_steps(env):
    gitea = env["gitea"]
    gitea.delete_errors["office/analyst/spec"] = GiteaError(500, "boom", "DELETE", "/x")
    first = env["make"]().run(at=RESET_AT)
    assert first.failed_step == wr.STEP_PRUNE and "boom" in first.error
    assert gitea.closed == [] and env["v4"] == []
    del gitea.delete_errors["office/analyst/spec"]
    second = env["make"]().run(at=RESET_AT)
    assert second.ok and second.ran[0] == wr.STEP_PRUNE


@pytest.mark.unit
def test_already_deleted_branch_is_not_a_failure(env):
    env["gitea"].delete_errors["office/analyst/spec"] = GiteaError(404, "not found", "DELETE", "/x")
    result = env["make"]().run(at=RESET_AT)
    assert result.ok
    assert result.details[wr.STEP_PRUNE]["already_gone"] == ["office/analyst/spec"]


@pytest.mark.unit
def test_v4_hook_error_is_recorded_and_retried(env):
    def broken(week, closing, campaign, at):
        raise RuntimeError("v4 down")
    result = env["make"](v4_hook=broken).run(at=RESET_AT)
    assert result.failed_step == wr.STEP_V4
    assert _ledger(env["ledger"])["weeks"]["2"]["last_error"]["error"].endswith("v4 down")
    assert _ledger(env["ledger"])["weeks"]["2"]["completed_at"] is None
    second = env["make"]().run(at=RESET_AT)
    assert second.ok and second.ran == [wr.STEP_V4] and len(env["v4"]) == 1


@pytest.mark.unit
def test_default_v4_hook_is_the_command_hook(env, tmp_path):
    runner = wr.WeeklyReset(env["git"], env["gitea"], str(tmp_path / "l.json"),
                            config=env["config"], epoch=EPOCH)
    assert isinstance(runner.v4_hook, wr.CommandV4Hook)
    assert runner.v4_hook.argv(RESET_AT) == [
        sys.executable, str(wr.DEFAULT_V4_SCRIPT), "weekly-reset", "--at", RESET_AT.isoformat()]


# ── the v4 weekly-reset command hook ────────────────────────────────────────
class FakeRun:
    """subprocess.run stand-in: records argv/kwargs, returns `code`."""

    def __init__(self, code=0, stderr="", exc=None):
        self.code, self.stderr, self.exc = code, stderr, exc
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.code, stdout="", stderr=self.stderr)


@pytest.fixture
def v4_script(tmp_path):
    script = tmp_path / "services" / "character-updater" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fake v4 entry point\n", encoding="utf-8")
    return script


@pytest.mark.unit
@pytest.mark.parametrize("code, status", [(0, "done"), (2, "nothing_to_do")])
def test_command_hook_runs_default_command_with_at(v4_script, code, status):
    run = FakeRun(code=code)
    hook = wr.CommandV4Hook(script=v4_script, runner=run, timeout_s=5)
    detail = hook(2, 1, "ashiorid_office", RESET_AT)
    [(argv, kwargs)] = run.calls
    assert argv == [sys.executable, str(v4_script), "weekly-reset", "--at", RESET_AT.isoformat()]
    assert kwargs["timeout"] == 5 and kwargs["capture_output"] is True
    assert detail["exit_code"] == code and detail["status"] == status
    assert "weekly-reset --at" in detail["command"]


@pytest.mark.unit
@pytest.mark.parametrize("command, expected", [
    ("docker compose run --rm character-jobs weekly-reset",
     ["docker", "compose", "run", "--rm", "character-jobs", "weekly-reset", "--at", RESET_AT_ISO]),
    (["jobs", "weekly-reset", "--at={at}", "--force"],
     ["jobs", "weekly-reset", f"--at={RESET_AT_ISO}", "--force"]),
])
def test_command_hook_override(command, expected, tmp_path):
    run = FakeRun()
    hook = wr.CommandV4Hook(command, script=tmp_path / "missing.py", runner=run)
    hook(2, 1, "ashiorid_office", RESET_AT)
    assert run.calls[0][0] == expected


@pytest.mark.unit
def test_command_hook_missing_default_script_fails_without_running(tmp_path):
    run = FakeRun()
    hook = wr.CommandV4Hook(script=tmp_path / "nope" / "main.py", runner=run)
    with pytest.raises(wr.ResetStepError, match="not installed"):
        hook(2, 1, "ashiorid_office", RESET_AT)
    assert run.calls == []


@pytest.mark.unit
@pytest.mark.parametrize("run, match", [
    (FakeRun(code=1, stderr="Traceback: boom"), "exited 1: Traceback: boom"),
    (FakeRun(code=3), "exited 3"),
    (FakeRun(exc=FileNotFoundError("docker")), "command not found: docker"),
    (FakeRun(exc=subprocess.TimeoutExpired("docker", 5)), "timed out"),
])
def test_command_hook_failures_raise_reset_step_error(run, match):
    hook = wr.CommandV4Hook("docker compose run --rm character-jobs weekly-reset", runner=run)
    with pytest.raises(wr.ResetStepError, match=match):
        hook(2, 1, "ashiorid_office", RESET_AT)


@pytest.mark.unit
def test_failing_v4_command_fails_the_step_and_is_retried(env, v4_script):
    run = FakeRun(code=1, stderr="db down")
    hook = wr.CommandV4Hook(script=v4_script, runner=run)
    first = env["make"](v4_hook=hook).run(at=RESET_AT)
    assert first.failed_step == wr.STEP_V4 and "db down" in first.error
    run.code = 0
    second = env["make"](v4_hook=hook).run(at=RESET_AT)
    assert second.ok and second.ran == [wr.STEP_V4]
    assert second.details[wr.STEP_V4]["exit_code"] == 0 and len(run.calls) == 2


# ── ledger backward compatibility ────────────────────────────────────────────
@pytest.mark.unit
def test_v1_ledger_with_retired_refresh_step_is_migrated_and_completes(env):
    """A v1 ledger (0-based weeks, with the retired office character_refresh
    step recorded) loads: week 1 (old numbering) is week 2 now, and only the
    steps still missing run."""
    done = {"completed_at": "2026-10-04T04:00:00+00:00", "detail": {}}
    old = {"version": 1, "campaign": "ashiorid_office", "weeks": {"1": {
        "week": 1, "steps": {s: done for s in (wr.STEP_ARCHIVE, wr.STEP_RESET, wr.STEP_PRUNE,
                                               wr.STEP_CLOSE_ISSUES, wr.STEP_CLEAR_STATE,
                                               wr.STEP_REFRESH_RETIRED)},
        "last_error": {"step": wr.STEP_V4, "error": "stub"}, "completed_at": None}}}
    env["ledger"].write_text(json.dumps(old), encoding="utf-8")
    result = env["make"]().run(at=RESET_AT)
    assert result.ok and result.week == 2 and result.ran == [wr.STEP_V4]
    data = _ledger(env["ledger"])
    assert data["version"] == wr.LEDGER_VERSION and set(data["weeks"]) == {"2"}
    record = data["weeks"]["2"]
    assert record["week"] == 2 and record["v1_week"] == 1 and record["completed_at"]
    assert wr.STEP_REFRESH_RETIRED in record["steps"]      # kept, never counted
    assert env["git"].mutating_calls() == []


@pytest.mark.unit
def test_v2_ledger_with_retired_step_recorded_is_not_rerun(env):
    ledger = wr.StepLedger(env["ledger"])
    ledger.mark_done(2, wr.STEP_REFRESH_RETIRED, {"message_id": "old"})
    for step in wr.STEPS:
        ledger.mark_done(2, step)
    result = env["make"]().run(at=RESET_AT)
    assert result.ok and result.ran == [] and result.skipped == list(wr.STEPS)


@pytest.mark.unit
@pytest.mark.parametrize("data", [
    {"version": 7, "weeks": {}},
    {"version": 1, "weeks": {"week-one": {}}},
])
def test_unknown_ledger_version_or_bad_v1_key_raises(env, data):
    env["ledger"].write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(wr.LedgerError):
        wr.StepLedger(env["ledger"])


@pytest.mark.unit
def test_missing_seed_tag_fails_reset(env):
    git = FakeGit(tags=())
    result = env["make"](git=git).run(at=RESET_AT)
    assert result.failed_step == wr.STEP_RESET


# ── dry-run ──────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_dry_run_makes_no_mutating_calls_and_writes_nothing(env):
    result = env["make"]().run(at=RESET_AT, dry_run=True)
    assert result.ok and result.dry_run and result.ran == list(wr.STEPS)
    assert env["git"].calls == []
    assert env["gitea"].mutating_calls() == []
    assert env["v4"] == []
    assert env["hist"].exists()
    assert not env["ledger"].exists()
    actions = {(a["step"], a["action"]) for a in result.actions}
    assert (wr.STEP_ARCHIVE, "create_branch") in actions
    assert (wr.STEP_RESET, "reset_hard") in actions
    assert (wr.STEP_V4, "call_v4_weekly_reset") in actions
    deletes = sorted(a["branch"] for a in result.actions if a["action"] == "delete_branch")
    assert deletes == ["office/analyst/spec", "office/engineer/velocity-rule"]
    closes = [a["number"] for a in result.actions if a["action"] == "close_issue"]
    assert closes == [3, 4]


@pytest.mark.unit
def test_dry_run_after_partial_run_only_previews_remaining(env):
    wr.StepLedger(env["ledger"]).mark_done(2, wr.STEP_ARCHIVE)
    before = env["ledger"].read_text(encoding="utf-8")
    result = env["make"]().run(at=RESET_AT, dry_run=True)
    assert result.skipped == [wr.STEP_ARCHIVE]
    assert env["ledger"].read_text(encoding="utf-8") == before


# ── branch protection ────────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("name, protected", [
    ("main", True), ("refs/heads/main", True), ("loop-seed", True),
    ("archive/week-3", True), ("archive/anything", True), ("loop/2", True), ("loop/10", True),
    ("", True), ("HEAD", True),
    ("office/engineer/x", False), ("feature/loop", False), ("mainline", False),
    ("archived", False), ("loop-2", False),
])
def test_is_protected_branch(name, protected):
    assert wr.is_protected_branch(name) is protected


@pytest.mark.unit
def test_custom_base_branch_is_protected():
    cfg = wr.ResetConfig(base_branch="trunk")
    assert wr.is_protected_branch("trunk", cfg) and not wr.is_protected_branch("main", cfg)


@pytest.mark.unit
def test_prune_never_deletes_protected_open_unmerged_or_foreign(env):
    result = env["make"]().run(at=RESET_AT)
    deleted = set(env["gitea"].deleted)
    for name in ("main", "loop-seed", "archive/week-0", "loop/1", "office/unmerged",
                 "office/still-open", "feature/fork"):
        assert name not in deleted
    detail = result.details[wr.STEP_PRUNE]
    assert "office/still-open" in detail["kept"] and "archive/week-0" in detail["kept"]
    assert detail["foreign"] == ["feature/fork"]


@pytest.mark.unit
def test_close_issues_only_touches_labelled_issues(env):
    env["make"]().run(at=RESET_AT)
    assert ("list_issues", "open", ("loop-1",)) in env["gitea"].calls
    assert 9 not in env["gitea"].closed


# ── session state ────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_clear_state_refuses_workspace_and_git_dirs(env, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    cfg = wr.ResetConfig(session_state_paths=(str(repo),))
    result = env["make"](config=cfg).run(at=RESET_AT)
    assert result.failed_step == wr.STEP_CLEAR_STATE and repo.exists()


@pytest.mark.unit
def test_clear_state_removes_directories(env, tmp_path):
    cache = tmp_path / "sessions"
    (cache / "a").mkdir(parents=True)
    (cache / "a" / "s.json").write_text("{}", encoding="utf-8")
    cfg = wr.ResetConfig(session_state_paths=(str(cache), str(tmp_path / "nope")))
    result = env["make"](config=cfg).run(at=RESET_AT)
    assert result.ok and not cache.exists()
    assert result.details[wr.STEP_CLEAR_STATE]["paths"][str(tmp_path / "nope")] == "missing"


# ── CLI ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_cli_dry_run_with_at(env, capsys, tmp_path):
    ledger = tmp_path / "cli_ledger.json"
    code = wr.main(["--dry-run", "--at", RESET_AT_ISO, "--epoch", "2026-09-27",
                    "--ledger", str(ledger), "--workspace", str(tmp_path)],
                   git=env["git"], gitea=env["gitea"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["week"] == 2 and out["dry_run"] is True
    assert env["git"].calls == [] and not ledger.exists()


@pytest.mark.unit
def test_cli_real_run_then_status(env, capsys, tmp_path):
    ledger = tmp_path / "cli_ledger.json"
    argv = ["--at", RESET_AT_ISO, "--epoch", "2026-09-27",
            "--ledger", str(ledger), "--workspace", str(tmp_path)]
    assert wr.main(argv, git=env["git"], gitea=env["gitea"], v4_hook=lambda *a: {}) == 0
    capsys.readouterr()
    assert wr.main(argv + ["--status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["week"] == 2 and set(status["record"]["steps"]) == set(wr.STEPS)


@pytest.mark.unit
def test_cli_failure_exit_code(env, tmp_path):
    env["git"].fail_fetch = True
    code = wr.main(["--at", RESET_AT_ISO, "--epoch", "2026-09-27",
                    "--ledger", str(tmp_path / "l.json"), "--workspace", str(tmp_path)],
                   git=env["git"], gitea=env["gitea"], v4_hook=lambda *a: {})
    assert code == 1


@pytest.mark.unit
def test_cli_builds_the_v4_command_hook_from_env_and_flags(env, tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_V4_RESET_CMD", "docker compose run --rm character-jobs weekly-reset")
    captured = {}
    real = wr.CommandV4Hook

    def spy(command=None, **kwargs):
        hook = real(command, runner=FakeRun(), **kwargs)
        captured["hook"] = hook
        return hook
    monkeypatch.setattr(wr, "CommandV4Hook", spy)
    code = wr.main(["--at", RESET_AT_ISO, "--ledger", str(tmp_path / "l.json"),
                    "--workspace", str(tmp_path), "--v4-timeout", "42", "--bootstrap", "kafka:9092"],
                   git=env["git"], gitea=env["gitea"])
    hook = captured["hook"]
    assert code == 0 and hook.timeout_s == 42.0 and hook.env["KAFKA_BOOTSTRAP_SERVERS"] == "kafka:9092"
    [(argv, _)] = hook.runner.calls
    assert argv == ["docker", "compose", "run", "--rm", "character-jobs", "weekly-reset",
                    "--at", RESET_AT_ISO]


@pytest.mark.unit
def test_cli_dry_run_previews_the_v4_command(env, capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("OFFICE_V4_RESET_CMD", raising=False)
    code = wr.main(["--dry-run", "--at", RESET_AT_ISO, "--ledger", str(tmp_path / "l.json"),
                    "--workspace", str(tmp_path)], git=env["git"], gitea=env["gitea"])
    out = json.loads(capsys.readouterr().out)
    [action] = [a for a in out["actions"] if a["step"] == wr.STEP_V4]
    assert code == 0 and action["command"].endswith(f"weekly-reset --at {RESET_AT_ISO}")


@pytest.mark.unit
def test_cli_rejects_naive_at():
    with pytest.raises(SystemExit):
        wr.build_parser().parse_args(["--at", "2026-10-11T00:00:00"])


@pytest.mark.unit
def test_cli_without_workspace_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKSPACE_PATH", raising=False)
    assert wr.main(["--ledger", str(tmp_path / "l.json"), "--at", RESET_AT_ISO]) == 2


@pytest.mark.unit
def test_token_never_logged(env, caplog, monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN_OFFICE", "supersecret-token-value")
    caplog.set_level(5)
    env["make"]().run(at=RESET_AT)
    assert "supersecret-token-value" not in caplog.text
