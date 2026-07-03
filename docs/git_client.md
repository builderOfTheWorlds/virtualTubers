# git_client.py

## Overview

Local git operations for coder workspaces: init/seed, commit-everything,
diff stats between refs, and per-persona commit attribution. Remote
operations (`push`, `create_pr`) exist but are deliberate no-ops until
`GIT_SERVER_URL` is configured — the local git server is being stood up
separately, and a finished local commit must never be lost to a missing or
broken remote.

## Signature

```python
class GitError(RuntimeError): ...

class GitClient:
    def __init__(self, repo_path, author_name, author_email=None, remote_url=None)
    def is_repo(self) -> bool
    def init_repo(self, initial_message=...) -> str
    def head(self) -> str | None
    def is_dirty(self) -> bool
    def commit_all(self, message: str) -> str | None
    def diff_stats(self, from_ref, to_ref="HEAD") -> tuple[int, int, int]
    def log_last(self, n=1) -> list[str]
    def push(self, branch="HEAD") -> bool
    def create_pr(self, title, body="") -> None      # placeholder
```

## Parameters

- `repo_path` (str, required) — the workspace directory.
- `author_name` (str, required) — persona name (e.g. `NYX-1`); identity is
  passed per-invocation via `-c user.name/user.email`, so containers need no
  global git config. `author_email` defaults to
  `<name-lowercased>@virtualtubers.local`.
- `remote_url` (str, optional) — defaults to env `GIT_SERVER_URL`; empty
  means local-only mode.

## Return Value

- `commit_all` — new HEAD sha, or `None` when the tree was clean (no empty
  commits, ever — a no-change run is a valid, visible result).
- `diff_stats` — `(files_changed, insertions, deletions)`; `(0, 0, 0)` for
  equal/absent refs.
- `push` — `False` (with a log line) when no remote is configured or the
  push fails; `True` on success.

## Dependencies

- `git` CLI (present in the worker image and on dev machines) via
  `subprocess` — no GitPython dependency.

## Usage Examples

```python
git = GitClient("/data/repo", "NYX-1")
before = git.head()
# ... backend writes files ...
sha = git.commit_all("feat: add median()\n\nvia native coding backend")
files, ins, dels = git.diff_stats(before)
```

```python
# Local-only mode (GIT_SERVER_URL unset): push is a logged no-op
git.push()   # -> False, "[git:NYX-1] no GIT_SERVER_URL configured — skipping push"
```

## Error Handling

- `GitError` — raised from any failing git invocation with git's stderr in
  the message. `head()`/`log_last()` swallow it for the empty-repo case and
  return `None`/`[]`; `push()` reports-not-raises (see Overview).

## Changelog

- v1.0.0 (2026-07-03) — initial version: local commit surface + stubbed
  remote ops pending the local git server.
