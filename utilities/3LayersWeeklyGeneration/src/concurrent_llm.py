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
import json
import logging
import os
import time
from datetime import datetime, timezone

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

    def complete_streaming(self, system_prompt, messages, on_progress=None) -> str:
        """Same request as complete(), but with `"stream": true` so partial
        tokens arrive as NDJSON lines instead of one blocking response. Used
        ONLY by the arc stage (single sequential call per batch) so a live
        "N tokens decoded, X tok/s" callback is unambiguous — see
        docs/dashboard live-progress panel. Deliberately a separate method
        from complete(): the concurrent segment/dialogue pool keeps using
        the non-streaming path unchanged, so this carries zero risk to
        those call sites or their tests.

        on_progress, when given, is called roughly every 2 seconds (never
        more often — Postgres write pressure) with a dict:
          {"model": self.model, "n_decoded": int, "tokens_per_s": float,
           "started_at": str (UTC ISO-8601), "updated_at": str (UTC ISO-8601)}
        Never called after the stream ends — the caller is responsible for
        clearing whatever it displayed (e.g. update_llm_progress(None)).

        Raises the same LLMError subclasses as complete() for the same
        failure classes (HTTP error, timeout, connection error, malformed
        response) so callers do not need to special-case which method they
        called.
        """
        log.debug("PooledOllamaClient.complete_streaming called for model %s with %d messages",
                  self.model, len(messages))

        json_body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "stream": True,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        if self.num_ctx is not None:
            json_body["options"]["num_ctx"] = self.num_ctx

        started = time.monotonic()
        started_wall = datetime.now(timezone.utc).isoformat()
        last_reported = started
        n_decoded = 0
        chunks = []
        logged_len = 0  # how much of "".join(chunks) has already hit the log

        try:
            with self.http_client.stream(
                "POST", f"{self.base_url}/api/chat",
                json=json_body, timeout=self.timeout_s,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError):
                        # A partial/malformed NDJSON line mid-stream must not
                        # abort an otherwise-good response — skip and keep
                        # reading; the final content is validated below.
                        log.debug("complete_streaming: skipping unparsable line for model %s",
                                  self.model)
                        continue
                    piece = event.get("message", {}).get("content", "")
                    if piece:
                        chunks.append(piece)
                        n_decoded += 1  # NDJSON event granularity, not exact
                                        # BPE token count — good enough for a
                                        # progress indicator, not a metric.

                    now = time.monotonic()
                    due = now - last_reported >= 2.0
                    if due:
                        elapsed = max(now - started, 1e-6)
                        if on_progress is not None:
                            on_progress({
                                "model": self.model,
                                "n_decoded": n_decoded,
                                "tokens_per_s": round(n_decoded / elapsed, 2),
                                "started_at": started_wall,
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            })
                        # Log the actual generated text as it streams in, on
                        # the SAME 2s cadence — this is what makes the
                        # model's real output visible in `docker logs` / the
                        # dashboard's existing Live Log Output panel while a
                        # long call is still in flight, not just after it
                        # ends. Logged unconditionally (not gated on
                        # on_progress) since the two serve different readers
                        # (DB progress row vs. container log tail).
                        full_so_far = "".join(chunks)
                        delta = full_so_far[logged_len:]
                        if delta:
                            log.info("[complete_streaming] model=%s n_decoded=%d new_text=%r",
                                      self.model, n_decoded, delta)
                            logged_len = len(full_so_far)
                        last_reported = now
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

        content = "".join(chunks)
        if not content:
            error_msg = "Ollama response did not contain expected structure"
            log.error("Malformed response from model %s: %s", self.model, error_msg)
            raise LLMError(error_msg)

        # Flush whatever text hadn't hit the 2s log cadence yet, so the log
        # always shows 100% of the generated text, not just what happened
        # to land on a throttle tick.
        tail = content[logged_len:]
        if tail:
            log.info("[complete_streaming] model=%s n_decoded=%d final_text=%r",
                      self.model, n_decoded, tail)

        log.debug("PooledOllamaClient.complete_streaming succeeded for model %s with %d characters",
                  self.model, len(content))
        return content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return None


class OpenAICompatClient:
    """Pooled OpenAI-compatible ``/v1/chat/completions`` client (vLLM and
    any server speaking the same wire protocol).

    A SEPARATE class from PooledOllamaClient rather than a subclass: the
    wire protocol differs in every place it matters (JSON body shape,
    Bearer auth, ``choices[0].message.content`` response, SSE ``data:``
    streaming instead of Ollama's raw NDJSON). It deliberately implements
    the same ``complete`` / ``complete_streaming`` / ``close`` /
    context-manager surface with the same LLMError semantics — the ERROR
    log before every raise (see module docstring) — so every layer call
    site works through ``from_profile`` without changes.

    ``extra_body`` carries provider-specific request fields verbatim, e.g.
    vLLM + Qwen3 thinking control:
    ``{"chat_template_kwargs": {"enable_thinking": false}}``.
    """

    def __init__(self, base_url, model, temperature, max_tokens,
                 timeout_s=600, api_key=None, extra_body=None,
                 http_client=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.api_key = api_key
        self.extra_body = dict(extra_body or {})

        self._owns_http_client = http_client is None
        if http_client is None:
            self.http_client = httpx.Client(timeout=timeout_s)
        else:
            self.http_client = http_client

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _json_body(self, system_prompt, messages, stream):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}] + list(messages),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        body.update(self.extra_body)
        return body

    def complete(self, system_prompt, messages) -> str:
        log.debug("OpenAICompatClient.complete called for model %s with %d messages",
                  self.model, len(messages))

        try:
            response = self.http_client.post(
                f"{self.base_url}/v1/chat/completions",
                json=self._json_body(system_prompt, messages, stream=False),
                headers=self._headers(),
                timeout=self.timeout_s,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error_msg = (f"OpenAI-compatible request failed: "
                         f"{exc.response.status_code} {exc.response.text}")
            log.error("HTTP error for model %s: %s", self.model, error_msg)
            raise LLMError(error_msg) from exc
        except (httpx.TimeoutException, httpx.ConnectTimeout) as exc:
            error_msg = (f"OpenAI-compatible request to model {self.model} "
                         f"timed out after {self.timeout_s}s")
            log.error("Timeout error for model %s: %s", self.model, error_msg)
            raise LLMError(error_msg) from exc
        except httpx.RequestError as exc:
            error_msg = f"OpenAI-compatible request to {self.base_url} failed: {str(exc)}"
            log.error("Request error for base_url %s: %s", self.base_url, error_msg)
            raise LLMError(error_msg) from exc

        try:
            content = response.json()["choices"][0]["message"]["content"]
            if content is None:
                raise KeyError("content")
            log.debug("OpenAICompatClient.complete succeeded for model %s with %d characters",
                      self.model, len(content))
            return content
        except (KeyError, TypeError, IndexError) as exc:
            error_msg = ("OpenAI-compatible response did not contain "
                         "choices[0].message.content")
            log.error("Malformed response from model %s: %s", self.model, error_msg)
            raise LLMError(error_msg) from exc

    def complete_streaming(self, system_prompt, messages, on_progress=None) -> str:
        """Same request as complete(), but with ``"stream": true`` so deltas
        arrive as SSE ``data:`` lines. Same contract as
        PooledOllamaClient.complete_streaming (2s progress cadence, no
        callback after the stream ends, identical LLMError semantics).

        Qwen3 reasoning models put the chain-of-thought in a SEPARATE
        ``delta.reasoning`` field (vLLM's ``--reasoning-parser qwen3``);
        only ``delta.content`` is accumulated, so reasoning tokens never
        leak into the parsed plan.
        """
        log.debug("OpenAICompatClient.complete_streaming called for model %s with %d messages",
                  self.model, len(messages))

        started = time.monotonic()
        started_wall = datetime.now(timezone.utc).isoformat()
        last_reported = started
        n_decoded = 0
        chunks = []
        logged_len = 0  # how much of "".join(chunks) has already hit the log

        try:
            with self.http_client.stream(
                "POST", f"{self.base_url}/v1/chat/completions",
                json=self._json_body(system_prompt, messages, stream=True),
                headers=self._headers(), timeout=self.timeout_s,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        # SSE comment lines (": OPENAI..."), blank keep-alives,
                        # and the "[DONE]" terminator are all skipped here.
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except (TypeError, ValueError):
                        # A partial/malformed SSE line mid-stream must not
                        # abort an otherwise-good response — skip and keep
                        # reading; the final content is validated below.
                        log.debug("complete_streaming: skipping unparsable SSE line "
                                  "for model %s", self.model)
                        continue
                    choice = (event.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        chunks.append(piece)
                        n_decoded += 1  # delta granularity, not exact BPE
                                        # count — good enough for a progress
                                        # indicator, not a metric.

                    now = time.monotonic()
                    due = now - last_reported >= 2.0
                    if due:
                        elapsed = max(now - started, 1e-6)
                        if on_progress is not None:
                            on_progress({
                                "model": self.model,
                                "n_decoded": n_decoded,
                                "tokens_per_s": round(n_decoded / elapsed, 2),
                                "started_at": started_wall,
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            })
                        full_so_far = "".join(chunks)
                        delta_text = full_so_far[logged_len:]
                        if delta_text:
                            log.info("[complete_streaming] model=%s n_decoded=%d new_text=%r",
                                     self.model, n_decoded, delta_text)
                            logged_len = len(full_so_far)
                        last_reported = now
        except httpx.HTTPStatusError as exc:
            error_msg = (f"OpenAI-compatible request failed: "
                         f"{exc.response.status_code} {exc.response.text}")
            log.error("HTTP error for model %s: %s", self.model, error_msg)
            raise LLMError(error_msg) from exc
        except (httpx.TimeoutException, httpx.ConnectTimeout) as exc:
            error_msg = (f"OpenAI-compatible request to model {self.model} "
                         f"timed out after {self.timeout_s}s")
            log.error("Timeout error for model %s: %s", self.model, error_msg)
            raise LLMError(error_msg) from exc
        except httpx.RequestError as exc:
            error_msg = f"OpenAI-compatible request to {self.base_url} failed: {str(exc)}"
            log.error("Request error for base_url %s: %s", self.base_url, error_msg)
            raise LLMError(error_msg) from exc

        content = "".join(chunks)
        if not content:
            error_msg = ("OpenAI-compatible response did not contain "
                         "any streamed content")
            log.error("Malformed response from model %s: %s", self.model, error_msg)
            raise LLMError(error_msg)

        tail = content[logged_len:]
        if tail:
            log.info("[complete_streaming] model=%s n_decoded=%d final_text=%r",
                     self.model, n_decoded, tail)

        log.debug("OpenAICompatClient.complete_streaming succeeded for model %s with %d characters",
                  self.model, len(content))
        return content

    def close(self) -> None:
        # Only close the client if we built it (not injected) — same
        # ownership contract as PooledOllamaClient.
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return None


# Provider spellings the config may use for the OpenAI-compatible path.
_OPENAI_COMPAT_PROVIDERS = ("openai", "openai-compatible", "vllm")


def from_profile(profile):
    """Build a pooled LLM client from a resolved model profile.

    Returns a PooledOllamaClient for ``provider: ollama``, an
    OpenAICompatClient for the OpenAI-compatible providers (vllm, ...),
    and falls back to llm_client.build_llm_client for anything else
    (claude).
    """
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
    if provider in _OPENAI_COMPAT_PROVIDERS:
        # `api_key_env` names an environment variable holding the key, so
        # configs committed to git never carry the secret itself. An inline
        # `api_key` still wins (saved DB configs created before this use it).
        api_key = profile.get("api_key")
        key_env = profile.get("api_key_env")
        if not api_key and key_env:
            api_key = os.environ.get(key_env) or None
            if api_key is None:
                log.warning("from_profile: api_key_env %s is not set; sending no key", key_env)
        return OpenAICompatClient(
            base_url=profile.get("base_url", "http://localhost:8000"),
            model=profile.get("model", "gpt-4o-mini"),
            temperature=profile.get("temperature", 0.7),
            max_tokens=profile.get("max_tokens", 1024),
            timeout_s=profile.get("timeout_s", 600),
            api_key=api_key,
            extra_body=profile.get("extra_body"),
        )
    # For other providers, delegate to the existing builder
    from llm_client import build_llm_client
    # Create a minimal config dict for build_llm_client
    config = {"llm": profile}
    return build_llm_client(config)
