"""
native_backend.py
Our own minimal coding loop: send the whole (tiny) workspace to the worker's
existing llm_client and ask for full-file replacements in a strict FILE:
block format, then write + commit them via git_client.

Deliberately simple versus opencode/aider — no repo-map, no diff patching,
no multi-turn tool use. Full-file replacement is the most robust edit format
for small local models (nothing to mis-apply), and the sandbox workspace is
small enough to fit in one context window. That simplicity IS the baseline
being A/B-tested against the real tools.
"""
import re
from pathlib import Path

from coding_backend import CodingBackend, tail

# Workspace context caps — sandbox-sized by design. If a workspace outgrows
# these the native backend is the wrong tool and the run should show that
# (files silently omitted would corrupt the comparison, so omissions are
# listed in the prompt instead).
MAX_FILE_CHARS = 12000
MAX_TOTAL_CHARS = 48000
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".aider.tags.cache"}
SKIP_FILES = {"opencode.json"}

FILE_BLOCK_RE = re.compile(r"FILE:\s*(\S+)\s*\n```[a-zA-Z0-9_.-]*\n(.*?)```", re.DOTALL)

EDIT_SYSTEM_PROMPT = """You are a precise coding assistant. You will be shown a small project's \
files and a task. Reply with the COMPLETE new content of every file you create or change, and \
nothing else, using exactly this format for each file:

FILE: relative/path.py
```
<entire file content>
```

Rules:
- Output the ENTIRE file content for each changed file, not a diff or fragment.
- Only include files you are changing or creating. Do not include unchanged files.
- Paths are relative to the project root. Never use absolute paths or '..'.
- No commentary, no explanations outside the FILE blocks."""


class NativeBackend(CodingBackend):
    name = "native"

    def __init__(self, workspace, git, backend_config, timeout, llm_client):
        super().__init__(workspace, git, backend_config, timeout)
        self.llm_client = llm_client

    def _collect_files(self):
        """{relative_path: content} for the prompt, plus a list of paths that
        were skipped for size (surfaced to the model, never silent)."""
        files, skipped, total = {}, [], 0
        root = Path(self.workspace)
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts) or rel.name in SKIP_FILES:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary/unreadable: not editable text, not an error
            if len(content) > MAX_FILE_CHARS or total + len(content) > MAX_TOTAL_CHARS:
                skipped.append(str(rel))
                continue
            files[str(rel).replace("\\", "/")] = content
            total += len(content)
        return files, skipped

    def _build_user_prompt(self, task, files, skipped):
        parts = [f"TASK: {task}\n\nPROJECT FILES:"]
        for rel, content in files.items():
            parts.append(f"\nFILE: {rel}\n```\n{content}\n```")
        if skipped:
            parts.append(f"\n(omitted for size, do not modify: {', '.join(skipped)})")
        return "\n".join(parts)

    def _parse_and_write(self, response):
        """Parse FILE blocks and write them. Returns (written_paths, error).
        Unsafe paths fail the whole run — a model emitting '../' is broken
        output, not something to partially apply."""
        blocks = FILE_BLOCK_RE.findall(response)
        if not blocks:
            return [], "no FILE blocks found in model response"
        root = Path(self.workspace).resolve()
        written = []
        for rel, content in blocks:
            rel = rel.strip()
            target = (root / rel).resolve()
            if Path(rel).is_absolute() or not str(target).startswith(str(root)):
                return written, f"unsafe path in model response: {rel!r}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(rel)
        return written, None

    def run_task(self, task):
        import time
        started = time.monotonic()
        before_sha = self.git.head()
        files, skipped = self._collect_files()
        user_prompt = self._build_user_prompt(task, files, skipped)
        transcript = []

        # One retry with parse feedback: small local models frequently miss a
        # strict format on the first try but correct when shown the failure.
        error = None
        for attempt in (1, 2):
            try:
                response = self.llm_client.complete(
                    EDIT_SYSTEM_PROMPT, [{"role": "user", "content": user_prompt}]
                )
            except Exception as exc:
                error = f"LLM call failed: {exc}"
                transcript.append(error)
                break
            transcript.append(f"--- attempt {attempt} response ---\n{response}")
            written, error = self._parse_and_write(response)
            if not error:
                transcript.append(f"wrote: {', '.join(written)}")
                break
            user_prompt += (
                f"\n\nYour previous reply could not be applied ({error}). "
                "Reply again using EXACTLY the FILE block format."
            )

        if not error:
            subject = task if len(task) <= 60 else task[:57] + "..."
            self.git.commit_all(f"feat: {subject}\n\nvia native coding backend")

        return self._measure(before_sha, started, tail("\n".join(transcript)), error=error)
