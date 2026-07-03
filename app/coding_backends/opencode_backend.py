"""
opencode_backend.py
Adapter shelling out to the OpenCode CLI (`opencode run`) in non-interactive
mode. OpenCode edits files but does not commit, so the adapter commits after
a successful run — keeping commit authorship/messages consistent with the
other backends via git_client.

The adapter writes an opencode.json into the workspace on every run, derived
from the worker's own `llm` config (Ollama exposed as an OpenAI-compatible
provider at <base_url>/v1) — config-driven like everything else, no
hand-maintained tool config drifting from the worker config.
"""
import json
import os
from pathlib import Path

from coding_backend import CodingBackend, run_cli


class OpenCodeBackend(CodingBackend):
    name = "opencode"

    def __init__(self, workspace, git, backend_config, timeout, llm_config):
        super().__init__(workspace, git, backend_config, timeout)
        self.llm_config = llm_config or {}

    def _resolve_llm(self):
        base_url = (
            os.environ.get("LLM_BASE_URL")
            or self.llm_config.get("base_url", "http://localhost:11434")
        ).rstrip("/")
        model = self.backend_config.get("model") or self.llm_config.get("model", "qwen2.5:14b")
        return base_url, model

    def _write_config(self):
        """opencode.json in the workspace root (highest-precedence project
        config). Ollama speaks the OpenAI-compatible API at /v1 — NOT the
        native /api endpoint."""
        base_url, model = self._resolve_llm()
        config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "ollama": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Ollama (local)",
                    "options": {"baseURL": f"{base_url}/v1"},
                    "models": {model: {"name": model}},
                }
            },
            "model": f"ollama/{model}",
        }
        path = Path(self.workspace) / "opencode.json"
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return model

    def run_task(self, task):
        import time
        started = time.monotonic()
        before_sha = self.git.head()

        model = self._write_config()
        rc, output = run_cli(
            ["opencode", "run", "--model", f"ollama/{model}", task],
            cwd=self.workspace,
            timeout=self.timeout,
        )

        error = None if rc == 0 else f"opencode exited {rc}"
        if not error:
            subject = task if len(task) <= 60 else task[:57] + "..."
            self.git.commit_all(f"feat: {subject}\n\nvia opencode backend")

        return self._measure(before_sha, started, output, error=error)
