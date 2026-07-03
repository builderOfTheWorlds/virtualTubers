# workspace_setup.py

## Overview

Seeds a coder worker's workspace volume from the sandbox template baked
into the image (`/app/sandbox`, copied by the Dockerfile from `sandbox/`),
then git-inits it with an initial commit. Called by
`coding_backend.build_coding_backend()` on every coder startup; idempotent,
so agent commits survive container restarts.

## Signature

```python
def ensure_workspace(workspace: str, author_name: str,
                     template: str = "/app/sandbox") -> GitClient
```

## Parameters

- `workspace` (str, required) — target directory (the mounted volume, e.g.
  `/data/repo`).
- `author_name` (str, required) — persona name for the initial commit's
  attribution.
- `template` (str, optional) — sandbox template source; only consulted when
  the workspace is empty.

## Return Value

A ready `GitClient` bound to the workspace. Three cases:

1. already a git repo → untouched (restart case);
2. missing/empty dir → template copied in, `git init` + initial commit;
3. files but no `.git` → init + commit of what's there (a pre-populated
   volume is treated as the intended starting tree, NOT overwritten).

## Dependencies

- `git_client.GitClient`, `shutil.copytree`.

## Usage Examples

```python
git = ensure_workspace("/data/repo", "NYX-1")           # container
git = ensure_workspace(str(tmp), "TEST-1", template=str(tpl))  # tests
```

## Error Handling

- `FileNotFoundError` — empty workspace and the template directory doesn't
  exist. Surfaces through `build_coding_backend`'s catch in `agent.py`
  (worker degrades to narration-only with a WARN, never crashes).
- `GitError` — propagated from the underlying git calls.

## Changelog

- v1.0.0 (2026-07-03) — initial version.
