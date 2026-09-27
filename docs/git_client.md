# git_client.py

## Overview

Git operations for coder workspaces and the Fraud-Stop office loop:
init/seed, commit-everything, diff stats, per-persona commit attribution,
branch/tag/reset helpers, and real remote operations (`push`, `push_branch`,
`push_tag`, `fetch`) against the Gitea server.

Remote mode is opt-in. With no `remote_url` (and `GIT_SERVER_URL` unset or
empty) every remote operation is a logged no-op returning `False`. Remote
failures are reported, never raised: a finished local commit must never be
lost to a missing or broken remote.

PRs, issues, reviews and merges go through the REST API instead. See
[`gitea_client.md`](gitea_client.md).

### Remote auth

| Remote form | Auth |
|---|---|
| `ssh://git@192.168.1.120:2222/gitea_admin/fraud-stop.git` or `git@host:owner/repo.git` | Mounted SSH key. `ssh_command` (or env `GIT_SSH_COMMAND`, e.g. `ssh -o StrictHostKeyChecking=accept-new`) is passed to git. |
| `http(s)://…` | For the duration of the call, a `GIT_ASKPASS` helper is written to a temp file with mode 0700 and deleted afterwards. It answers the username from `http_user` (default `gitea_admin`) and the password by expanding `${<token_env>}` at runtime (default `GITEA_TOKEN_OFFICE`). The file holds only the variable **name**, never the token. `credential.helper` is blanked for the call, and `GIT_TERMINAL_PROMPT=0` stops git from hanging on a prompt. |
| local path | Plain git. Used in tests against a bare repo. |

The token never goes into the remote URL, git argv, a file on disk, or a log
line. Any `user:pass@` userinfo that shows up in git's stderr is redacted
(`redact_url`) before it is logged or put into a `GitError`.

## Signature

```python
class GitError(RuntimeError): ...
def redact_url(text) -> str
def remote_kind(url) -> "http" | "ssh" | "local" | None

class GitClient:
    def __init__(self, repo_path, author_name, author_email=None, remote_url=None,
                 token_env=None, ssh_command=None, http_user=None, remote_name="origin")
    # local
    def is_repo(self) -> bool
    def init_repo(self, initial_message=...) -> str
    def head(self) -> str | None
    def current_branch(self) -> str | None
    def is_dirty(self) -> bool
    def commit_all(self, message: str) -> str | None
    def diff_stats(self, from_ref, to_ref="HEAD") -> tuple[int, int, int]
    def log_last(self, n=1) -> list[str]
    def checkout(self, ref) -> str | None
    def checkout_new_branch(self, name, start_point=None) -> str | None
    def create_tag(self, name, ref="HEAD", message=None) -> str
    def reset_hard_to(self, ref) -> str | None
    # remote (bool; no-op False without a remote)
    def push(self, branch="HEAD", force=False, set_upstream=True) -> bool
    def push_branch(self, name, force=False) -> bool
    def push_tag(self, name) -> bool
    def fetch(self, ref=None, tags=True, prune=True) -> bool
    def create_pr(self, title, body="") -> None      # legacy no-op; use GiteaClient.open_pr
```

## Parameters

- `repo_path` (str, required): the workspace directory.
- `author_name` (str, required): persona name (e.g. `NYX-1`). Identity is
  passed on each invocation via `-c user.name/user.email`, so no global git
  config is needed. `author_email` defaults to `<name-lowercased>@virtualtubers.local`.
- `remote_url` (str, optional): defaults to env `GIT_SERVER_URL`. Empty
  means local-only mode.
- `token_env` (str, optional): the **name** of the env var that holds the
  http(s) token. Defaults to env `GIT_TOKEN_ENV`, then `GITEA_TOKEN_OFFICE`.
  Must be a valid identifier, which guards against passing the token itself.
  `ValueError` otherwise, and the error does not echo the value.
- `ssh_command` (str, optional): defaults to env `GIT_SSH_COMMAND`.
- `http_user` (str, optional): defaults to env `GIT_HTTP_USER`, then `gitea_admin`.
- `remote_name` (str): default `origin`. The remote is added if it is
  missing and re-pointed if its URL differs.
- `push(force=True)` uses `--force-with-lease`, never a blind `--force`.

## Return Value

- `commit_all`: the new HEAD sha, or `None` when the tree was clean. It never
  makes empty commits.
- `diff_stats`: `(files_changed, insertions, deletions)`. Returns `(0, 0, 0)`
  when the refs are equal or absent.
- `checkout*` / `reset_hard_to`: the new HEAD sha. `create_tag`: the sha of
  the tagged commit.
- `push*` / `fetch`: `True` on success. `False` when no remote is configured
  or the operation failed (the failure is logged at ERROR).

## Dependencies

- `git` CLI via `subprocess` (no GitPython). The askpass helper is a POSIX
  `sh` script: native in the Linux worker image, and git-for-Windows' bundled
  sh on dev machines.
- stdlib `logging`, `tempfile`, `stat`, `re`, `os`.

## Usage Examples

```python
git = GitClient("/data/repo", "NYX-1")
before = git.head()
# ... backend writes files ...
sha = git.commit_all("feat: add median()\n\nvia native coding backend")
files, ins, dels = git.diff_stats(before)
```

```python
# Office loop over SSH (key mounted at ~/.ssh)
git = GitClient("/data/fraud-stop", "ENGINEER",
                remote_url="ssh://git@192.168.1.120:2222/gitea_admin/fraud-stop.git",
                ssh_command="ssh -o StrictHostKeyChecking=accept-new")
git.checkout_new_branch("eng/velocity-rule")
git.commit_all("feat(rules): tune velocity window")
git.push_branch("eng/velocity-rule")          # -> True
```

```python
# Weekly reset (OB-31): fresh branch from the seed tag
git.fetch()
git.checkout_new_branch("loop/2026-W40", "loop-seed")
git.push_branch("loop/2026-W40")
```

```python
# http(s) with a token from env; the token never touches argv/URL/logs
git = GitClient(ws, "TESTER", remote_url="http://192.168.1.120:3300/gitea_admin/fraud-stop.git",
                token_env="GITEA_TOKEN_OFFICE")
git.push()
```

```python
# Local-only mode (GIT_SERVER_URL unset): push is a logged no-op
git.push()   # -> False, "[git:NYX-1] no GIT_SERVER_URL configured — skipping push (local-only mode)"
```

## Error Handling

- `GitError` is raised by any failing local git invocation, with git's stderr
  (redacted) in the message. `head()`, `current_branch()` and `log_last()`
  swallow it and return `None` or `[]`.
- Remote ops (`push*`, `fetch`) catch `GitError`/`OSError`, log at ERROR, and
  return `False`.
- `checkout_new_branch` / `create_tag` raise `GitError` if the name already
  exists or the start point is unknown.
- `ValueError` is raised at construction if `token_env` isn't a valid env
  var name.

## Changelog

- v2.0.0 (2026-09-27): OB-23. Real remote support: SSH (`GIT_SSH_COMMAND`
  pass-through) and http(s) with a token via a 0700 temp `GIT_ASKPASS`
  helper. Added `push_branch`, `push_tag`, `fetch`, `checkout`,
  `checkout_new_branch`, `create_tag`, `reset_hard_to` and `current_branch`,
  plus URL redaction in errors and structured logging. `push` gained
  `force` (`--force-with-lease`) and `set_upstream`, and re-points an
  existing `origin`. `create_pr` stays a no-op that points to `GiteaClient`.
- v1.0.0 (2026-07-03): initial version. Local commit surface plus stubbed
  remote ops pending the local git server.
