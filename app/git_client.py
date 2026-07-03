"""
git_client.py
Local git operations for the coder workers' workspaces: init/seed detection,
commit-all, and diff stats between refs. Remote operations (push, PRs) are
deliberately thin no-ops until GIT_SERVER_URL is configured — the local git
server is being set up separately; nothing here may fail a coding run just
because no remote exists yet.

Identity is passed per-invocation (-c user.name/user.email) so containers
need no global git config and each worker's commits are attributed to its
persona (e.g. "NYX-1 <nyx-1@virtualtubers.local>").
"""
import os
import re
import subprocess

SHORTSTAT_RE = re.compile(
    r"(?:(\d+) files? changed)?(?:, )?(?:(\d+) insertions?\(\+\))?(?:, )?(?:(\d+) deletions?\(-\))?"
)


class GitError(RuntimeError):
    """Raised when a git invocation fails; message carries git's stderr."""


class GitClient:
    def __init__(self, repo_path, author_name, author_email=None, remote_url=None):
        self.repo_path = repo_path
        self.author_name = author_name
        self.author_email = author_email or f"{author_name.lower()}@virtualtubers.local"
        self.remote_url = remote_url or os.environ.get("GIT_SERVER_URL")

    def _run(self, *args):
        result = subprocess.run(
            [
                "git", "-C", self.repo_path,
                "-c", f"user.name={self.author_name}",
                "-c", f"user.email={self.author_email}",
                *args,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def is_repo(self):
        try:
            return self._run("rev-parse", "--is-inside-work-tree") == "true"
        except GitError:
            return False

    def init_repo(self, initial_message="chore: seed sandbox workspace"):
        """git init + first commit of everything present. Used by
        workspace_setup after copying the sandbox template in."""
        self._run("init")
        self._run("add", "-A")
        self._run("commit", "-m", initial_message)
        return self.head()

    def head(self):
        """Current HEAD sha, or None in an empty (no-commit) repo."""
        try:
            return self._run("rev-parse", "HEAD")
        except GitError:
            return None

    def is_dirty(self):
        return bool(self._run("status", "--porcelain"))

    def commit_all(self, message):
        """Stage everything and commit. Returns the new sha, or None when
        there was nothing to commit (backends that produced no file changes
        must not create empty commits)."""
        self._run("add", "-A")
        if not self._run("status", "--porcelain"):
            return None
        self._run("commit", "-m", message)
        return self.head()

    def diff_stats(self, from_ref, to_ref="HEAD"):
        """(files_changed, insertions, deletions) between two refs via
        --shortstat. (0, 0, 0) when refs are equal/absent — a no-change run
        is a valid result, not an error."""
        if not from_ref or not to_ref or from_ref == to_ref:
            return 0, 0, 0
        out = self._run("diff", "--shortstat", from_ref, to_ref)
        match = SHORTSTAT_RE.search(out or "")
        if not match:
            return 0, 0, 0
        files, ins, dels = (int(g) if g else 0 for g in match.groups())
        return files, ins, dels

    def log_last(self, n=1):
        """Last n commit subjects (oneline), newest first. Empty list for an
        empty repo — used to surface aider's auto-commit message."""
        try:
            out = self._run("log", f"-{n}", "--pretty=%h %s")
        except GitError:
            return []
        return out.splitlines()

    # ── Remote operations — graceful no-ops until GIT_SERVER_URL is set ──────

    def push(self, branch="HEAD"):
        """Push to the configured remote. Returns False (and logs) when no
        remote is configured — local-only mode is the expected state until
        the user's git server exists. Push failures are reported, not raised:
        a completed local commit must never be lost to a network error."""
        if not self.remote_url:
            print(f"[git:{self.author_name}] no GIT_SERVER_URL configured — skipping push (local-only mode)")
            return False
        try:
            remotes = self._run("remote")
            if "origin" not in remotes.split():
                self._run("remote", "add", "origin", self.remote_url)
            self._run("push", "-u", "origin", branch)
            return True
        except GitError as exc:
            print(f"[git:{self.author_name}] push failed (continuing local-only): {exc}")
            return False

    def create_pr(self, title, body=""):
        """Placeholder for the git server's PR API (Gitea-compatible REST
        planned). Queued until GIT_SERVER_URL + API tokens exist — see
        .claude/prompts/coding_backend_ab_test.md."""
        print(f"[git:{self.author_name}] create_pr({title!r}) skipped — no git server API configured yet")
        return None
