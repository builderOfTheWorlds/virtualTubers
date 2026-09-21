"""vLLM host adapter (OpenAI v1 protocol).

vLLM exposes the OpenAI-compatible `/v1/chat/completions`. We measure the
same `complete() -> CompletionResult` interface as OllamaHost so the runner
treats both identically. Differences:

- thinking toggle: we send `chat_template_kwargs.enable_thinking` (the
  Qwen-style chat template knob) AND a top-level `think` — whichever the
  model's template understands is picked up, the other is ignored. For
  non-thinking templates both are dropped, no error. This is best-effort:
  vLLM's thinking toggle is template-dependent, so the canonical metric is
  reported with the host's *observed* reasoning-token count, not an assumed
  off state.
- state: vLLM's `/metrics` (Prometheus) — we pull `vllm:kv_cache_usage_perc`
  and the loaded-model set from `vllm:num_requests_running` /
  `vllm:gpu_cache_usage_perc` etc.
- host-side timing: vLLM does not emit per-request durations on the v1 wire,
  so we rely on client wall-clock + token counts (same as any OpenAI server).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from host_base import CompletionResult, HostError, HostModel, HostState

_METRIC_RE = re.compile(
    r"^(vllm:[a-zA-Z0-9_]+)(?:\{([^}]*)\})?\s+([-+0-9.eE]+)$")


def _parse_metric_line(line: str) -> tuple[str, dict[str, str], float] | None:
    """Parse one Prometheus text line into (name, labels, value).
    Returns None for comment lines, non-vllm series, or values that
    can't be parsed as finite floats (+Inf/-Inf/nan are skipped)."""
    m = _METRIC_RE.match(line)
    if not m:
        return None
    name = m.group(1)
    label_str = m.group(2) or ""
    raw_value = m.group(3)
    try:
        value = float(raw_value)
    except (ValueError, OverflowError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    labels: dict[str, str] = {}
    for part in label_str.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            labels[k.strip()] = v.strip().strip('"')
    return name, labels, value


class vLLMHost:
    protocol = "vllm_openai_v1"

    def __init__(self, base_url: str, timeout_s: float = 1200.0,
                 headers: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.headers = headers or {}
        self._c = httpx.Client(base_url=self.base_url, timeout=timeout_s,
                               headers=self.headers)

    # -- introspection -------------------------------------------------------

    def health(self) -> bool:
        try:
            r = self._c.get("/health")
            # 200 healthy; 503 reachable-but-still-loading; anything else
            # fall back to the v1 models endpoint.
            if r.status_code in (200, 503):
                return True
        except Exception:
            pass
        try:
            return self._c.get("/v1/models").status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[HostModel]:
        r = self._c.get("/v1/models")
        if r.status_code != 200:
            raise HostError(f"list_models: HTTP {r.status_code}: {r.text[:200]}")
        out: list[HostModel] = []
        for m in r.json().get("data", []):
            mid = m.get("id")
            if not mid:
                continue
            out.append(HostModel(
                id=mid,
                size_bytes=m.get("size"),
                context_length=m.get("context_length"),
                metadata=m,
            ))
        return out

    def state(self) -> HostState:
        """Loaded models + KV-cache pressure, from vLLM's Prometheus
        `/metrics`. `kv_cache_usage` is the fraction of the KV cache the
        engine is currently using — a direct signal of context-window
        pressure on the unified-memory pool."""
        r = self._c.get("/metrics")
        if r.status_code != 200:
            raise HostError(f"state: HTTP {r.status_code}: {r.text[:200]}")
        metrics: list[dict[str, Any]] = []
        kv = None
        for line in r.text.splitlines():
            parsed = _parse_metric_line(line)
            if not parsed:
                continue
            name, labels, value = parsed
            metrics.append({"name": name, "labels": labels, "value": value})
            if name in ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc") \
                    and kv is None:
                kv = value
        return HostState(
            protocol=self.protocol,
            note=(f"{len(metrics)} vllm:* series; "
                  f"kv_cache_usage={kv}"),
            metrics=metrics,
        )

    # -- the measured call ---------------------------------------------------

    def complete(self, *, model: str, system: str, user: str,
                 num_predict: int, think: bool = False,
                 temperature: float = 0.7, top_p: float | None = 1.0,
                 ) -> CompletionResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": num_predict,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": think, "think": think},
        }
        if top_p is not None:
            body["top_p"] = top_p

        t0 = time.perf_counter()
        first_byte_t: float | None = None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason = None

        with self._c.stream("POST", "/v1/chat/completions", json=body) as resp:
            if resp.status_code != 200:
                text = ""
                for blk in resp.iter_text():
                    text += blk
                    if len(text) > 4000:
                        break
                raise HostError(
                    f"complete: HTTP {resp.status_code}: {text[:400]}")
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                first_byte_t = (first_byte_t
                                if first_byte_t is not None
                                else time.perf_counter() - t0)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                    for key in ("reasoning", "thinking"):
                        if delta.get(key):
                            reasoning_parts.append(delta[key])
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

        total_s = time.perf_counter() - t0
        if first_byte_t is None:
            first_byte_t = 0.0

        content_text = "".join(content_parts)
        reasoning_text = "".join(reasoning_parts)

        n_prompt = int(usage.get("prompt_tokens") or 0)
        n_completion = int(usage.get("completion_tokens") or 0)
        n_think_reported = usage.get("reasoning_tokens")
        n_think = int(n_think_reported) if n_think_reported is not None else None
        if n_think is not None:
            reasoning_n = n_think
            content_n = max(0, n_completion - reasoning_n)
        else:
            # vLLM often doesn't split reasoning vs content in usage; split
            # by observed text (consistent within a run).
            rc, cc = len(reasoning_text), len(content_text)
            if n_completion and (rc + cc):
                reasoning_n = int(n_completion * rc / (rc + cc))
                content_n = max(0, n_completion - reasoning_n)
            else:
                reasoning_n, content_n = 0, n_completion
            n_think = reasoning_n

        return CompletionResult(
            model=model,
            prompt_tokens=n_prompt,
            content_tokens=content_n,
            reasoning_tokens=reasoning_n,
            ttft_s=first_byte_t,
            total_s=total_s,
            content_text=content_text,
            reasoning_text=reasoning_text,
            finish_reason=finish_reason,
            raw_usage=usage,
            raw_extra={"note": "vLLM: no per-request host timing on v1 wire; "
                               "ttft/total are client wall-clock"},
        )

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:
            pass


def from_url(base_url: str, *, timeout_s: float = 600.0,
             headers: dict[str, str] | None = None) -> vLLMHost:
    return vLLMHost(base_url, timeout_s=timeout_s, headers=headers)
