"""Shared result types for benchmark host adapters.

Each host adapter (OllamaHost, vLLMHost) owns its own protocol, but
they all return the same `CompletionResult` shape so the runner can
measure, aggregate and report uniformly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class HostError(RuntimeError):
    """Base error type. Subclasses should use this, not Exception, so
    callers can `except HostError` around benchmark calls."""


class ModelNotFound(HostError):
    pass


@dataclass
class CompletionResult:
    """One `Host.complete()` result.

    Conventions:
    - `content_tokens`  — number of tokens in the *spoken* line (the
      canonical "feels-live" unit). 0 if the model had no budget left
      to emit anything other than reasoning, or the call failed.
    - `reasoning_tokens` — thinking tokens the model emitted ahead of
      content. 0 if no thinking happened (or was disabled and the model
      has no thinking).
    - `ttft_s` — client-perceived time from request-out to the first
      byte of content. Measured by the benchmark runner itself (i.e.
      wall-clock), not by the host.
    - `total_s` — request-out → stream-complete, wall-clock.
    - `raw_extra` — host-side timing (e.g. Ollama's `eval_duration`),
      useful for cross-checking against client-wall-clock to detect
      client-side overhead.
    """
    model: str
    prompt_tokens: int
    content_tokens: int
    reasoning_tokens: int
    ttft_s: float
    total_s: float
    content_text: str = ""
    reasoning_text: str = ""
    finish_reason: str | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)
    raw_extra: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class HostModel:
    id: str
    size_bytes: int | None = None
    quantization: str | None = None
    context_length: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HostState:
    """Point-in-time view of what's loaded / resident on the host.
    Subclasses add host-specific fields (e.g. Ollama: `loaded`; vLLM:
    `metrics`, `kv_cache_usage`)."""
    note: str = ""
    protocol: str = ""
    loaded: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
