"""
coding_backend.py
Swappable "actually write the code" layer for coder workers, mirroring
llm_client.py's provider pattern one level up: a worker config's
`coding_backend.provider` (env override CODING_BACKEND) selects which tool
turns a task_assignment into real file edits + a local git commit:

    native   — our own minimal loop (llm_client + full-file replacement edits)
    opencode — shells out to `opencode run` (OpenCode CLI)
    aider    — shells out to `aider --message` (auto-commits itself)
    none     — narration-only worker, no code written (the default)

Every backend returns the same TaskResult so agent.py, the Kafka
coding_run_report message, and the Postgres coding_backend_runs table never
care which tool did the work — that's what makes A/B comparison fair.
"""
import os
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict

from git_client import GitClient

# Tool output kept in TaskResult/bus payloads is tail-truncated: the end of a
# CLI run (test summary, error, commit line) is the diagnostic part, and bus
# messages/DB rows shouldn't carry megabytes of scrollback.
OUTPUT_TAIL_CHARS = 4000
DEFAULT_TIMEOUT_S = 600


class CodingBackendError(RuntimeError):
    pass


@dataclass
class TaskResult:
    backend: str
    success: bool
    commit: str = None          # HEAD sha after the run (None: nothing committed)
    committed: bool = False
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    duration_s: float = 0.0
    output: str = ""
    error: str = None

    def to_payload(self):
        """Bus-safe dict for coding_run_report / commit_notification."""
        return asdict(self)


def tail(text, limit=OUTPUT_TAIL_CHARS):
    if text is None:
        return ""
    return text if len(text) <= limit else "…" + text[-limit:]


def run_cli(cmd, cwd, env=None, timeout=DEFAULT_TIMEOUT_S):
    """Run a coding tool CLI, capturing combined output. Returns
    (returncode, output); a timeout returns (-1, partial output) rather than
    raising — a hung tool is a failed run, not a crashed worker."""
    merged_env = {**os.environ, **(env or {})}
    try:
        result = subprocess.run(
            cmd, cwd=cwd, env=merged_env,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return -1, tail(partial) + f"\n[timeout after {timeout}s]"
    except FileNotFoundError as exc:
        return -1, f"[tool not installed: {exc}]"
    return result.returncode, tail((result.stdout or "") + (result.stderr or ""))


class CodingBackend(ABC):
    """One instance per worker, bound to that worker's workspace + git."""

    name = "abstract"

    def __init__(self, workspace, git, backend_config=None, timeout=DEFAULT_TIMEOUT_S):
        self.workspace = workspace
        self.git = git
        self.backend_config = backend_config or {}
        self.timeout = timeout

    @abstractmethod
    def run_task(self, task):
        """Attempt `task` against the workspace. Must return a TaskResult and
        must not raise — tool failure is data (success=False), not a worker
        crash."""

    def _measure(self, before_sha, started, output, error=None, committed=False):
        """Common TaskResult assembly from git state around a run."""
        after_sha = self.git.head()
        made_commit = bool(after_sha) and after_sha != before_sha
        files, ins, dels = self.git.diff_stats(before_sha, after_sha) if made_commit else (0, 0, 0)
        return TaskResult(
            backend=self.name,
            success=error is None and made_commit,
            commit=after_sha if made_commit else None,
            committed=made_commit or committed,
            files_changed=files,
            insertions=ins,
            deletions=dels,
            duration_s=round(time.monotonic() - started, 2),
            output=tail(output),
            error=error,
        )


def build_coding_backend(config, llm_client=None):
    """Factory mirroring build_llm_client: reads `coding_backend` from the
    worker config, seeds the workspace, and returns the adapter — or None
    for provider 'none'/absent (narration-only workers: manager, tester,
    and the legacy coder keep exactly their current behavior)."""
    from workspace_setup import ensure_workspace  # local import: avoid cycle in tests

    cb_config = config.get("coding_backend", {}) or {}
    provider = os.environ.get("CODING_BACKEND") or cb_config.get("provider", "none")
    if provider in (None, "", "none"):
        return None

    # Validate BEFORE touching the filesystem — a config typo must fail fast,
    # not after seeding a workspace volume it never needed.
    if provider not in ("native", "opencode", "aider"):
        raise CodingBackendError(
            f"unknown coding_backend.provider: {provider!r} "
            "(expected 'native', 'opencode', 'aider', or 'none')"
        )
    if provider == "native" and llm_client is None:
        raise CodingBackendError("native coding backend requires an LLM client")

    workspace = os.environ.get("WORKSPACE_PATH") or cb_config.get("workspace", "/data/repo")
    author = config.get("agent", {}).get("name") or cb_config.get("author", "coder")
    timeout = cb_config.get("timeout_s", DEFAULT_TIMEOUT_S)

    git = ensure_workspace(workspace, author)

    if provider == "native":
        from coding_backends.native_backend import NativeBackend
        return NativeBackend(workspace, git, cb_config, timeout, llm_client=llm_client)
    if provider == "opencode":
        from coding_backends.opencode_backend import OpenCodeBackend
        return OpenCodeBackend(workspace, git, cb_config, timeout, llm_config=config.get("llm", {}))
    from coding_backends.aider_backend import AiderBackend
    return AiderBackend(workspace, git, cb_config, timeout, llm_config=config.get("llm", {}))
