#!/usr/bin/env python3
"""
agent_metrics.py
Per-worker LLM-call instrumentation: `AgentMetrics` tracks running
performance counters and atomically snapshots them to the runtime-dir JSON
file the radar pane polls (Frozen Contract A, docs/tuber_base_layout_plan.md);
`InstrumentedLLMClient` wraps a real `llm_client.py` client so `agent.py`'s
`main()` can swap it in at the single choke point where `llm_client` and
`producer` are constructed, without touching any of the 8
`llm_client.complete(...)` call sites elsewhere in agent.py.

Metric definitions (best-effort estimates — no true tokenizer is available
across providers, see contract A):
- tokens_per_sec: running average of `len(response.split()) / latency_s` per
  successful call (word count over wall-clock seconds).
- avg_latency_s: running average of `complete()` wall-clock duration, over
  every call attempt (success or failure) since process start.
- context_tokens: best-effort word-count of the last prompt sent
  (system_prompt + all message `content` fields joined), NOT a true
  tokenizer count.
- error_rate_pct: 100 * failed_calls / total_calls since process start (0.0
  when no calls have been made yet).
- uptime_pct: 100 * ticks_without_exception / total_ticks, a simple health
  proxy driven by `AgentMetrics.record_tick(ok)` — the agent loop already
  knows, per tick, whether it hit an unhandled exception, so this needs no
  new plumbing beyond one call per tick. 100.0 when no ticks recorded yet
  (fresh worker, nothing to report as unhealthy).
- messages_sent: running count of every `producer.send(...)` call, tracked
  via `MetricsProducerWrapper` (thin pass-through producer that increments
  the shared `AgentMetrics` instance and forwards to the real producer).

The metrics file is rewritten (never appended) after every LLM completion,
atomically (`tmp` + `os.replace`, matching agent_state.write_state's
pattern) so a pane reading it mid-write never observes a torn file.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

from message_bus import build_message

THINKING_INSTRUCTION = (
    "\n\nBefore your reply, think through your approach privately inside a "
    "<thinking>...</thinking> block, then give your actual in-character "
    "reply after it. The <thinking> block will not be shown to the "
    "audience."
)

THINKING_BLOCK_RE = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL | re.IGNORECASE)

DEFAULT_RUNTIME_DIR = "/tmp/panes"
RUNTIME_DIR_ENV = "PANES_RUNTIME_DIR"


def resolve_runtime_dir(env_name=RUNTIME_DIR_ENV, default=DEFAULT_RUNTIME_DIR):
    """Env var wins over `default` — mirrors message_bus.resolve's
    precedence convention. No worker-config key for this today (the runtime
    dir is a deployment concern set via build_layout.py's --runtime-dir /
    the matching env var), so there's no config_value argument here."""
    return os.environ.get(env_name) or default


def _metrics_path(runtime_dir, worker_id):
    return os.path.join(runtime_dir, f"metrics_{worker_id}.json")


class AgentMetrics:
    """Running counters for one worker process, snapshotted to disk per
    contract A. Not thread-safe (agent.py's main loop is single-threaded)."""

    def __init__(self, worker_id, runtime_dir=None):
        self.worker_id = worker_id
        self.runtime_dir = runtime_dir or resolve_runtime_dir()

        self.total_calls = 0
        self.failed_calls = 0
        self._latency_total_s = 0.0
        self._tokens_per_sec_total = 0.0
        self._tokens_per_sec_samples = 0
        self.context_tokens = 0

        self.total_ticks = 0
        self.ticks_without_exception = 0

        self.messages_sent = 0

    def record_call(self, latency_s, prompt_word_count, response_word_count, success):
        """Update running averages after one `complete()` attempt.

        `prompt_word_count` always updates `context_tokens` (the estimate
        is of the request that was sent, regardless of outcome).
        `tokens_per_sec` only accumulates on success — a failed call has no
        response to measure a rate from.
        """
        self.total_calls += 1
        if not success:
            self.failed_calls += 1
        self._latency_total_s += latency_s
        self.context_tokens = prompt_word_count

        if success and latency_s > 0:
            self._tokens_per_sec_total += response_word_count / latency_s
            self._tokens_per_sec_samples += 1

    def record_tick(self, ok):
        """Call once per agent-loop tick with whether it completed without
        an unhandled exception. Drives the uptime_pct health proxy."""
        self.total_ticks += 1
        if ok:
            self.ticks_without_exception += 1

    def record_message_sent(self):
        self.messages_sent += 1

    @property
    def avg_latency_s(self):
        if self.total_calls == 0:
            return 0.0
        return self._latency_total_s / self.total_calls

    @property
    def tokens_per_sec(self):
        if self._tokens_per_sec_samples == 0:
            return 0.0
        return self._tokens_per_sec_total / self._tokens_per_sec_samples

    @property
    def error_rate_pct(self):
        if self.total_calls == 0:
            return 0.0
        return 100.0 * self.failed_calls / self.total_calls

    @property
    def uptime_pct(self):
        if self.total_ticks == 0:
            return 100.0
        return 100.0 * self.ticks_without_exception / self.total_ticks

    def snapshot(self):
        """Return the contract-A dict (not yet written to disk)."""
        return {
            "worker_id": self.worker_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "tokens_per_sec": round(self.tokens_per_sec, 2),
            "avg_latency_s": round(self.avg_latency_s, 2),
            "context_tokens": self.context_tokens,
            "error_rate_pct": round(self.error_rate_pct, 2),
            "uptime_pct": round(self.uptime_pct, 2),
            "messages_sent": self.messages_sent,
        }

    def write_snapshot(self):
        """Atomically rewrite `metrics_<worker_id>.json` in `runtime_dir`
        (tmp file + os.replace, same pattern as agent_state.write_state)."""
        os.makedirs(self.runtime_dir, exist_ok=True)
        path = _metrics_path(self.runtime_dir, self.worker_id)
        data = self.snapshot()
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
        return data


def parse_thinking_block(raw_response):
    """Split a raw LLM response into (thinking_text_or_None, reply_text).

    Extracts the first `<thinking>...</thinking>` block (contract B). If
    none is found (model ignored the instruction), returns
    `(None, raw_response)` unchanged — the caller must not crash or drop
    the reply, per contract B's fallback rule.
    """
    match = THINKING_BLOCK_RE.search(raw_response or "")
    if not match:
        return None, raw_response
    thinking_text = match.group(1).strip()
    reply_text = (raw_response[: match.start()] + raw_response[match.end():]).strip()
    return thinking_text, reply_text


def _word_count(text):
    return len((text or "").split())


def _prompt_word_count(system_prompt, messages):
    total = _word_count(system_prompt)
    for message in messages or []:
        total += _word_count(message.get("content", ""))
    return total


class MetricsProducerWrapper:
    """Thin pass-through around a real MessageProducer: forwards `.send`
    unchanged and increments the shared AgentMetrics' messages_sent counter.
    Same public surface (`send`) as MessageProducer, so it's a drop-in
    replacement anywhere a producer is used."""

    def __init__(self, producer, metrics):
        self._producer = producer
        self._metrics = metrics

    def send(self, message):
        result = self._producer.send(message)
        self._metrics.record_message_sent()
        return result

    def __getattr__(self, name):
        # Forward anything else (e.g. attribute access in tests) straight
        # to the wrapped producer.
        return getattr(self._producer, name)


class InstrumentedLLMClient:
    """Wraps a real llm_client (OllamaClient/ClaudeClient) so `agent.py`'s
    8 existing `llm_client.complete(system_prompt, messages)` call sites
    need no changes: same signature, same plain-string return type.

    Per call: injects the thinking-block instruction into the system
    prompt (one completion call, no extra round-trip), times it, updates
    `metrics`, parses out `<thinking>...</thinking>` (contract B, with the
    documented fallback when absent), publishes an `agent_thinking` message
    via `producer` when a thinking block was found, writes the metrics
    snapshot, and returns the reply text with the thinking block stripped.

    On an underlying `complete()` failure: metrics are still updated
    (failed-call counted, latency recorded, context_tokens estimated), the
    snapshot is still written, and the exception is re-raised unchanged so
    every existing call site's `except Exception` handling keeps working
    exactly as today.
    """

    def __init__(self, wrapped_client, producer, worker_id, metrics=None, runtime_dir=None):
        self._wrapped = wrapped_client
        self._producer = producer
        self.worker_id = worker_id
        self.metrics = metrics or AgentMetrics(worker_id, runtime_dir=runtime_dir)

    def complete(self, system_prompt, messages):
        prompt_word_count = _prompt_word_count(system_prompt, messages)
        instrumented_system_prompt = (system_prompt or "") + THINKING_INSTRUCTION

        start = time.monotonic()
        try:
            raw_response = self._wrapped.complete(instrumented_system_prompt, messages)
        except Exception:
            latency_s = time.monotonic() - start
            self.metrics.record_call(latency_s, prompt_word_count, 0, success=False)
            self.metrics.write_snapshot()
            raise
        latency_s = time.monotonic() - start

        thinking_text, reply_text = parse_thinking_block(raw_response)
        response_word_count = _word_count(raw_response)
        self.metrics.record_call(latency_s, prompt_word_count, response_word_count, success=True)

        if thinking_text:
            self._producer.send(build_message(
                self.worker_id, "broadcast", "agent_thinking", {"text": thinking_text},
            ))
        else:
            print(
                f"[agent_metrics:{self.worker_id}] DEBUG no <thinking> block found in "
                "response, publishing full reply as narration without agent_thinking"
            )

        self.metrics.write_snapshot()
        return reply_text
