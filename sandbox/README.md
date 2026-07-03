# Sandbox Project — Agent Workspace

This is the disposable project the coder agents actually write code against.
It is copied into each coder worker's workspace volume (`/data/repo`) by
`app/workspace_setup.py` on first startup (git init + initial commit), so
every coding backend (`native` | `opencode` | `aider`) starts from an
identical tree and results are comparable.

## Layout

- `calculator.py` — small arithmetic module. **Ships with one seeded bug**:
  `divide()` returns `0` on division by zero instead of raising
  `ValueError`, so `tests/test_calculator.py::test_divide_by_zero_raises`
  fails out of the box. Fixing it turns the suite fully green.
- `string_utils.py` — small string helpers, all tests passing.
- `tests/` — pytest suite. The tester agent runs this for real and reports
  `test_passed` / `bug_report` based on the actual exit code.

## Suggested task_assignment payloads (send via message-api)

```bash
# The seeded bug — the canonical first A/B task:
curl -X POST http://localhost:8090/messages -H "Content-Type: application/json" \
  -d '{"to": "coder-native", "type": "task_assignment", "payload": {"task": "Fix the bug in calculator.py: divide() must raise ValueError on division by zero instead of returning 0. Do not change the tests."}}'

# Feature tasks (suite stays green, agents add code + tests):
#  - "Add a median(values) function to calculator.py with tests in tests/test_calculator.py"
#  - "Add a slugify(text) function to string_utils.py that lowercases, strips, and joins words with hyphens; add tests"
#  - "Add a clamp(value, low, high) function to calculator.py with tests"
```

Swap `"to"` between `coder-native`, `coder-opencode`, and `coder-aider` to
run the same task against each backend in parallel; compare in Postgres:

```sql
SELECT backend, success, files_changed, insertions, deletions, duration_s
FROM coding_backend_runs ORDER BY reported_at DESC;
```
