#!/usr/bin/env python3
"""
prompting.py
Prompt assembly and response parsing for the local-model implementation worker.

Two design choices here are load-bearing for getting usable output from a 30B
local model:

1. **Whole files, not diffs.** Local models produce unified diffs with wrong
   line numbers and drifting context far more often than they produce a valid
   whole file. The worker therefore always emits complete file bodies, and the
   harness overwrites rather than patches.
2. **Executable acceptance criteria.** The pytest file for the task is included
   in the prompt verbatim. The model is not asked to infer the contract from
   prose — the contract is the test, and the same test is what gates the result.
"""
import logging
import re

log = logging.getLogger("qwen_worker.prompting")

FILE_MARKER = "=== FILE:"

# `=== FILE: <path> ===` followed by a fenced block. The language tag is
# optional and ignored; the path is what matters. Non-greedy body so several
# files in one reply parse independently.
FILE_BLOCK_RE = re.compile(
    r"^===\s*FILE:\s*(?P<path>[^=\n]+?)\s*===\s*\n"
    r"```[^\n]*\n"
    r"(?P<body>.*?)"
    r"^```\s*$",
    re.MULTILINE | re.DOTALL,
)

SYSTEM_PROMPT = """\
You are an implementation worker on a Python project. You write complete, \
runnable Python modules that satisfy a provided pytest suite.

Rules you must follow exactly:

1. Output ONLY files, each in this exact format:

=== FILE: relative/path/to/file.py ===
```python
<the complete file body>
```

2. Emit the COMPLETE file body every time. Never emit a diff, a patch, an \
ellipsis, a "rest unchanged" comment, or a partial class. The file you emit \
replaces the file on disk in full.
3. Only emit files listed under TARGET FILES. Never emit the test file. Never \
emit files listed as read-only context.
4. The pytest suite provided is the specification. Your code must make it pass \
without modifying it.
5. Match the surrounding project's style: module docstring at the top \
explaining the module's purpose, snake_case names, standard-library imports \
preferred, and `logging` for diagnostics.
6. Do not add dependencies beyond what the context files already import.
7. Write no prose outside the file blocks. No preamble, no explanation, no \
summary afterwards.
"""


def _fence(text, language=""):
    """Wrap text in a fence that cannot be closed early by its own content."""
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return f"{ticks}{language}\n{text}\n{ticks}"


def build_user_prompt(spec, context_sources, test_sources, failure_feedback=None):
    """Assemble the user turn.

    spec              — parsed task spec dict (see specs/*.yaml)
    context_sources   — {repo_relative_path: file text} the model may READ
    test_sources      — {repo_relative_path: file text} the acceptance suite
    failure_feedback  — pytest output from the previous attempt, if retrying

    Ordering is deliberate: goal first, then the contract, then the tests, then
    the (largest) context, then the retry feedback last so the most recent and
    most actionable information sits nearest the model's generation point.
    """
    parts = []

    parts.append(f"# TASK: {spec['task_id']}\n\n{spec['goal'].strip()}")

    if spec.get("interface"):
        parts.append("## REQUIRED INTERFACE\n\n"
                     "Your implementation must expose exactly these names:\n\n"
                     + spec["interface"].strip())

    if spec.get("notes"):
        parts.append("## IMPLEMENTATION NOTES\n\n" + spec["notes"].strip())

    parts.append("## TARGET FILES\n\nEmit exactly these files, complete:\n"
                 + "\n".join(f"- {path}" for path in spec["target_files"]))

    if test_sources:
        blocks = "\n\n".join(
            f"### {path} (DO NOT EMIT THIS FILE — it is the spec)\n{_fence(text, 'python')}"
            for path, text in test_sources.items()
        )
        parts.append("## ACCEPTANCE TESTS\n\n"
                     "Your code must make these pass unmodified.\n\n" + blocks)

    if context_sources:
        blocks = "\n\n".join(
            f"### {path} (READ-ONLY CONTEXT — do not emit)\n{_fence(text, 'python')}"
            for path, text in context_sources.items()
        )
        parts.append("## EXISTING PROJECT CODE\n\n"
                     "Reuse these where relevant; do not rewrite them.\n\n" + blocks)

    if failure_feedback:
        parts.append("## PREVIOUS ATTEMPT FAILED\n\n"
                     "Your last answer did not pass. Fix the cause and emit the "
                     "complete corrected files again.\n\n"
                     + _fence(failure_feedback.strip(), "text"))

    prompt = "\n\n---\n\n".join(parts)
    log.debug("built user prompt: %d chars, %d context files, %d test files",
              len(prompt), len(context_sources), len(test_sources))
    return prompt


def parse_response(response, allowed_paths):
    """Extract {path: body} from a worker reply.

    allowed_paths gates what may be written — a local model will occasionally
    helpfully "fix" a test or a context file, and silently accepting that would
    let it rewrite its own acceptance criteria. Out-of-scope paths are dropped
    with a warning rather than raising, so one stray block does not throw away
    an otherwise good answer.

    Returns (files, rejected_paths).
    """
    allowed = set(allowed_paths)
    files, rejected = {}, []

    for match in FILE_BLOCK_RE.finditer(response):
        path = match.group("path").strip()
        body = match.group("body")
        if path not in allowed:
            log.warning("rejecting out-of-scope file from worker: %s", path)
            rejected.append(path)
            continue
        if not body.endswith("\n"):
            body += "\n"
        files[path] = body
        log.debug("parsed file %s (%d bytes)", path, len(body))

    if not files:
        marker_count = response.count(FILE_MARKER)
        log.error("no usable file blocks parsed (%d '%s' markers present, "
                  "%d chars of response)", marker_count, FILE_MARKER, len(response))

    return files, rejected


def missing_targets(files, target_files):
    """Which required target files the worker failed to emit."""
    return [path for path in target_files if path not in files]
