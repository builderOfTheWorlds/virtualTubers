"""
Pooled, timeout-configurable Ollama client for concurrent 3-layer generation.

This module provides a subclass of OllamaClient that fixes three problems
with the live avatar app's client in app/llm_client.py:

1. HARDCODED 120s TIMEOUT. The parent calls httpx.post(..., timeout=120).
   Batching raises total throughput BY RAISING PER-REQUEST LATENCY: when
   eight requests are in flight against one model instance, a single request
   that took 65s alone can take several times that. Every one of those crosses
   120s and dies. The timeout must come from config.

2. NO CONNECTION POOLING. It calls the MODULE-LEVEL httpx.post, which builds
   and tears down a connection for every call. This run makes roughly 14,300 calls.

3. NO num_ctx. The context length drives the per-slot KV cache, which is the
   memory lever that decides how many requests fit in parallel. It must be
   settable per model profile.

AND THE FAILURE IS SILENT. LLMImproviser.generate_scene (the caller) wraps
its LLM call in a bare except Exception: that logs "generate_scene: LLM call
failed" WITHOUT the exception, then returns an empty take. So a timeout is
indistinguishable from "the model wrote nothing", and a run would spend hours
writing empty files while looking healthy. That is why this module must log its
own failure detail at ERROR before raising: that log line is the only surviving
evidence.

This module deliberately does not modify app/llm_client.py, which is shared with
the live avatar app. It subclasses OllamaClient and overrides complete.
"""
import logging

import httpx

from llm_client import LLMError, OllamaClient

log = logging.getLogger(__name__)


class PooledOllamaClient(OllamaClient):
    def __init__(self, base_url, model, temperature, max_tokens,
                 timeout_s=600, num_ctx=None, http_client=None):
        super().__init__(base_url, model, temperature, max_tokens)
        self.timeout_s = timeout_s
        self.num_ctx = num_ctx

        # Track whether we built the client or it was injected
        self._owns_http_client = http_client is None
        if http_client is None:
            self.http_client = httpx.Client(timeout=timeout_s)
        else:
            self.http_client = http_client

    def complete(self, system_prompt, messages) -> str:
        log.debug("PooledOllamaClient.complete called for model %s with %d messages",
                  self.model, len(messages))

        json_body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        if self.num_ctx is not None:
            json_body["options"]["num_ctx"] = self.num_ctx

        try:
            response = self.http_client.post(
                f"{self.base_url}/api/chat",
                json=json_body,
                timeout=self.timeout_s
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error_msg = f"Ollama request failed: {exc.response.status_code} {exc.response.text}"
            log.error("HTTP error for model %s: %s", self.model, error_msg)
            raise LLMError(error_msg) from exc
        except (httpx.TimeoutException, httpx.ConnectTimeout) as exc:
            error_msg = f"Ollama request to model {self.model} timed out after {self.timeout_s}s"
            log.error("Timeout error for model %s: %s", self.model, error_msg)
            raise LLMError(error_msg) from exc
        except httpx.RequestError as exc:
            error_msg = f"Ollama request to {self.base_url} failed: {str(exc)}"
            log.error("Request error for base_url %s: %s", self.base_url, error_msg)
            raise LLMError(error_msg) from exc
        except (KeyError, TypeError) as exc:
            error_msg = "Ollama response did not contain expected structure"
            log.error("Malformed response from model %s: %s", self.model, error_msg)
            raise LLMError(error_msg) from exc

        try:
            content = response.json()["message"]["content"]
            log.debug("PooledOllamaClient.complete succeeded for model %s with %d characters",
                      self.model, len(content))
            return content
        except (KeyError, TypeError) as exc:
            error_msg = "Ollama response did not contain expected structure"
            log.error("Malformed response from model %s: %s", self.model, error_msg)
            raise LLMError(error_msg) from exc

    def close(self) -> None:
        # Only close the client if we built it (not injected)
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return None


def from_profile(profile) -> PooledOllamaClient:
    """Build a PooledOllamaClient from a resolved model profile."""
    provider = profile.get("provider", "ollama")
    if provider == "ollama":
        return PooledOllamaClient(
            base_url=profile.get("base_url", "http://localhost:11434"),
            model=profile.get("model", "mistral"),
            temperature=profile.get("temperature", 0.7),
            max_tokens=profile.get("max_tokens", 1024),
            timeout_s=profile.get("timeout_s", 600),
            num_ctx=profile.get("num_ctx"),
        )
    else:
        # For non-ollama providers, delegate to the existing builder
        from llm_client import build_llm_client
        # Create a minimal config dict for build_llm_client
        config = {"llm": profile}
        return build_llm_client(config)
