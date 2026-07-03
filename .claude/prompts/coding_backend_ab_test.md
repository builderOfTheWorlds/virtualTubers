# Coding Backend A/B Test — Build Plan (2026-07-03, overnight build)

## Goal

Let coder workers actually write code (not just narrate), via a **swappable
coding backend** selected per-worker in config, so three backends can be
compared head-to-head on identical tasks:

- `native` — our own minimal loop: existing `llm_client.py` + file-edit
  parsing + `git_client.py`
- `opencode` — shells out to `opencode run` (needs Node 18+ in image)
- `aider` — shells out to `aider --message` (isolated venv in image)

One new coder worker per backend (`coder-native`, `coder-opencode`,
`coder-aider`), each with its **own workspace volume** seeded from a sandbox
project, running the same `task_assignment` in parallel. Results logged to
Postgres for comparison.

## Locked decisions (user answered 2026-07-03)

1. **Git**: local commits only; push/PR built but no-op gracefully until
   `GIT_SERVER_URL` is set (user is standing up a local git server separately).
2. **Sandbox**: I create a small seeded-bug Python project as the agents'
   workspace. Everything logged.
3. **Image**: ONE `vtube-worker` image gains Node 18 + opencode + aider —
   backend choice is pure config.
4. **Tester**: real pytest execution replaces the weighted-random stub.

## Architecture decisions made during build

- **Workspaces**: each A/B coder mounts its own named volume at `/data/repo`
  (`repo-native`, `repo-opencode`, `repo-aider`). No shared-writer contention.
  Tester mounts each read-only at `/data/repos/<coder_id>`; resolves the
  workspace to test by convention from the `coder_id` field in the
  `commit_notification` payload (config override: `tester.workspaces`).
  Tester copies the tree to a tmpdir before running pytest (ro mount).
- **Run logging**: workers publish a `coding_run_report` message on the Kafka
  bus (they already have producer + creds); `message-logger` — the only
  service holding Postgres creds — additionally unpacks that type into a new
  `coding_backend_runs` table. No psycopg2/creds added to workers.
- **Sandbox seeding**: sandbox template is COPY'd into the image at
  `/app/sandbox/`; `workspace_setup.py` seeds `/data/repo` from it (git init +
  initial commit) on coder startup if the workspace is empty.
- **Bug-fix routing with multiple coders**: `commit_notification` payload
  gains `coder_id` + carried through `bug_report`; manager re-delegates
  `task_assignment` back to that specific coder (falls back to `"coder"`).
- **Handler signature**: all handlers gain trailing `coding_backend=None`
  kwarg (uniform dispatch, explicit-params style preserved).
- **pytest** added to worker requirements (tester runs it at runtime).
- **Aider isolation**: dedicated venv `/opt/aider`, symlinked binary — avoids
  dependency conflicts with agent runtime.
- **OpenCode config**: adapter writes `opencode.json` into the workspace
  pointing at Ollama's OpenAI-compatible endpoint (`<base_url>/v1`).

## TaskResult contract (all backends)

```
TaskResult:
  backend: str            # native | opencode | aider
  success: bool
  commit: str | None      # HEAD sha after run (None if nothing committed)
  committed: bool         # aider auto-commits; others need git_client
  files_changed: int
  insertions: int
  deletions: int
  duration_s: float
  output: str             # tool stdout/stderr tail or native-loop transcript
  error: str | None
```

## Work order

1. ✅ Read core files
2. ✅ This plan file
3. Sandbox project (`sandbox/`): tiny calculator + string_utils app, pytest
   suite, seeded bugs for repeatable tasks
4. `app/git_client.py`
5. `app/coding_backend.py` (TaskResult + ABC + factory) and
   `app/coding_backends/{native,opencode,aider}_backend.py`
6. `coding_run_report` publishing + message-logger `coding_backend_runs` table
   + `docs/sql/03_create_coding_backend_runs.sql`
7. `agent.py`: coder handler calls backend, tmux replay, payload threading
8. `agent.py`: tester real pytest execution (copy-to-tmp, parse results)
9. Worker configs `coder-native.yaml` (NYX-1), `coder-opencode.yaml` (OKO-2),
   `coder-aider.yaml` (ADA-3); tester config gains workspace map
10. Dockerfile (Node 18, opencode, aider venv, sandbox COPY, pytest) +
    docker-compose.yml (3 workers, 3 volumes, tester ro mounts)
11. Tests (`tests/test_git_client.py`, `test_coding_backend.py`, etc.)
12. Docs (`docs/coding_backend.md`, `docs/git_client.md`,
    `docs/sandbox_project.md`) + README + operator_commands update
13. Conventional commits throughout; memory update at end

## Queued questions / follow-ups for the user (DO NOT BLOCK — queue here)

- OpenCode/aider versions are installed unpinned (couldn't verify current
  versions offline tonight). Pin `OPENCODE_VERSION` / `AIDER_VERSION` build
  args once you can check.
- Ollama model quality: tool-following on `qwen2.5:14b` is untested for
  opencode's agentic loop; if opencode runs flail, try `qwen2.5-coder:14b+`.
- When your local git server is up: set `GIT_SERVER_URL` + per-worker tokens
  in `.env`; push/PR paths in `git_client.py` are stubbed ready.
- Portainer prod uses `/opt/virtualTubers/...` host mounts — new worker
  config mounts follow the same pattern; rebuild image + redeploy needed.
