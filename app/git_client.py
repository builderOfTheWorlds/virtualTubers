"""
git_client.py
Local git operations for the coder workers' workspaces: init/seed detection,
commit-all, diff stats between refs, branch/tag/reset helpers — plus real
remote operations (push, fetch, tag push) against the Gitea server once a
remote is configured.

Remote mode is opt-in: with no remote_url (and GIT_SERVER_URL unset/empty)
every remote operation is a logged no-op returning False — nothing here may
fail a coding run just because no remote exists.

Remote auth:
- ssh:// / scp-style remotes rely on the mounted key. An explicit
  ssh_command (or env GIT_SSH_COMMAND, e.g.
  "ssh -o StrictHostKeyChecking=accept-new") is passed through.
- http(s):// remotes get the token via a GIT_ASKPASS helper script written
  to a 0700 temp file for the duration of the call. The script contains only
  the *name* of the env var holding the token (default GITEA_TOKEN_OFFICE);
  the token itself never appears in the remote URL, git argv, on disk, or in
  logs.

PRs, issues, reviews and merges go through gitea_client.GiteaClient.

Identity is passed per-invocation (-c user.name/user.email) so containers
need no global git config and each worker's commits are attributed to its
persona (e.g. "NYX-1 <nyx-1@virtualtubers.local>").
"""
import logging
import os
import re
import stat
import subprocess
import tempfile

log = logging.getLogger(__name__)

SHORTSTAT_RE = re.compile(
    r"(?:(\d+) files? changed)?(?:, )?(?:(\d+) insertions?\(\+\))?(?:, )?(?:(\d+) deletions?\(-\))?"
)
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_URL_USERINFO_RE = re.compile(r"(https?://)[^/@\s]+@", re.IGNORECASE)

DEFAULT_TOKEN_ENV = "GITEA_TOKEN_OFFICE"
DEFAULT_HTTP_USER = "gitea_admin"
DEFAULT_REMOTE_NAME = "origin"

# POSIX sh askpass helper. Git calls it with the prompt as $1
# ("Username for 'http://…': " / "Password for 'http://…': "). The token is
# expanded from the environment at call time — the file never contains it.
_ASKPASS_TEMPLATE = """#!/bin/sh
case "$1" in
  Username*|username*) printf '%s\\n' "${{VT_GIT_HTTP_USER}}" ;;
  *) printf '%s\\n' "${{{token_env}}}" ;;
esac
"""


class GitError(RuntimeError):
    """Raised when a git invocation fails; message carries git's stderr."""


def redact_url(text):
    """Strip any user:pass@ userinfo from http(s) URLs inside `text`, so a
    misconfigured credential-bearing remote can never leak into logs."""
    if not text:
        return text
    return _URL_USERINFO_RE.sub(r"\1***@", str(text))


def remote_kind(url):
    """Classify a remote URL: 'http', 'ssh', 'local', or None when unset."""
    if not url:
        return None
    lowered = url.lower()
    if lowered.startswith(("http://", "https://")):
        return "http"
    if lowered.startswith(("ssh://", "git+ssh://", "ssh+git://")):
        return "ssh"
    # scp-style: git@host:owner/repo.git (a Windows drive "C:\..." has no '@')
    if re.match(r"^[\w.\-]+@[\w.\-]+:", url):
        return "ssh"
    return "local"


def _safe_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        log.warning("git.askpass event=cleanup_failed path=%s", path)


class GitClient:
    def __init__(
        self,
        repo_path,
        author_name,
        author_email=None,
        remote_url=None,
        token_env=None,
        ssh_command=None,
        http_user=None,
        remote_name=DEFAULT_REMOTE_NAME,
    ):
        self.repo_path = repo_path
        self.author_name = author_name
        self.author_email = author_email or f"{author_name.lower()}@virtualtubers.local"
        self.remote_url = remote_url or os.environ.get("GIT_SERVER_URL") or None
        self.token_env = token_env or os.environ.get("GIT_TOKEN_ENV") or DEFAULT_TOKEN_ENV
        if not _ENV_NAME_RE.match(self.token_env):
            # Guards against someone passing the token itself instead of the var name.
            raise ValueError("token_env must be an environment variable NAME (e.g. GITEA_TOKEN_OFFICE)")
        self.ssh_command = ssh_command or os.environ.get("GIT_SSH_COMMAND") or None
        self.http_user = http_user or os.environ.get("GIT_HTTP_USER") or DEFAULT_HTTP_USER
        self.remote_name = remote_name

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _run(self, *args, env=None):
        shown = redact_url(" ".join(args))
        log.debug("git.run event=enter repo=%s args=%s", self.repo_path, shown)
        result = subprocess.run(
            [
                "git", "-C", self.repo_path,
                "-c", f"user.name={self.author_name}",
                "-c", f"user.email={self.author_email}",
                *args,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            log.debug("git.run event=fail args=%s rc=%s", shown, result.returncode)
            raise GitError(f"git {shown} failed: {redact_url(result.stderr.strip())}")
        return result.stdout.strip()

    def _remote_env(self, askpass_path=None):
        """Environment for a network git call. Adds only the askpass path and
        the http username — the token stays in the var the parent already has."""
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        if self.ssh_command:
            env["GIT_SSH_COMMAND"] = self.ssh_command
        if askpass_path:
            env["GIT_ASKPASS"] = askpass_path
            env["VT_GIT_HTTP_USER"] = self.http_user
            env.pop("SSH_ASKPASS", None)
        return env

    def _write_askpass(self):
        """Write the askpass helper to a 0700 temp file; caller deletes it."""
        fd, path = tempfile.mkstemp(prefix="vt-askpass-", suffix=".sh")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(_ASKPASS_TEMPLATE.format(token_env=self.token_env))
            os.chmod(path, stat.S_IRWXU)
        except OSError:
            log.error("git.askpass event=write_failed path=%s", path, exc_info=True)
            _safe_unlink(path)
            raise
        log.debug("git.askpass event=written path=%s token_env=%s", path, self.token_env)
        return path

    def _run_remote(self, *args):
        """Run a network git command with the right auth for the remote kind.
        Raises GitError/OSError; callers decide whether to swallow."""
        kind = remote_kind(self.remote_url)
        askpass = None
        extra = []
        if kind == "http":
            if not os.environ.get(self.token_env):
                log.warning("git.remote event=no_token token_env=%s", self.token_env)
            askpass = self._write_askpass()
            # Bypass any host credential helper so only our askpass answers.
            extra = ["-c", "credential.helper="]
        try:
            return self._run(*extra, *args, env=self._remote_env(askpass))
        finally:
            if askpass:
                _safe_unlink(askpass)

    def _ensure_remote(self):
        """Add or re-point the named remote to self.remote_url."""
        remotes = self._run("remote").split()
        if self.remote_name not in remotes:
            log.debug("git.remote event=add name=%s url=%s", self.remote_name, redact_url(self.remote_url))
            self._run("remote", "add", self.remote_name, self.remote_url)
            return
        current = self._run("remote", "get-url", self.remote_name)
        if current != self.remote_url:
            log.debug("git.remote event=set_url name=%s url=%s", self.remote_name, redact_url(self.remote_url))
            self._run("remote", "set-url", self.remote_name, self.remote_url)

    def _no_remote(self, op):
        print(f"[git:{self.author_name}] no GIT_SERVER_URL configured — skipping {op} (local-only mode)")
        log.debug("git.remote event=skip op=%s reason=no_remote author=%s", op, self.author_name)
        return False

    # ── local operations ─────────────────────────────────────────────────────

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

    def current_branch(self):
        """Current branch name, or None when detached / not a repo."""
        try:
            name = self._run("symbolic-ref", "--short", "-q", "HEAD")
        except GitError:
            return None
        return name or None

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

    def checkout(self, ref):
        """Switch to an existing branch/ref. Returns the new HEAD sha."""
        log.debug("git.checkout event=enter ref=%s", ref)
        self._run("checkout", ref)
        return self.head()

    def checkout_new_branch(self, name, start_point=None):
        """Create and switch to `name` (optionally from `start_point`, e.g. a
        tag). Raises GitError if the branch exists or start_point is unknown.
        Returns the new HEAD sha."""
        log.debug("git.checkout_new_branch event=enter name=%s start=%s", name, start_point)
        args = ["checkout", "-b", name]
        if start_point:
            args.append(start_point)
        self._run(*args)
        sha = self.head()
        log.info("git.checkout_new_branch event=done name=%s head=%s author=%s", name, sha, self.author_name)
        return sha

    def create_tag(self, name, ref="HEAD", message=None):
        """Create a local tag (annotated when `message` is given). Returns the
        commit sha the tag points at. Raises GitError if it already exists."""
        log.debug("git.create_tag event=enter name=%s ref=%s annotated=%s", name, ref, bool(message))
        if message:
            self._run("tag", "-a", name, "-m", message, ref)
        else:
            self._run("tag", name, ref)
        sha = self._run("rev-list", "-n", "1", name)
        log.info("git.create_tag event=done name=%s sha=%s", name, sha)
        return sha

    def reset_hard_to(self, ref):
        """Hard-reset the current branch + worktree to `ref` and remove
        untracked files/dirs (weekly loop reset). Ignored files survive.
        Returns the new HEAD sha."""
        log.debug("git.reset_hard_to event=enter ref=%s", ref)
        self._run("reset", "--hard", ref)
        self._run("clean", "-fd")
        sha = self.head()
        log.info("git.reset_hard_to event=done ref=%s head=%s author=%s", ref, sha, self.author_name)
        return sha

    # ── remote operations — graceful no-ops when no remote is configured ─────

    def push(self, branch="HEAD", force=False, set_upstream=True):
        """Push `branch` to the configured remote. Returns False when no
        remote is configured (local-only mode is the default) or the push
        fails; True on success. Failures are reported, not raised: a
        completed local commit must never be lost to a network error.
        force=True uses --force-with-lease (never a blind --force)."""
        if not self.remote_url:
            return self._no_remote("push")
        kind = remote_kind(self.remote_url)
        log.debug("git.push event=enter branch=%s kind=%s force=%s", branch, kind, force)
        args = ["push"]
        if set_upstream:
            args.append("-u")
        if force:
            args.append("--force-with-lease")
        args += [self.remote_name, branch]
        try:
            self._ensure_remote()
            self._run_remote(*args)
        except (GitError, OSError) as exc:
            print(f"[git:{self.author_name}] push failed (continuing local-only): {redact_url(exc)}")
            log.error("git.push event=failed branch=%s kind=%s error=%s", branch, kind, redact_url(exc))
            return False
        log.info("git.push event=done branch=%s kind=%s author=%s", branch, kind, self.author_name)
        return True

    def push_branch(self, name, force=False):
        """Push a named local branch (sets upstream). Returns bool like push()."""
        return self.push(branch=name, force=force)

    def push_tag(self, name):
        """Push a single tag to the remote. Returns bool like push()."""
        if not self.remote_url:
            return self._no_remote("push_tag")
        try:
            self._ensure_remote()
            self._run_remote("push", self.remote_name, f"refs/tags/{name}")
        except (GitError, OSError) as exc:
            log.error("git.push_tag event=failed tag=%s error=%s", name, redact_url(exc))
            return False
        log.info("git.push_tag event=done tag=%s author=%s", name, self.author_name)
        return True

    def fetch(self, ref=None, tags=True, prune=True):
        """Fetch from the remote (optionally a single ref). Returns bool."""
        if not self.remote_url:
            return self._no_remote("fetch")
        args = ["fetch"]
        if tags:
            args.append("--tags")
        if prune:
            args.append("--prune")
        args.append(self.remote_name)
        if ref:
            args.append(ref)
        try:
            self._ensure_remote()
            self._run_remote(*args)
        except (GitError, OSError) as exc:
            log.error("git.fetch event=failed ref=%s error=%s", ref, redact_url(exc))
            return False
        log.info("git.fetch event=done ref=%s author=%s", ref, self.author_name)
        return True

    def create_pr(self, title, body=""):
        """Kept as a logged no-op for backwards compatibility. PRs are opened
        via the Gitea REST API: gitea_client.GiteaClient.open_pr(...)."""
        print(f"[git:{self.author_name}] create_pr({title!r}) skipped — use gitea_client.GiteaClient.open_pr")
        log.debug("git.create_pr event=skip title=%r", title)
        return None
