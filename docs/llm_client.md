# llm_client.py

## Overview

Provider-switchable LLM client used by `app/agent.py`'s `think()` step. Reads
a worker's `llm.provider` config value and returns an object with a single
`complete(system_prompt, messages)` method, so the agent loop doesn't need to
know whether it's talking to a local Ollama instance or the Claude API.

## Signature

```python
class LLMError(RuntimeError): ...

class OllamaClient:
    def __init__(self, base_url: str, model: str, temperature: float, max_tokens: int)
    def complete(self, system_prompt: str, messages: list[dict]) -> str

class ClaudeClient:
    def __init__(self, model: str, max_tokens: int)
    def complete(self, system_prompt: str, messages: list[dict]) -> str

def build_llm_client(config: dict) -> OllamaClient | ClaudeClient
```

## Parameters

- `config` (dict, required) — a worker's parsed YAML config (as returned by
  `message_bus.load_worker_config`). Reads `config["llm"]`: `provider`
  (`"ollama"` | `"claude"`, default `"ollama"`), `base_url`, `model`,
  `temperature`, `max_tokens`.
- `base_url` (str, required for `OllamaClient`) — Ollama server base URL.
- `model` (str, required) — for Ollama, any locally pulled model name; for
  Claude, a model ID (e.g. `claude-opus-4-8`).
- `temperature` (float, `OllamaClient` only) — sampling temperature. Not sent
  to the Claude API: Claude's current-generation models (Opus 4.7/4.8 and
  later) reject non-default sampling parameters with a 400.
- `max_tokens` (int, required) — hard cap on generated tokens.
- `system_prompt` (str, required) — the worker's persona/instructions, from
  `agent.system_prompt` in the worker config.
- `messages` (list[dict], required) — chat turns as `{"role": ..., "content": ...}`.

## Return Value

- `build_llm_client` — an `OllamaClient` or `ClaudeClient` instance, selected
  by `config["llm"]["provider"]` (env var `LLM_PROVIDER` overrides). If
  `provider: claude` but `model` isn't a `claude-`-prefixed ID (e.g. it's
  still set to an Ollama model name like `mistral`), defaults to
  `claude-opus-4-8` rather than sending an invalid model ID.
- `complete` — the model's text response as a `str`.

## Dependencies

- `httpx` (`OllamaClient` — plain HTTP POST to `{base_url}/api/chat`)
- `anthropic` (`ClaudeClient` — official SDK, `client.messages.create`)
- `ClaudeClient` resolves credentials from the `ANTHROPIC_API_KEY` environment
  variable via the SDK's default client construction — never reads a key from
  the worker config file.

## Usage Examples

```python
from message_bus import load_worker_config
from llm_client import build_llm_client

config = load_worker_config("/config/worker.yaml")
llm = build_llm_client(config)

narration = llm.complete(
    config["agent"]["system_prompt"],
    [{"role": "user", "content": "You've been assigned: fix the login bug."}],
)
```

```python
# Force a provider regardless of the worker's config, e.g. for local testing
import os
os.environ["LLM_PROVIDER"] = "ollama"
llm = build_llm_client({"llm": {"model": "llama3"}})
```

## Error Handling

- `build_llm_client` raises `LLMError` if `llm.provider` is set to anything
  other than `"ollama"` or `"claude"`.
- `OllamaClient.complete` raises `httpx.HTTPStatusError` on a non-2xx
  response and `httpx.ConnectError`/`httpx.TimeoutException` if the Ollama
  server is unreachable — both propagate uncaught; `app/agent.py` catches
  them at the call site and posts a `clarification_request` instead of
  crashing the agent loop.
- `ClaudeClient.complete` raises the `anthropic` SDK's typed exceptions
  (`AuthenticationError` if `ANTHROPIC_API_KEY` is unset/invalid,
  `RateLimitError`, `APIStatusError`, etc.) — same uncaught-and-handled-by-caller
  pattern as above.

## Changelog

- v1.0.0 (2026-07-01) — Initial version: provider-switchable Ollama/Claude
  chat completion client.
