#!/usr/bin/env python3
"""
sandbox.py
Staging and sandboxed verification for local-model worker output.

The worker's files never land in the working tree. Each attempt is written to
a staging directory, then verified inside a throwaway copy of the repo's Python
slice (app/ tests/ config/ campaigns/ + pytest.ini). Only after a human review
does anything get promoted, and promotion is an explicit separate step.

The copied slice is ~2 MB, so a full sandbox build costs milliseconds — cheap
enough to do on every attempt and worth far more than the alternative of
letting an unreviewed local model write into a live tree.
"""
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("qwen_worker.sandbox")

# Directories copied into the verification sandbox. Everything the test suite
# imports or reads must be here; .venv, .git and node_modules must not.
SANDBOX_DIRS = ("app", "tests", "config", "campaigns", "tools")
SANDBOX_FILES = ("pytest.ini",)

# Never copied — large, irrelevant, or actively harmful to duplicate.
COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".pytest_cache", ".git", ".venv",
    "node_modules", "*.wav", "*.mp4", ".qwen_staging",
)


def staging_dir(repo_root, task_id):
    """Where this task's attempts are written. Repo-local and gitignored."""
    return Path(repo_root) / ".qwen_staging" / task_id


def write_staged(repo_root, task_id, files):
    """Write {repo_relative_path: body} into the task's staging directory.

    Returns {repo_relative_path: absolute staged Path}. Parent directories are
    created as needed so a worker can introduce a new package directory.
    """
    root = staging_dir(repo_root, task_id)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    staged = {}
    for rel_path, body in files.items():
        destination = root / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
        staged[rel_path] = destination
        log.debug("staged %s -> %s (%d bytes)", rel_path, destination, len(body))

    log.info("staged %d file(s) for task %s under %s", len(staged), task_id, root)
    return staged


def build_sandbox(repo_root, staged_files, extra_files=None):
    """Copy the repo's Python slice to a temp dir and overlay staged output.

    extra_files — {repo_relative_path: text} written in addition to the staged
    files. Used for the acceptance test file when it does not yet exist in the
    working tree, so a task can be verified before its tests are committed.

    Returns the sandbox root Path. The caller owns cleanup (see run_pytest,
    which does it for the common case).
    """
    repo_root = Path(repo_root)
    sandbox = Path(tempfile.mkdtemp(prefix="qwen-sandbox-"))

    for name in SANDBOX_DIRS:
        source = repo_root / name
        if source.is_dir():
            shutil.copytree(source, sandbox / name, ignore=COPY_IGNORE)

    for name in SANDBOX_FILES:
        source = repo_root / name
        if source.is_file():
            shutil.copy2(source, sandbox / name)

    for rel_path, staged_path in (staged_files or {}).items():
        destination = sandbox / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_path, destination)
        log.debug("overlaid staged %s into sandbox", rel_path)

    for rel_path, text in (extra_files or {}).items():
        destination = sandbox / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        log.debug("overlaid extra %s into sandbox", rel_path)

    log.debug("sandbox built at %s", sandbox)
    return sandbox


def run_pytest(repo_root, staged_files, test_paths, extra_files=None,
               python_bin=None, timeout=600, keep=False):
    """Run the acceptance tests against staged output in a throwaway sandbox.

    Returns (passed, output). Output is combined stdout+stderr, tail-trimmed by
    the caller before being fed back to the model.
    """
    python_bin = python_bin or str(Path(repo_root) / ".venv" / "bin" / "python")
    sandbox = build_sandbox(repo_root, staged_files, extra_files)

    # --maxfail/--tb keep the feedback useful: a broad failure otherwise fills
    # the tail with 40+ bare test names and pushes every traceback off the top,
    # so the model retries blind against the summary instead of the cause.
    command = [python_bin, "-m", "pytest", *test_paths, "-q", "--no-header",
               "--maxfail=3", "--tb=short", "-p", "no:cacheprovider"]
    log.info("running acceptance tests: %s", " ".join(test_paths))

    try:
        completed = subprocess.run(
            command,
            cwd=sandbox,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        output = completed.stdout + completed.stderr
        passed = completed.returncode == 0
        log.info("acceptance tests %s (exit %d)",
                 "PASSED" if passed else "FAILED", completed.returncode)
    except subprocess.TimeoutExpired as exc:
        output = f"pytest timed out after {timeout}s\n{exc.stdout or ''}{exc.stderr or ''}"
        passed = False
        log.error("acceptance tests timed out after %ds", timeout)
    finally:
        if keep:
            log.info("sandbox retained for inspection: %s", sandbox)
        else:
            shutil.rmtree(sandbox, ignore_errors=True)

    return passed, output


def tail(text, max_chars=6000):
    """Trim pytest output for feedback — the failure summary is at the end."""
    if len(text) <= max_chars:
        return text
    return "...(truncated)...\n" + text[-max_chars:]


def promote(repo_root, task_id, files):
    """Copy reviewed staged files into the working tree.

    Deliberately separate from the run loop: the harness never calls this
    itself. It is invoked explicitly (`runner.py --promote`) after a human has
    read the staged diff.
    """
    repo_root = Path(repo_root)
    root = staging_dir(repo_root, task_id)
    promoted = []

    for rel_path in files:
        source = root / rel_path
        if not source.is_file():
            raise FileNotFoundError(f"nothing staged at {source}")
        destination = repo_root / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        promoted.append(rel_path)
        log.info("promoted %s into working tree", rel_path)

    return promoted
