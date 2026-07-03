"""
test_runner.py
Real pytest execution for the tester worker, replacing the weighted-random
outcome stub. The tester's workspace mounts are read-only (it must never be
able to modify a coder's work), and pytest needs to write caches — so the
tree is copied to a tmpdir and the suite runs there.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", ".pytest_cache", "node_modules", ".aider*", "opencode.json"
)
FAILED_LINE_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
DEFAULT_TIMEOUT_S = 180

# pytest exit codes that mean "the run itself worked": 0 all passed, 1 some
# tests failed. Everything else (2 interrupted, 3 internal error, 4 usage
# error, 5 no tests collected) is a broken run, not a verdict.
VERDICT_EXIT_CODES = (0, 1)


@dataclass
class TestRunResult:
    __test__ = False          # tell pytest this isn't a test class to collect

    ran: bool                 # False: suite could not produce a verdict
    passed: bool = False
    exit_code: int = None
    failed_tests: list = field(default_factory=list)
    summary: str = ""         # tail of pytest output (the summary lines)


def workspace_testable(workspace):
    """A workspace is testable when it exists and has been seeded (has .git).
    The legacy narration-only coder never seeds its volume, so its empty
    workspace falls back to the stub verdict rather than failing runs."""
    ws = Path(workspace)
    return ws.is_dir() and (ws / ".git").exists()


def run_pytest(workspace, timeout=DEFAULT_TIMEOUT_S):
    """Copy `workspace` to a tmpdir and run pytest -q there. Timeouts and
    non-verdict exit codes return ran=False — the caller decides what a
    broken run means, this module only reports."""
    with tempfile.TemporaryDirectory(prefix="tester-run-") as tmp:
        target = Path(tmp) / "workspace"
        shutil.copytree(workspace, target, ignore=COPY_IGNORE)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--color=no"],
                cwd=target, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return TestRunResult(ran=False, summary=f"pytest timed out after {timeout}s")

        output = (result.stdout or "") + (result.stderr or "")
        summary = "\n".join(output.strip().splitlines()[-15:])
        if result.returncode not in VERDICT_EXIT_CODES:
            return TestRunResult(
                ran=False, exit_code=result.returncode,
                summary=f"pytest exited {result.returncode} (no verdict)\n{summary}",
            )
        return TestRunResult(
            ran=True,
            passed=result.returncode == 0,
            exit_code=result.returncode,
            failed_tests=FAILED_LINE_RE.findall(output),
            summary=summary,
        )
