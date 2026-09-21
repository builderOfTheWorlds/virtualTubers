"""Ollama host adapter (native /api/chat protocol).

We use Ollama's NATIVE `/api/chat` (not the OpenAI `/v1` shim) because:

1. It returns authoritative host-side timing in the final `done` chunk —
   `prompt_eval_count`/`prompt_eval_duration` (prefill) and `eval_count`/
   `eval_duration` (full decode) — letting us split prefill from decode
   instead of guessing from wall-clock deltas.
2. It honours `options.think` for the reasoning toggle.
The cost is a host-specific protocol; the runner only sees the shared
`complete() -> CompletionResult` interface, so vLLM (OpenAI v1) and
Ollama (native) remain interchangeable from the runner's point of view.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from host_base import CompletionResult, HostError, HostModel, HostState

_GB10_NOTE = "On a GB10 the CPU and GPU share unified memory; watch free RAM."


def _validate_url(base_url: str) -> str:
    """base_url must be absolute; Ollama's API is HTTP-only (vLLM is
    typically HTTP, but HTTPS is allowed for remote deployments)."""
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise HostError(
            f"base_url must be a full URL (http:// or https://), got {base_url!r}")
    return base_url.rstrip("/")


class OllamaHost:
    protocol = "ollama_native"

    def __init__(self, base_url: str, timeout_s: float = 1200.0,
                 headers: dict[str, str] | None = None):
        self.base_url = _validate_url(base_url)
        self.timeout_s = timeout_s
        self.headers = headers or {}
        self._c = httpx.Client(base_url=self.base_url, timeout=timeout_s,
                               headers=self.headers)

    # -- introspection -------------------------------------------------------

    def health(self) -> bool:
        try:
            return self._c.get("/api/version").status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[HostModel]:
        try:
            r = self._c.get("/api/tags")
        except Exception as exc:  # noqa: BLE001
            raise HostError(f"list_models: {exc}") from exc
        if r.status_code != 200:
            raise HostError(f"list_models: HTTP {r.status_code}: {r.text[:200]}")
        out: list[HostModel] = []
        for m in r.json().get("models", []):
            out.append(HostModel(
                id=m.get("name", ""),
                size_bytes=m.get("size"),
                metadata=m,
            ))
        return out

    def state(self) -> HostState:
        """Which models are resident + how much memory each holds.

        `size_vram` is Ollama's per-model resident allocation (weights) at
        the queried context. Summing across loaded models is the "how
        much of the 128 GB unified pool is committed to weights" number
        the design doc §8 wants us to measure before picking A/B/C. Note:
        it reflects the current context window, so KV-cache growth on top
        is extra — that's why we also capture free RAM in the report."""
        r = self._c.get("/api/ps")
        if r.status_code != 200:
            raise HostError(f"state: HTTP {r.status_code}")
        loaded = []
        vram_total = 0
        for m in r.json().get("models", []):
            vram = int(m.get("size_vram") or 0)
            vram_total += vram
            details = m.get("details") or {}
            loaded.append({
                "name": m.get("name"),
                "size_vram": vram,
                "size_disk": m.get("size"),
                "context_length": m.get("context_length"),
                "quantization": details.get("quantization_level"),
                "parameter_size": details.get("parameter_size"),
                "expires_at": m.get("expires_at"),
            })
        return HostState(
            protocol=self.protocol,
            note=(f"{len(loaded)} model(s) resident; "
                  f"weights sum = {vram_total/1e9:.2f} GB"
                  f" (KV-cache at real context is extra — see { _GB10_NOTE })"),
            loaded=loaded,
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
            "stream": True,
            "options": {
                "think": think,
                "num_predict": num_predict,
                "temperature": temperature,
            },
        }
        if top_p is not None:
            body["options"]["top_p"] = top_p

        t0 = time.perf_counter()
        first_byte_t: float | None = None
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        done_chunk: dict[str, Any] = {}

        with self._c.stream("POST", "/api/chat", json=body) as resp:
            if resp.status_code != 200:
                text = ""
                for blk in resp.iter_text():
                    text += blk
                    if len(text) > 4000:
                        break
                raise HostError(
                    f"complete: HTTP {resp.status_code}: {text[:400]}")
            for line in resp.iter_lines():
                if not line or not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                first_byte_t = (first_byte_t
                                if first_byte_t is not None
                                else time.perf_counter() - t0)
                msg = chunk.get("message") or {}
                if msg.get("content"):
                    content_parts.append(msg["content"])
                # Thinking arrives as `thinking` on this endpoint (not
                # `reasoning`, which is the /v1 shim field name).
                for key in ("thinking", "reasoning"):
                    if msg.get(key):
                        thinking_parts.append(msg[key])
                if chunk.get("done"):
                    done_chunk = chunk

        total_s = time.perf_counter() - t0
        if first_byte_t is None:
            first_byte_t = 0.0

        content_text = "".join(content_parts)
        reasoning_text = "".join(thinking_parts)

        # Token counts.
        n_completion = int(done_chunk.get("eval_count") or 0)
        n_prompt = int(done_chunk.get("prompt_eval_count") or 0)
        n_think_reported = int(done_chunk.get("reasoning_token_count") or 0)
        # Content vs thinking split: prefer the explicit reasoning count if
        # the host gives us one; otherwise split the completed tokens by the
        # observed text ratio (consistent within a run, which is what we need).
        if n_think_reported:
            reasoning_n = n_think_reported
            content_n = max(0, n_completion - reasoning_n)
        else:
            rc, cc = len(reasoning_text), len(content_text)
            if n_completion and (rc + cc):
                reasoning_n = int(n_completion * rc / (rc + cc))
                content_n = max(0, n_completion - reasoning_n)
            else:
                reasoning_n, content_n = 0, n_completion

        # Host-authoritative split of prefill vs decode:
        # Ollama's `prompt_eval_duration` is prefill time, `eval_duration`
        # is decode time — they are separate quantities, NOT cumulative,
        # even though the field names suggest otherwise (verified empirically:
        # a 6709-prompt, 11-token call returns prompt_eval_duration=3200ms,
        # eval_duration=388ms — decode is ~11 tokens at ~28 tok/s, which matches).
        prompt_eval_dur = done_chunk.get("prompt_eval_duration")
        eval_dur = done_chunk.get("eval_duration")
        host_prefill_s = (prompt_eval_dur / 1e9) if prompt_eval_dur else None
        host_decode_s = (eval_dur / 1e9) if eval_dur else None
        host_total_eval_s = host_prefill_s + host_decode_s \
            if host_prefill_s is not None and host_decode_s is not None else None

        raw_extra = {
            "finish_reason": done_chunk.get("done_reason"),
            "host_prefill_s": host_prefill_s,
            "host_decode_s": host_decode_s,
            "host_total_eval_s": host_total_eval_s,
            "load_duration_s": (done_chunk.get("load_duration", 0) / 1e9
                                or None),
        }

        return CompletionResult(
            model=model,
            prompt_tokens=n_prompt,
            content_tokens=content_n,
            reasoning_tokens=reasoning_n,
            ttft_s=first_byte_t,
            total_s=total_s,
            content_text=content_text,
            reasoning_text=reasoning_text,
            finish_reason=done_chunk.get("done_reason"),
            raw_usage=done_chunk,
            raw_extra=raw_extra,
        )

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:
            pass


def from_url(base_url: str, *, timeout_s: float = 600.0,
             headers: dict[str, str] | None = None) -> OllamaHost:
    return OllamaHost(base_url, timeout_s=timeout_s, headers=headers)
