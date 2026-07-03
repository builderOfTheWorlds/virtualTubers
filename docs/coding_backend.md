# coding_backend.py (+ coding_backends/)

## Overview

Swappable "actually write the code" layer for coder workers — the second
provider-switch in the codebase, mirroring `llm_client.py` one level up.
A worker config's `coding_backend.provider` selects which tool turns a
`task_assignment` into real file edits and a local git commit. Three
adapters implement the same interface so backends can be A/B-tested on
identical tasks (the whole point — see
`.claude/prompts/coding_backend_ab_test.md`):

| provider   | persona | what it is |
|---|---|---|
| `native`   | NYX-1   | our own minimal loop: the worker's `llm_client` + strict `FILE:` full-file-replacement blocks + `git_client` commit |
| `opencode` | OKO-2   | shells out to the OpenCode CLI (`opencode run`, non-interactive); adapter writes `opencode.json` and commits after |
| `aider`    | ADA-3   | shells out to `aider --message` one-shot; aider auto-commits, adapter sweeps any leftovers |
| `none`     | (default) | narration-only worker — legacy behavior, no code written |

Every adapter returns the same `TaskResult`; each run is also published on
the bus as a `coding_run_report` message, which `message-logger` unpacks
into the `coding_backend_runs` Postgres table for comparison queries.

## Signature

```python
class CodingBackendError(RuntimeError): ...

@dataclass
class TaskResult:
    backend: str; success: bool
    commit: str | None; committed: bool
    files_changed: int; insertions: int; deletions: int
    duration_s: float; output: str; error: str | None
    def to_payload(self) -> dict

def tail(text: str | None, limit: int = 4000) -> str
def run_cli(cmd: list, cwd: str, env: dict = None, timeout: int = 600) -> tuple[int, str]

class CodingBackend(ABC):
    def __init__(self, workspace, git, backend_config=None, timeout=600)
    def run_task(self, task: str) -> TaskResult   # abstract; must not raise

def build_coding_backend(config: dict, llm_client=None) -> CodingBackend | None
```

## Parameters

- `config` (dict, required) — a worker's parsed YAML config. Reads
  `config["coding_backend"]`: `provider` (env override `CODING_BACKEND`),
  `workspace` (default `/data/repo`, env override `WORKSPACE_PATH`),
  `timeout_s` (default 600), optional `model` (overrides `llm.model` for
  opencode/aider). Commit author comes from `agent.name`.
- `llm_client` — required for `provider: native` (it IS the brain there);
  unused by opencode/aider, which talk to Ollama themselves via
  `config["llm"]` (`base_url`, `model`).
- `task` (str, required) — the task text from the `task_assignment` payload.

## Return Value

- `build_coding_backend` — an adapter instance, or `None` for
  `provider: none`/absent (manager, tester, and the legacy coder keep their
  exact current narration-only behavior). Validates the provider name and
  the native/llm_client requirement BEFORE seeding the workspace.
- `run_task` — always a `TaskResult`, never an exception. `success` requires
  both a zero tool exit AND a new commit; `error` carries the failure reason
  otherwise. `output` is tail-truncated to 4000 chars.

## Dependencies

- `git_client.GitClient` (commits, diff stats) and
  `workspace_setup.ensure_workspace` (idempotent seed from `/app/sandbox`)
- Adapters are imported lazily — a worker only needs its selected tool
  installed. In the worker image: Node 18 + `opencode-ai` (npm, global) and
  `aider-chat` (isolated venv at `/opt/aider`, symlinked binary).

## Usage Examples

```python
# agent.py main(): built once at startup, degrades to narration-only on error
coding_backend = build_coding_backend(config, llm_client)

# handle_task_assignment: run first, narrate what actually happened after
result = coding_backend.run_task("Fix the bug in calculator.py: divide() must raise ValueError")
producer.send(build_message(worker_id, "broadcast", "coding_run_report",
                            {"task": task, **result.to_payload()}))
```

```sql
-- A/B comparison across backends (Postgres, table created by message-logger)
SELECT backend, count(*) FILTER (WHERE success) AS wins,
       avg(duration_s) AS avg_s, avg(insertions + deletions) AS avg_churn
FROM coding_backend_runs GROUP BY backend;
```

## Error Handling

- `CodingBackendError` — unknown provider, or `native` without an
  `llm_client`. Raised from `build_coding_backend` only; `agent.py` catches
  everything at startup and degrades to narration-only with a WARN.
- `run_task` never raises: tool timeouts return exit `-1` with partial
  output, missing binaries return `[tool not installed: ...]`, LLM/parse
  failures land in `TaskResult.error`. A failed run becomes a
  `clarification_request` blocker to the manager, not a fake commit.
- Native backend safety: model responses proposing absolute or `..` paths
  fail the run outright (nothing partially applied outside the workspace).

## Changelog

- v1.0.0 (2026-07-03) — initial version: TaskResult contract, factory with
  fail-fast validation, native/opencode/aider adapters, coding_run_report
  publishing.
