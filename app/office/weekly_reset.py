"""
office/weekly_reset.py
Sunday 00:00 (America/New_York) weekly loop reset, office side (OB-31).

When week W opens, the week that just ended is N = W - 1. The reset runs these
steps in a fixed order:

  1. Fraud-Stop repo
     - repo_archive: keep week N's branch `loop/<N>` as `archive/week-<N>`
     - repo_reset: fresh branch `loop/<W>`, hard reset to the `loop-seed` tag
     - repo_prune_branches: delete branches of merged PRs, except protected
       ones (the base branch, `loop-seed`, `archive/*`, `loop/*`)
     - repo_close_issues: close open issues labelled `loop-<N>`
  2. clear_session_state: delete the agents' on-disk session state (aider chat
     and input history in the workspace by default)
  3. v4_weekly_reset: run the v4 `weekly-reset` job (CommandV4Hook: by
     default `python services/character-updater/main.py weekly-reset --at
     <iso>`, overridable with --v4-command / OFFICE_V4_RESET_CMD). The v4 job
     is authoritative for the characters: it archives week N and publishes
     the ONLY `character_refresh` (from "character-updater", payload
     {campaign, week, characters, reason}); the office no longer sends one.

Weeks use the v4 LoopClock numbering (office.clock): week 1 starts at the
epoch Sunday, so the first week's trunk is `loop/1` and week 1 has no
previous week to archive.

Idempotency comes from a JSON step ledger, keyed by week W, which mirrors v4
`loop_weeks.reset_steps`. The retired office `character_refresh` step
(ledger v1) may still be recorded in old ledgers; it is ignored. A v1 ledger
(old 0-based week numbers) is migrated on load (every week key + 1). Each finished step is recorded with a timestamp. A
re-run skips finished steps. A failed step stops the run and records the
error; the next run retries from that step. With dry_run nothing mutates:
reads (list PRs, list issues) still run, and every would-be action is logged
and returned.

The git and Gitea clients and the v4 hook are injected. Nothing here logs a token: the clients read theirs from the
environment by name. CLI: `python -m office.weekly_reset --help` (run from
app/) or `python app/office/weekly_reset.py --help`. See
docs/office_weekly_reset.md.
"""
import argparse
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    # Run as a script (python app/office/weekly_reset.py): put app/ on the
    # path so `office.*` and the bare app modules import.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from git_client import GitError  # noqa: E402
from gitea_client import GiteaError  # noqa: E402
from office.clock import (  # noqa: E402
    DEFAULT_EPOCH,
    DEFAULT_TZ,
    FIRST_WEEK,
    office_time,
    segment_start,
)
from office.protocol import CLOCK_SENDER  # noqa: E402
from relay_io import atomic_write_json  # noqa: E402

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

CAMPAIGN = "ashiorid_office"
CHARACTER_REFRESH = "character_refresh"
REFRESH_REASON = "weekly_reset"
#: The only character_refresh publisher (the v4 weekly-reset job, plan §3.2).
REFRESH_SENDER = "character-updater"

STEP_ARCHIVE = "repo_archive"
STEP_RESET = "repo_reset"
STEP_PRUNE = "repo_prune_branches"
STEP_CLOSE_ISSUES = "repo_close_issues"
STEP_CLEAR_STATE = "clear_session_state"
STEP_V4 = "v4_weekly_reset"
STEPS = (STEP_ARCHIVE, STEP_RESET, STEP_PRUNE, STEP_CLOSE_ISSUES,
         STEP_CLEAR_STATE, STEP_V4)
#: Steps an older office wrote to the ledger and no longer runs. A ledger
#: that recorded them still loads; they never count towards completion.
STEP_REFRESH_RETIRED = "character_refresh"
RETIRED_STEPS = (STEP_REFRESH_RETIRED,)

#: v2: v4 week numbering (week 1 = the epoch week). v1 ledgers used 0-based
#: weeks and are migrated on load.
LEDGER_VERSION = 2
# DEFAULT_EPOCH (re-exported from office.clock): campaign start Sunday;
# override with OFFICE_EPOCH.
REPO_ROOT = Path(__file__).resolve().parents[2]
#: The v4 job entry point (WP-20), relative to the repo root.
DEFAULT_V4_SCRIPT = REPO_ROOT / "services" / "character-updater" / "main.py"
DEFAULT_V4_TIMEOUT_S = 1800.0
#: v4 job exit codes that mean the week is reset (plan §5): 0 done,
#: 2 nothing to do (already done, locked by a running copy, or not due).
V4_OK_EXIT_CODES = (0, 2)
DEFAULT_LEDGER = Path(__file__).resolve().parents[2] / "data" / "office" / "weekly_reset_ledger.json"
DEFAULT_GITEA_BASE_URL = "http://192.168.1.120:3300"
DEFAULT_GITEA_OWNER = "gitea_admin"
DEFAULT_GITEA_REPO = "fraud-stop"
DEFAULT_TOPIC = "vtuber.messages"
#: Aider writes these into the workspace root; `git clean -fd` keeps them
#: because aider adds `.aider*` to .gitignore, so the reset removes them here.
DEFAULT_WORKSPACE_STATE_FILES = (".aider.chat.history.md", ".aider.input.history")
#: The week trunk every office PR targets (ResetConfig.week_branch_fmt default).
WEEK_BRANCH_FMT = "loop/{week}"


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class LedgerError(RuntimeError):
    """The ledger file exists but cannot be read or parsed. Never treated as
    empty: that would repeat resets that already happened."""


class ResetStepError(RuntimeError):
    """A reset step could not complete (push refused, no bus, ...)."""


# ── config and branch protection ─────────────────────────────────────────────
@dataclass(frozen=True)
class ResetConfig:
    campaign: str = CAMPAIGN
    base_branch: str = "main"
    seed_tag: str = "loop-seed"
    week_branch_fmt: str = WEEK_BRANCH_FMT
    archive_branch_fmt: str = "archive/week-{week}"
    issue_label_fmt: str = "loop-{week}"
    #: files or directories removed by clear_session_state
    session_state_paths: tuple = ()
    #: "owner/repo"; when set, PR heads from other repos (forks) are ignored
    repo_full_name: str = None
    #: git author / actor name of the reset (not a bus sender any more)
    sender: str = CLOCK_SENDER

    def week_branch(self, week):
        return self.week_branch_fmt.format(week=week)

    def archive_branch(self, week):
        return self.archive_branch_fmt.format(week=week)

    def issue_label(self, week):
        return self.issue_label_fmt.format(week=week)

    def protected_prefixes(self):
        """`archive/` and `loop/`: each name format up to its last `/` before
        `{week}` (the whole namespace, so any `archive/*` is kept)."""
        prefixes = []
        for fmt in (self.archive_branch_fmt, self.week_branch_fmt):
            prefix = fmt.split("{", 1)[0]
            if "/" in prefix:
                prefix = prefix[: prefix.rindex("/") + 1]
            if prefix:
                prefixes.append(prefix)
        return tuple(prefixes)


def _short_ref(name):
    name = (name or "").strip()
    for prefix in ("refs/heads/", "refs/tags/"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def is_protected_branch(name, config=None):
    """True for branches the reset must never delete: empty/HEAD, the base
    branch, the seed tag name, `archive/*` and the week trunks `loop/*`."""
    config = config or ResetConfig()
    short = _short_ref(name)
    if not short or short == "HEAD":
        return True
    if short in (config.base_branch, config.seed_tag):
        return True
    return short.startswith(config.protected_prefixes())


def week_for(now=None, epoch=DEFAULT_EPOCH, tz=DEFAULT_TZ):
    """The office loop week (v4 numbering, 1 = the epoch week) containing
    `now` (tz-aware; default the current UTC instant). `epoch` may be a date
    or a YYYY-MM-DD string. Raises ValueError for a bad epoch/tz, a naive
    `now` or an instant before the epoch (office.clock.office_time)."""
    now = now or datetime.now(timezone.utc)
    if isinstance(epoch, str):
        epoch = date.fromisoformat(epoch)
    return office_time(now, epoch, tz).loop_week


def loop_branch(week, fmt=WEEK_BRANCH_FMT):
    """The week trunk name for loop week `week` (`loop/<W>`). Raises
    ValueError unless `week` is an int >= 1 (the v4 week numbering)."""
    if isinstance(week, bool) or not isinstance(week, int) or week < FIRST_WEEK:
        raise ValueError(f"loop week must be an int >= {FIRST_WEEK}, got {week!r}")
    return fmt.format(week=week)


def current_loop_branch(now=None, epoch=DEFAULT_EPOCH, tz=DEFAULT_TZ, fmt=WEEK_BRANCH_FMT):
    """The week trunk (`loop/<W>`) for the office week containing `now`
    (week_for + loop_branch; the same week arithmetic as WeeklyReset).
    Raises ValueError like week_for."""
    now = now or datetime.now(timezone.utc)
    week = week_for(now, epoch, tz)
    branch = loop_branch(week, fmt)
    log.debug("weekly_reset current_loop_branch now=%s week=%s branch=%s", now, week, branch)
    return branch


# ── step ledger ──────────────────────────────────────────────────────────────
class StepLedger:
    """JSON file: {"version", "campaign", "weeks": {"<W>": record}}. A record
    holds `steps` (step -> {completed_at, detail}), `last_error` and
    `completed_at` (set once every step is done). Written atomically."""

    def __init__(self, path, campaign=CAMPAIGN):
        self.path = Path(path)
        self.campaign = campaign
        self.data = self._load()

    def _load(self):
        _trace("ledger load enter path=%s", self.path)
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.debug("ledger missing, starting empty path=%s", self.path)
            return {"version": LEDGER_VERSION, "campaign": self.campaign, "weeks": {}}
        except (OSError, ValueError) as exc:
            log.error("ledger unreadable path=%s error=%s", self.path, exc)
            raise LedgerError(f"cannot read ledger {self.path}: {exc}") from exc
        try:
            data = json.loads(text)
        except ValueError as exc:
            log.error("ledger corrupt path=%s error=%s", self.path, exc)
            raise LedgerError(f"ledger {self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("weeks"), dict):
            log.error("ledger malformed path=%s", self.path)
            raise LedgerError(f"ledger {self.path} has no 'weeks' object")
        if data.get("campaign") not in (None, self.campaign):
            log.error("ledger campaign mismatch path=%s ledger=%s expected=%s",
                      self.path, data.get("campaign"), self.campaign)
            raise LedgerError(f"ledger {self.path} belongs to campaign {data.get('campaign')!r}")
        return self._migrate(data)

    def _migrate(self, data):
        """v1 -> v2: v1 counted weeks from 0 at the epoch; v2 uses the v4
        numbering (week 1 at the epoch), so every week key moves up by one.
        In memory only; the next save writes v2."""
        version = data.get("version", 1)
        if version == LEDGER_VERSION:
            return data
        if version != 1:
            log.error("ledger unknown version path=%s version=%r", self.path, version)
            raise LedgerError(f"ledger {self.path} has unknown version {version!r}")
        weeks = {}
        for key, rec in data["weeks"].items():
            try:
                old = int(key)
            except (TypeError, ValueError) as exc:
                log.error("ledger bad week key path=%s key=%r", self.path, key)
                raise LedgerError(f"ledger {self.path} has a bad week key {key!r}") from exc
            rec = dict(rec) if isinstance(rec, dict) else {"steps": {}}
            rec.update(week=old + FIRST_WEEK, v1_week=old)
            weeks[str(old + FIRST_WEEK)] = rec
        log.warning("ledger migrated v1 -> v%s path=%s weeks=%s (week numbers + %s)",
                    LEDGER_VERSION, self.path, sorted(weeks, key=int), FIRST_WEEK)
        return {**data, "version": LEDGER_VERSION, "weeks": weeks}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        log.debug("ledger write path=%s", self.path)
        atomic_write_json(str(self.path), self.data, fsync=True)

    def record(self, week, create=False):
        rec = self.data["weeks"].get(str(week))
        if rec is None and create:
            rec = {"week": week, "steps": {}, "last_error": None, "completed_at": None}
            self.data["weeks"][str(week)] = rec
        return rec

    def is_done(self, week, step):
        rec = self.record(week)
        return bool(rec and step in rec.get("steps", {}))

    def mark_done(self, week, step, detail=None):
        rec = self.record(week, create=True)
        rec["steps"][step] = {"completed_at": _now_iso(), "detail": detail}
        rec["last_error"] = None
        if all(s in rec["steps"] for s in STEPS):
            rec["completed_at"] = rec["steps"][step]["completed_at"]
        self.save()

    def mark_failed(self, week, step, error):
        rec = self.record(week, create=True)
        rec["last_error"] = {"step": step, "error": str(error)[:500], "at": _now_iso()}
        self.save()


# ── result ───────────────────────────────────────────────────────────────────
@dataclass
class ResetResult:
    week: int
    closing_week: int
    dry_run: bool
    run_id: str
    ran: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed_step: str = None
    error: str = None
    actions: list = field(default_factory=list)
    details: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.failed_step is None

    def as_dict(self):
        return {"week": self.week, "closing_week": self.closing_week, "dry_run": self.dry_run,
                "run_id": self.run_id, "ok": self.ok, "ran": self.ran, "skipped": self.skipped,
                "failed_step": self.failed_step, "error": self.error, "actions": self.actions,
                "details": self.details}


def _iso(at):
    return at.isoformat() if isinstance(at, datetime) else str(at)


class CommandV4Hook:
    """The v4 `weekly-reset` job as a subprocess (the office's v4 hook).

    command: None -> [sys.executable, services/character-updater/main.py,
    "weekly-reset"]; a string (shlex-split) or a list overrides it, e.g.
    "docker compose run --rm character-jobs weekly-reset". The run's instant
    is passed as `--at <iso>`, or substituted for a literal `{at}` token.
    Exit 0 (done) and 2 (nothing to do: already done, locked, not due) pass;
    anything else, a missing command/script or a timeout raises
    ResetStepError, so the step fails and the ledger retries it next run.
    `runner` (subprocess.run) and `env` are injectable for tests."""

    def __init__(self, command=None, *, script=DEFAULT_V4_SCRIPT, timeout_s=DEFAULT_V4_TIMEOUT_S,
                 runner=None, env=None, cwd=REPO_ROOT):
        if isinstance(command, str):
            command = shlex.split(command)
        self.command = list(command) if command else None
        self.script = Path(script)
        self.timeout_s = float(timeout_s)
        self.runner = runner or subprocess.run
        self.env = env
        self.cwd = str(cwd) if cwd else None

    def argv(self, at):
        """The argv for a run at instant `at`."""
        base = self.command or [sys.executable, str(self.script), "weekly-reset"]
        iso = _iso(at)
        if any("{at}" in part for part in base):
            return [part.replace("{at}", iso) for part in base]
        return [*base, "--at", iso]

    def describe(self, at):
        return " ".join(shlex.quote(part) for part in self.argv(at))

    def __call__(self, week, closing_week, campaign=CAMPAIGN, at=None):
        argv = self.argv(at or datetime.now(timezone.utc))
        shown = " ".join(shlex.quote(part) for part in argv)
        if self.command is None and not self.script.exists():
            log.error("weekly_reset v4 job missing script=%s week=%s", self.script, week)
            raise ResetStepError(f"v4 weekly-reset job not installed: {self.script} does not exist")
        log.debug("weekly_reset v4 call enter week=%s closing_week=%s campaign=%s command=%s",
                  week, closing_week, campaign, shown)
        try:
            proc = self.runner(argv, capture_output=True, text=True, timeout=self.timeout_s,
                               cwd=self.cwd, env=self.env)
        except FileNotFoundError as exc:
            log.error("weekly_reset v4 command not found command=%s error=%s", shown, exc)
            raise ResetStepError(f"v4 weekly-reset command not found: {argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            log.error("weekly_reset v4 command timed out command=%s timeout_s=%s", shown,
                      self.timeout_s)
            raise ResetStepError(f"v4 weekly-reset timed out after {self.timeout_s:g}s") from exc
        code = proc.returncode
        log.debug("weekly_reset v4 call exit week=%s exit_code=%s", week, code)
        if code not in V4_OK_EXIT_CODES:
            tail = (proc.stderr or proc.stdout or "").strip()[-300:]
            log.error("weekly_reset v4 job failed week=%s exit_code=%s", week, code)
            raise ResetStepError(f"v4 weekly-reset exited {code}: {tail}")
        status = "done" if code == 0 else "nothing_to_do"
        log.info("weekly_reset v4 job ok week=%s exit_code=%s status=%s", week, code, status)
        return {"command": shown, "exit_code": code, "status": status}


def _remove_path(path, protected_roots):
    """Delete a file or directory. Refuses the filesystem root, the home
    directory, any protected root (the workspace) and any directory that
    holds a `.git`. Returns 'removed', 'missing' or raises ResetStepError."""
    p = Path(path)
    if not p.exists() and not p.is_symlink():
        return "missing"
    if p.is_dir() and not p.is_symlink():
        resolved = p.resolve()
        forbidden = {Path(resolved.anchor), Path.home().resolve(),
                     *(Path(r).resolve() for r in protected_roots if r)}
        if resolved in forbidden or (resolved / ".git").exists():
            log.error("weekly_reset refusing to delete directory path=%s", p)
            raise ResetStepError(f"refusing to delete protected directory {p}")
        shutil.rmtree(p)
    else:
        p.unlink()
    return "removed"


# ── orchestrator ─────────────────────────────────────────────────────────────
class WeeklyReset:
    """Runs the Sunday reset for one week against injected clients.

    git: a git_client.GitClient on the Fraud-Stop working copy (the
    Engineer's WORKSPACE_PATH). gitea: a gitea_client.GiteaClient for the
    same repo. v4_hook: callable(week, closing_week, campaign, at) -> detail
    dict (default CommandV4Hook()); it must raise to fail the step."""

    def __init__(self, git, gitea, ledger, config=None, v4_hook=None,
                 epoch=DEFAULT_EPOCH, tz=DEFAULT_TZ, workspace=None):
        self.git = git
        self.gitea = gitea
        self.ledger = ledger if isinstance(ledger, StepLedger) else StepLedger(ledger)
        self.config = config or ResetConfig()
        self.v4_hook = v4_hook or CommandV4Hook()
        self.epoch = epoch
        self.tz = tz
        self.workspace = workspace or getattr(git, "repo_path", None)
        self._handlers = {
            STEP_ARCHIVE: self._step_archive,
            STEP_RESET: self._step_reset,
            STEP_PRUNE: self._step_prune,
            STEP_CLOSE_ISSUES: self._step_close_issues,
            STEP_CLEAR_STATE: self._step_clear_state,
            STEP_V4: self._step_v4,
        }

    # ── helpers ──────────────────────────────────────────────────────────────
    def _act(self, ctx, action, **fields):
        """Record + log one action. In dry-run mode this is all that happens."""
        entry = {"step": ctx["step"], "action": action, **fields}
        ctx["result"].actions.append(entry)
        kv = " ".join(f"{k}={v}" for k, v in fields.items())
        verb = "would" if ctx["dry_run"] else "doing"
        log.info("weekly_reset %s run_id=%s week=%s step=%s action=%s %s",
                 verb, ctx["result"].run_id, ctx["week"], ctx["step"], action, kv)

    def _has_remote(self):
        return bool(getattr(self.git, "remote_url", None))

    def _fetch(self, ctx):
        if not self._has_remote():
            log.debug("weekly_reset no remote configured, fetch skipped")
            return
        self._act(ctx, "fetch")
        if ctx["dry_run"]:
            return
        if not self.git.fetch(tags=True, prune=True):
            raise ResetStepError("git fetch failed")

    def _push(self, ctx, branch):
        if not self._has_remote():
            log.warning("weekly_reset no remote configured, push skipped branch=%s", branch)
            return False
        self._act(ctx, "push_branch", branch=branch)
        if ctx["dry_run"]:
            return False
        if not self.git.push_branch(branch):
            raise ResetStepError(f"git push of {branch} failed")
        return True

    # ── steps ────────────────────────────────────────────────────────────────
    def _step_archive(self, ctx):
        closing = ctx["closing_week"]
        if closing < FIRST_WEEK:
            log.debug("weekly_reset archive: no previous week week=%s", ctx["week"])
            return {"skipped": "no previous week"}
        source = self.config.week_branch(closing)
        archive = self.config.archive_branch(closing)
        self._fetch(ctx)
        if ctx["dry_run"]:
            self._act(ctx, "commit_wip_if_dirty")
            self._act(ctx, "checkout", ref=source, fallback=self.config.base_branch)
            self._act(ctx, "create_branch", branch=archive)
            self._push(ctx, archive)
            return {"archive": archive, "source": source}

        wip = None
        if self.git.is_dirty():
            self._act(ctx, "commit_wip")
            wip = self.git.commit_all(f"chore(loop): week {closing} work in progress at reset")
        try:
            self.git.checkout(archive)
            used, existed = archive, True
            log.debug("weekly_reset archive branch already exists branch=%s", archive)
        except GitError:
            existed = False
            try:
                self._act(ctx, "checkout", ref=source)
                self.git.checkout(source)
                used = source
            except GitError as exc:
                log.warning("weekly_reset week branch missing, archiving base branch "
                            "source=%s base=%s error=%s", source, self.config.base_branch, exc)
                self._act(ctx, "checkout", ref=self.config.base_branch)
                self.git.checkout(self.config.base_branch)
                used = self.config.base_branch
            self._act(ctx, "create_branch", branch=archive, start=used)
            self.git.checkout_new_branch(archive)
        pushed = self._push(ctx, archive)
        return {"archive": archive, "source": used, "already_existed": existed,
                "head": self.git.head(), "wip_commit": wip, "pushed": pushed}

    def _step_reset(self, ctx):
        branch = self.config.week_branch(ctx["week"])
        seed = self.config.seed_tag
        self._fetch(ctx)
        self._act(ctx, "create_branch", branch=branch, start=seed)
        self._act(ctx, "reset_hard", ref=seed)
        if ctx["dry_run"]:
            self._push(ctx, branch)
            return {"branch": branch, "seed_tag": seed}
        try:
            self.git.checkout_new_branch(branch, start_point=seed)
            existed = False
        except GitError:
            log.debug("weekly_reset week branch exists, reusing branch=%s", branch)
            self.git.checkout(branch)
            existed = True
        head = self.git.reset_hard_to(seed)
        pushed = self._push(ctx, branch)
        return {"branch": branch, "seed_tag": seed, "head": head,
                "already_existed": existed, "pushed": pushed}

    def _pr_head(self, pr):
        head = pr.get("head") or {}
        ref = _short_ref(head.get("ref"))
        repo = head.get("repo") or {}
        full = repo.get("full_name") if isinstance(repo, dict) else None
        return ref, full

    def _step_prune(self, ctx):
        keep_open = {self._pr_head(pr)[0] for pr in self.gitea.list_prs(state="open")}
        candidates, protected, foreign = [], [], []
        for pr in self.gitea.list_prs(state="closed"):
            if not pr.get("merged"):
                continue
            ref, full = self._pr_head(pr)
            if not ref or ref in candidates or ref in protected:
                continue
            if self.config.repo_full_name and full and full != self.config.repo_full_name:
                foreign.append(ref)
                continue
            if is_protected_branch(ref, self.config) or ref in keep_open:
                log.debug("weekly_reset prune keeps branch=%s open_pr=%s", ref, ref in keep_open)
                protected.append(ref)
                continue
            candidates.append(ref)
        deleted, gone = [], []
        for ref in candidates:
            self._act(ctx, "delete_branch", branch=ref)
            if ctx["dry_run"]:
                continue
            try:
                self.gitea.delete_branch(ref)
                deleted.append(ref)
            except GiteaError as exc:
                if exc.status == 404:
                    log.debug("weekly_reset branch already gone branch=%s", ref)
                    gone.append(ref)
                    continue
                raise
        return {"deleted": deleted if not ctx["dry_run"] else candidates,
                "already_gone": gone, "kept": protected, "foreign": foreign}

    def _step_close_issues(self, ctx):
        closing = ctx["closing_week"]
        if closing < FIRST_WEEK:
            log.debug("weekly_reset close_issues: no previous week week=%s", ctx["week"])
            return {"skipped": "no previous week"}
        label = self.config.issue_label(closing)
        closed = []
        for issue in self.gitea.list_issues(state="open", labels=[label]):
            names = {lab.get("name") for lab in issue.get("labels") or [] if isinstance(lab, dict)}
            if label not in names:
                # Gitea may ignore an unknown label filter and return everything.
                log.debug("weekly_reset issue lacks label, kept number=%s label=%s",
                          issue.get("number"), label)
                continue
            number = issue.get("number")
            self._act(ctx, "close_issue", number=number, label=label)
            if not ctx["dry_run"]:
                self.gitea.close_issue(number)
            closed.append(number)
        return {"label": label, "closed": closed}

    def _step_clear_state(self, ctx):
        results = {}
        for path in self.config.session_state_paths:
            exists = os.path.lexists(path)
            log.debug("weekly_reset session state path=%s exists=%s", path, exists)
            if not exists:
                results[str(path)] = "missing"
                continue
            self._act(ctx, "remove_path", path=path)
            if ctx["dry_run"]:
                results[str(path)] = "would_remove"
                continue
            results[str(path)] = _remove_path(path, [self.workspace])
        return {"paths": results}

    def _step_v4(self, ctx):
        describe = getattr(self.v4_hook, "describe", None)
        command = describe(ctx["at"]) if callable(describe) else None
        self._act(ctx, "call_v4_weekly_reset", **({"command": command} if command else {}))
        if ctx["dry_run"]:
            return {"command": command} if command else {}
        detail = self.v4_hook(ctx["week"], ctx["closing_week"], self.config.campaign, ctx["at"])
        return detail if isinstance(detail, dict) else {"result": detail}

    # ── run ──────────────────────────────────────────────────────────────────
    def week_for(self, at):
        return week_for(at, self.epoch, self.tz)

    def run(self, at=None, dry_run=False):
        """Reset the week that contains `at` (tz-aware; default now). Returns a
        ResetResult; `ok` is False when a step failed (the run stopped)."""
        at = at or datetime.now(timezone.utc)
        week = self.week_for(at)
        result = ResetResult(week=week, closing_week=week - 1, dry_run=dry_run,
                             run_id=str(uuid.uuid4()))
        _trace("weekly_reset run enter run_id=%s at=%s week=%s dry_run=%s",
               result.run_id, at, week, dry_run)
        log.info("weekly_reset start run_id=%s week=%s closing_week=%s reset_at=%s dry_run=%s",
                 result.run_id, week, week - 1,
                 segment_start(week, 0, 0, self.epoch, self.tz).isoformat(), dry_run)
        for step in STEPS:
            if self.ledger.is_done(week, step):
                log.debug("weekly_reset step already done run_id=%s week=%s step=%s",
                          result.run_id, week, step)
                result.skipped.append(step)
                continue
            ctx = {"step": step, "week": week, "closing_week": week - 1, "at": at,
                   "dry_run": dry_run, "result": result}
            try:
                detail = self._handlers[step](ctx)
            except Exception as exc:  # any step failure stops the run; retried next time
                result.failed_step, result.error = step, f"{type(exc).__name__}: {exc}"
                log.error("weekly_reset step failed run_id=%s week=%s step=%s error=%s",
                          result.run_id, week, step, result.error)
                if not dry_run:
                    self.ledger.mark_failed(week, step, result.error)
                break
            result.details[step] = detail
            result.ran.append(step)
            if not dry_run:
                self.ledger.mark_done(week, step, detail)
            log.info("weekly_reset step done run_id=%s week=%s step=%s dry_run=%s",
                     result.run_id, week, step, dry_run)
        if result.ok:
            log.info("weekly_reset complete run_id=%s week=%s ran=%s skipped=%s dry_run=%s",
                     result.run_id, week, len(result.ran), len(result.skipped), dry_run)
        _trace("weekly_reset run exit run_id=%s ok=%s", result.run_id, result.ok)
        return result


# ── CLI ──────────────────────────────────────────────────────────────────────
def _parse_at(text):
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--at must be an ISO datetime: {exc}") from exc
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError("--at must include a UTC offset, e.g. 2026-10-04T00:00-04:00")
    return value


def _parse_epoch(text):
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--epoch must be YYYY-MM-DD: {exc}") from exc


def build_parser():
    env = os.environ.get
    ap = argparse.ArgumentParser(
        prog="python -m office.weekly_reset",
        description="Run the ashiorid_office Sunday reset for the week containing now (or --at).")
    ap.add_argument("--dry-run", action="store_true", help="log every action, change nothing")
    ap.add_argument("--at", type=_parse_at, help="tz-aware ISO instant to reset for (testing)")
    ap.add_argument("--epoch", type=_parse_epoch,
                    default=_parse_epoch(env("OFFICE_EPOCH") or DEFAULT_EPOCH.isoformat()),
                    help="loop epoch Sunday (env OFFICE_EPOCH)")
    ap.add_argument("--tz", default=env("OFFICE_TZ") or DEFAULT_TZ)
    ap.add_argument("--ledger", default=env("OFFICE_RESET_LEDGER") or str(DEFAULT_LEDGER))
    ap.add_argument("--workspace", default=env("WORKSPACE_PATH"),
                    help="Fraud-Stop working copy (env WORKSPACE_PATH)")
    ap.add_argument("--remote-url", default=None, help="git remote (default env GIT_SERVER_URL)")
    ap.add_argument("--author", default="office_clock", help="git author name for WIP commits")
    ap.add_argument("--gitea-url", default=env("OFFICE_GITEA_URL") or DEFAULT_GITEA_BASE_URL)
    ap.add_argument("--owner", default=DEFAULT_GITEA_OWNER)
    ap.add_argument("--repo", default=DEFAULT_GITEA_REPO)
    ap.add_argument("--token-env", default="GITEA_TOKEN_OFFICE",
                    help="NAME of the env var holding the Gitea token")
    ap.add_argument("--base-branch", default="main")
    ap.add_argument("--seed-tag", default="loop-seed")
    ap.add_argument("--state-path", action="append", default=[],
                    help="extra session-state file/dir to delete (repeatable)")
    ap.add_argument("--v4-command", default=env("OFFICE_V4_RESET_CMD"),
                    help="the v4 weekly-reset command, shlex-split; `--at <iso>` is appended "
                         "(or replaces a literal {at}). Default: python "
                         "services/character-updater/main.py weekly-reset "
                         "(env OFFICE_V4_RESET_CMD)")
    ap.add_argument("--v4-timeout", type=float,
                    default=float(env("OFFICE_V4_RESET_TIMEOUT_S") or DEFAULT_V4_TIMEOUT_S),
                    help="seconds before the v4 job is killed and the step fails "
                         "(env OFFICE_V4_RESET_TIMEOUT_S)")
    ap.add_argument("--bootstrap", default=env("KAFKA_BOOTSTRAP_SERVERS"),
                    help="Kafka bootstrap servers, passed to the v4 job as "
                         "KAFKA_BOOTSTRAP_SERVERS (the v4 job publishes character_refresh)")
    ap.add_argument("--topic", default=env("KAFKA_TOPIC") or DEFAULT_TOPIC,
                    help=argparse.SUPPRESS)  # kept for old cron lines; unused
    ap.add_argument("--status", action="store_true", help="print the ledger record and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv=None, git=None, gitea=None, v4_hook=None):
    """CLI entry. Clients can be injected (tests); otherwise they are built
    from the flags. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        ledger = StepLedger(args.ledger)
    except LedgerError as exc:
        log.error("weekly_reset cannot load ledger error=%s", exc)
        return 2
    at = args.at or datetime.now(timezone.utc)
    week = week_for(at, args.epoch, args.tz)
    if args.status:
        print(json.dumps({"week": week, "record": ledger.record(week)}, indent=2))
        return 0

    if git is None:
        if not args.workspace:
            log.error("weekly_reset needs --workspace (or WORKSPACE_PATH)")
            return 2
        from git_client import GitClient
        git = GitClient(args.workspace, args.author, remote_url=args.remote_url)
    if gitea is None:
        from gitea_client import GiteaClient
        gitea = GiteaClient(args.gitea_url, args.owner, args.repo, token_env=args.token_env)
    if v4_hook is None:
        env = None
        if args.bootstrap:
            env = {**os.environ, "KAFKA_BOOTSTRAP_SERVERS": args.bootstrap}
        v4_hook = CommandV4Hook(args.v4_command, timeout_s=args.v4_timeout, env=env)
    workspace = args.workspace or getattr(git, "repo_path", None)
    state_paths = [os.path.join(workspace, name) for name in DEFAULT_WORKSPACE_STATE_FILES] \
        if workspace else []
    config = ResetConfig(base_branch=args.base_branch, seed_tag=args.seed_tag,
                         session_state_paths=tuple(state_paths + list(args.state_path)),
                         repo_full_name=f"{args.owner}/{args.repo}")
    runner = WeeklyReset(git, gitea, ledger, config=config, v4_hook=v4_hook,
                         epoch=args.epoch, tz=args.tz, workspace=workspace)
    result = runner.run(at=at, dry_run=args.dry_run)
    print(json.dumps(result.as_dict(), indent=2, default=str))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
