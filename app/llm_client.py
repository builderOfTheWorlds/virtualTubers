"""
llm_client.py
Provider-switchable LLM client for the agent loop. Reads `llm.provider` from
a worker's config and returns a client with a single `complete(system_prompt,
messages)` method — callers don't need to know whether they're talking to a
local Ollama instance or the Claude API.
"""
import os

import anthropic
import httpx


class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url, model, temperature, max_tokens):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, system_prompt, messages):
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": system_prompt}] + messages,
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                },
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]


class ClaudeClient:
    def __init__(self, model, max_tokens):
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    def complete(self, system_prompt, messages):
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=messages,
        )
        return "".join(block.text for block in response.content if block.type == "text")


def build_llm_client(config):
    llm_config = config.get("llm", {})
    provider = os.environ.get("LLM_PROVIDER") or llm_config.get("provider", "ollama")
    max_tokens = llm_config.get("max_tokens", 1024)

    if provider == "claude":
        model = llm_config.get("model", "")
        if not model.startswith("claude-"):
            model = "claude-opus-4-8"
        return ClaudeClient(model, max_tokens)

    if provider != "ollama":
        raise LLMError(f"unknown llm.provider: {provider!r} (expected 'ollama' or 'claude')")

    base_url = os.environ.get("LLM_BASE_URL") or llm_config.get("base_url", "http://localhost:11434")
    model = llm_config.get("model", "mistral")
    temperature = llm_config.get("temperature", 0.7)
    return OllamaClient(base_url, model, temperature, max_tokens)
