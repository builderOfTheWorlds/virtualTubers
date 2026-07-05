"""
aider_backend.py
Adapter shelling out to aider in one-shot mode (`aider --message`). Unlike
the other backends aider auto-commits its own work, so the adapter's
post-run commit is only a fallback sweep for anything aider left dirty
(e.g. when configured with auto-commits off, or a lint step touched files).

Aider is installed in its own venv (/opt/aider, see Dockerfile) to keep its
heavy dependency tree away from the agent runtime; the `aider` binary on
PATH is a symlink into that venv.
"""
import os

from coding_backend import CodingBackend, run_cli


class AiderBackend(CodingBackend):
    name = "aider"

    def __init__(self, workspace, git, backend_config, timeout, llm_config):
        super().__init__(workspace, git, backend_config, timeout)
        self.llm_config = llm_config or {}

    def _resolve_llm(self):
        base_url = (
            os.environ.get("LLM_BASE_URL")
            or self.llm_config.get("base_url", "http://localhost:11434")
        ).rstrip("/")
        model = self.backend_config.get("model") or self.llm_config.get("model", "qwen2.5:7b-instruct-q4_K_M")
        return base_url, model

    def run_task(self, task):
        import time
        started = time.monotonic()
        before_sha = self.git.head()
        base_url, model = self._resolve_llm()

        rc, output = run_cli(
            [
                "aider",
                "--message", task,
                # ollama_chat/ is aider's litellm route for Ollama's chat API
                "--model", f"ollama_chat/{model}",
                "--yes-always",       # non-interactive: auto-confirm prompts
                "--no-stream",
                "--no-check-update",
                "--no-analytics",
                "--no-show-model-warnings",
            ],
            cwd=self.workspace,
            env={"OLLAMA_API_BASE": base_url},
            timeout=self.timeout,
        )

        error = None if rc == 0 else f"aider exited {rc}"
        if not error:
            # Fallback sweep: aider normally auto-commits, but never assume.
            subject = task if len(task) <= 60 else task[:57] + "..."
            self.git.commit_all(f"feat: {subject}\n\nvia aider backend (fallback commit)")

        result = self._measure(before_sha, started, output, error=error)
        # Surface aider's own commit subject in the output for the stream/DB.
        if result.committed:
            last = self.git.log_last(1)
            if last:
                result.output = f"[last commit] {last[0]}\n{result.output}"
        return result
