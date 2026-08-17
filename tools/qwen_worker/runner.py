#!/usr/bin/env python3
"""
runner.py
Entry point for the local-model implementation worker.

Drives `qwen3-coder:30b` (via local Ollama) through one task spec: assemble the
prompt, generate whole files, stage them, run the task's pytest suite in a
throwaway sandbox, and retry with the failure output fed back — up to N
attempts. Nothing is written to the working tree; promotion is a separate,
explicit command run after review.

Usage:
    python tools/qwen_worker/runner.py preflight
    python tools/qwen_worker/runner.py run tools/qwen_worker/specs/<task>.yaml
    python tools/qwen_worker/runner.py promote tools/qwen_worker/specs/<task>.yaml
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

import ollama_client  # noqa: E402
import prompting  # noqa: E402
import sandbox  # noqa: E402

log = logging.getLogger("qwen_worker.runner")

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_SPEC_KEYS = ("task_id", "goal", "target_files", "test_files")


# ── Loki (best-effort, per CLAUDE.md) ─────────────────────────────────────────
def _push_loki(operation, message, level="info", extra=None):
    """Ship an operational log line to the shared Loki stack.

    Fire-and-forget by contract: a missing projectManager checkout or a down
    Loki must never affect a worker run.
    """
    try:
        loki_src = REPO_ROOT.parent / "projectManager" / "src"
        if str(loki_src) not in sys.path:
            sys.path.insert(0, str(loki_src))
        import loki_push

        loki_push.push({"app": "virtualtubers", "operation": operation},
                       message, level=level, extra=extra or {})
    except Exception as exc:  # noqa: BLE001 - logging must never break the run
        log.debug("loki push skipped: %s", exc)


def setup_logging(verbose=False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


# ── spec loading ──────────────────────────────────────────────────────────────
def load_spec(spec_path):
    """Parse and validate a task spec.

    Fails loudly on a malformed spec rather than sending a half-built prompt to
    a model that will take minutes to answer.
    """
    spec_path = Path(spec_path)
    log.debug("loading spec %s", spec_path)

    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.error("cannot read spec %s: %s", spec_path, exc)
        raise SystemExit(f"invalid spec {spec_path}: {exc}") from exc

    if not isinstance(spec, dict):
        raise SystemExit(f"invalid spec {spec_path}: expected a mapping")

    missing = [key for key in REQUIRED_SPEC_KEYS if not spec.get(key)]
    if missing:
        raise SystemExit(f"invalid spec {spec_path}: missing {', '.join(missing)}")

    spec.setdefault("context_files", [])
    spec.setdefault("interface", "")
    spec.setdefault("notes", "")
    log.info("spec %s: %d target file(s), %d context file(s), %d test file(s)",
             spec["task_id"], len(spec["target_files"]),
             len(spec["context_files"]), len(spec["test_files"]))
    return spec


def read_sources(paths, label):
    """Read repo-relative files into {path: text}, erroring on any missing one."""
    sources = {}
    for rel_path in paths:
        full_path = REPO_ROOT / rel_path
        if not full_path.is_file():
            raise SystemExit(f"{label} file not found: {rel_path}")
        sources[rel_path] = full_path.read_text(encoding="utf-8")
        log.debug("read %s file %s (%d bytes)", label, rel_path, len(sources[rel_path]))
    return sources


# ── commands ──────────────────────────────────────────────────────────────────
def cmd_preflight(args):
    ok, detail = ollama_client.is_available(base_url=args.base_url, model=args.model)
    print(f"{'OK  ' if ok else 'FAIL'} {detail}")
    print(f"{'OK  ' if (REPO_ROOT / '.venv/bin/python').exists() else 'FAIL'} "
          f".venv python at {REPO_ROOT / '.venv/bin/python'}")
    return 0 if ok else 1


def cmd_run(args):
    spec = load_spec(args.spec)
    task_id = spec["task_id"]

    ok, detail = ollama_client.is_available(base_url=args.base_url, model=args.model)
    if not ok:
        log.error("preflight failed: %s", detail)
        raise SystemExit(detail)

    context_sources = read_sources(spec["context_files"], "context")
    test_sources = read_sources(spec["test_files"], "test")

    system_prompt = prompting.SYSTEM_PROMPT
    feedback = None
    started = time.monotonic()

    for attempt in range(1, args.attempts + 1):
        log.info("── attempt %d/%d for task %s ──", attempt, args.attempts, task_id)

        user_prompt = prompting.build_user_prompt(
            spec, context_sources, test_sources, failure_feedback=feedback)

        try:
            response = ollama_client.chat(
                system_prompt, user_prompt,
                model=args.model, base_url=args.base_url,
                num_ctx=args.num_ctx, timeout=args.timeout)
        except ollama_client.OllamaError as exc:
            log.error("attempt %d: model call failed: %s", attempt, exc)
            feedback = f"Your previous call failed at the transport level: {exc}"
            continue

        _write_transcript(task_id, attempt, user_prompt, response)

        files, rejected = prompting.parse_response(response, spec["target_files"])
        if not files:
            log.error("attempt %d: no usable file blocks in reply", attempt)
            feedback = ("Your reply contained no parsable file blocks. Emit each "
                        "file exactly as:\n=== FILE: path ===\n```python\n...\n```")
            continue

        missing = prompting.missing_targets(files, spec["target_files"])
        if missing:
            log.warning("attempt %d: worker omitted %s", attempt, ", ".join(missing))
            feedback = (f"You did not emit these required target files: "
                        f"{', '.join(missing)}. Emit every target file, complete.")
            continue

        staged = sandbox.write_staged(REPO_ROOT, task_id, files)
        passed, output = sandbox.run_pytest(
            REPO_ROOT, staged, spec["test_files"],
            keep=args.keep_sandbox, timeout=args.test_timeout)

        if passed:
            elapsed = time.monotonic() - started
            log.info("task %s PASSED on attempt %d after %.1fs", task_id, attempt, elapsed)
            _push_loki("qwen_worker", f"task {task_id} passed on attempt {attempt}",
                       level="info",
                       extra={"task_id": task_id, "attempt": attempt,
                              "elapsed_s": round(elapsed, 1),
                              "files": list(files)})
            print(f"\nPASS  {task_id} (attempt {attempt}, {elapsed:.0f}s)")
            print(f"Staged at: {sandbox.staging_dir(REPO_ROOT, task_id)}")
            for rel_path in files:
                print(f"  {rel_path}")
            print(f"\nReview, then: python {Path(__file__).relative_to(REPO_ROOT)} "
                  f"promote {args.spec}")
            return 0

        log.warning("attempt %d: acceptance tests failed", attempt)
        if rejected:
            log.warning("attempt %d also emitted out-of-scope files: %s",
                        attempt, ", ".join(rejected))
        feedback = sandbox.tail(output)

    elapsed = time.monotonic() - started
    log.error("task %s exhausted %d attempts after %.1fs", task_id, args.attempts, elapsed)
    _push_loki("qwen_worker", f"task {task_id} failed after {args.attempts} attempts",
               level="error",
               extra={"task_id": task_id, "attempts": args.attempts,
                      "elapsed_s": round(elapsed, 1)})
    print(f"\nFAIL  {task_id} after {args.attempts} attempts ({elapsed:.0f}s)")
    print(f"Last staged output: {sandbox.staging_dir(REPO_ROOT, task_id)}")
    print(f"Transcripts: {_transcript_dir(task_id)}")
    return 1


def cmd_promote(args):
    spec = load_spec(args.spec)
    promoted = sandbox.promote(REPO_ROOT, spec["task_id"], spec["target_files"])
    print(f"Promoted {len(promoted)} file(s) into the working tree:")
    for rel_path in promoted:
        print(f"  {rel_path}")
    _push_loki("qwen_worker", f"promoted task {spec['task_id']}",
               level="info", extra={"task_id": spec["task_id"], "files": promoted})
    return 0


# ── transcripts ───────────────────────────────────────────────────────────────
def _transcript_dir(task_id):
    return REPO_ROOT / ".qwen_staging" / task_id / "_transcripts"


def _write_transcript(task_id, attempt, user_prompt, response):
    """Persist every model turn — the only record of what the worker was told.

    Best-effort: an unwritable transcript directory must not abort a run that
    otherwise succeeded.
    """
    try:
        directory = _transcript_dir(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"attempt-{attempt:02d}.json").write_text(
            json.dumps({"attempt": attempt, "prompt": user_prompt,
                        "response": response}, indent=2),
            encoding="utf-8")
        log.debug("wrote transcript for attempt %d", attempt)
    except OSError as exc:
        log.warning("could not write transcript for attempt %d: %s", attempt, exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local-model implementation worker")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--model", default=ollama_client.DEFAULT_MODEL)
    parser.add_argument("--base-url", default=ollama_client.DEFAULT_BASE_URL)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight", help="check Ollama and the model are reachable")

    run = sub.add_parser("run", help="generate and verify one task")
    run.add_argument("spec")
    run.add_argument("--attempts", type=int, default=3)
    run.add_argument("--num-ctx", type=int, default=ollama_client.DEFAULT_NUM_CTX)
    run.add_argument("--timeout", type=int, default=ollama_client.DEFAULT_TIMEOUT_S)
    run.add_argument("--test-timeout", type=int, default=600)
    run.add_argument("--keep-sandbox", action="store_true")

    promote = sub.add_parser("promote", help="copy reviewed staged files into the tree")
    promote.add_argument("spec")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    handlers = {"preflight": cmd_preflight, "run": cmd_run, "promote": cmd_promote}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
