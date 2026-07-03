# test_runner.py

## Overview

Real pytest execution for the tester worker — replaces the weighted-random
verdict stub in `agent.py` (which now survives only as a fallback for
unreachable workspaces). The tester's coder-workspace mounts are read-only
by design, and pytest needs to write caches, so the tree is copied to a
tmpdir and the suite runs there.

## Signature

```python
@dataclass
class TestRunResult:
    ran: bool                    # False: no verdict could be produced
    passed: bool = False
    exit_code: int | None = None
    failed_tests: list = []      # e.g. ["tests/test_calculator.py::test_divide_by_zero_raises"]
    summary: str = ""            # last ~15 lines of pytest output

def workspace_testable(workspace: str) -> bool
def run_pytest(workspace: str, timeout: int = 180) -> TestRunResult
```

## Parameters

- `workspace` (str, required) — the coder workspace mount, e.g.
  `/data/repos/coder-native` (resolved by `agent._resolve_workspace` from
  the `coder_id` in the `commit_notification` payload, with the tester
  config's `agent.workspaces` map taking precedence).
- `timeout` (int, optional, default 180) — seconds before the run is
  abandoned as no-verdict.

## Return Value

Only pytest exit codes 0 (all passed) and 1 (some failed) count as
verdicts. Exit 5 (no tests collected), 2-4, and timeouts return
`ran=False` — the tester treats a verdict-less run as a high-severity
`bug_report` (a suite that can't even run is the strongest failure signal),
while `workspace_testable() == False` (missing dir or no `.git`, i.e. the
legacy narration-only coder) falls back to the stub verdict instead.

## Dependencies

- `pytest` (added to the worker image's `requirements.txt` — the tester
  needs it at RUNTIME, not just in dev)
- stdlib only otherwise (`shutil`, `subprocess`, `tempfile`)

## Usage Examples

```python
if workspace_testable(workspace):
    run = run_pytest(workspace)
    if run.ran and not run.passed:
        severity = _severity_from_failures(run.failed_tests)  # count-based
        repro = f"pytest in {workspace}: {', '.join(run.failed_tests)}"
```

```python
# The canonical seeded-bug flow: NYX-1 hasn't fixed divide() yet
run = run_pytest("/data/repos/coder-native")
# -> ran=True, passed=False,
#    failed_tests=["tests/test_calculator.py::test_divide_by_zero_raises"]
```

## Error Handling

- Never raises in normal operation: timeouts and non-verdict exit codes are
  encoded in `TestRunResult(ran=False, summary=...)`.
- `.git`, `__pycache__`, `.pytest_cache`, `node_modules`, `.aider*`, and
  `opencode.json` are excluded from the copy — tool droppings must not
  affect the verdict.

## Changelog

- v1.0.0 (2026-07-03) — initial version.
