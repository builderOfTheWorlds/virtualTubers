import json
import os

import pytest

from agent_metrics import (
    AgentMetrics,
    InstrumentedLLMClient,
    MetricsProducerWrapper,
    parse_thinking_block,
    resolve_runtime_dir,
    DEFAULT_RUNTIME_DIR,
)


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def complete(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if self.error:
            raise self.error
        return self.response


# --- resolve_runtime_dir ---------------------------------------------------

def test_resolve_runtime_dir_prefers_env_var(monkeypatch):
    monkeypatch.setenv("PANES_RUNTIME_DIR", "/tmp/from-env")
    assert resolve_runtime_dir() == "/tmp/from-env"


def test_resolve_runtime_dir_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("PANES_RUNTIME_DIR", raising=False)
    assert resolve_runtime_dir() == DEFAULT_RUNTIME_DIR


# --- parse_thinking_block ---------------------------------------------------

def test_parse_thinking_block_extracts_and_strips():
    raw = "<thinking>plotting the fix</thinking>Digging into the bug now."
    thinking, reply = parse_thinking_block(raw)
    assert thinking == "plotting the fix"
    assert reply == "Digging into the bug now."


def test_parse_thinking_block_handles_surrounding_whitespace_and_multiline():
    raw = "Intro.\n<thinking>\n  step one\n  step two\n</thinking>\nOutro text."
    thinking, reply = parse_thinking_block(raw)
    assert thinking == "step one\n  step two"
    assert reply == "Intro.\n\nOutro text."


def test_parse_thinking_block_falls_back_when_absent():
    raw = "Just a plain reply, no thinking tags."
    thinking, reply = parse_thinking_block(raw)
    assert thinking is None
    assert reply == raw


def test_parse_thinking_block_case_insensitive():
    raw = "<THINKING>caps block</THINKING>reply text"
    thinking, reply = parse_thinking_block(raw)
    assert thinking == "caps block"
    assert reply == "reply text"


# --- AgentMetrics: running averages / rates --------------------------------

def test_agent_metrics_fresh_snapshot_shape_and_defaults(tmp_path):
    metrics = AgentMetrics("coder", runtime_dir=str(tmp_path))
    snap = metrics.snapshot()

    assert snap["worker_id"] == "coder"
    assert set(snap.keys()) == {
        "worker_id", "updated_at", "tokens_per_sec", "avg_latency_s",
        "context_tokens", "error_rate_pct", "uptime_pct", "messages_sent",
    }
    assert snap["tokens_per_sec"] == 0.0
    assert snap["avg_latency_s"] == 0.0
    assert snap["context_tokens"] == 0
    assert snap["error_rate_pct"] == 0.0
    assert snap["uptime_pct"] == 100.0
    assert snap["messages_sent"] == 0


def test_agent_metrics_record_call_running_averages(tmp_path):
    metrics = AgentMetrics("coder", runtime_dir=str(tmp_path))

    metrics.record_call(latency_s=2.0, prompt_word_count=10, response_word_count=20, success=True)
    metrics.record_call(latency_s=4.0, prompt_word_count=15, response_word_count=8, success=True)

    # tokens_per_sec: avg of (20/2.0=10.0) and (8/4.0=2.0) => 6.0
    assert metrics.tokens_per_sec == pytest.approx(6.0)
    # avg_latency_s: avg of 2.0 and 4.0 => 3.0
    assert metrics.avg_latency_s == pytest.approx(3.0)
    # context_tokens: most recent prompt's word count
    assert metrics.context_tokens == 15


def test_agent_metrics_error_rate_pct_counts_failures(tmp_path):
    metrics = AgentMetrics("coder", runtime_dir=str(tmp_path))

    metrics.record_call(latency_s=1.0, prompt_word_count=5, response_word_count=5, success=True)
    metrics.record_call(latency_s=1.0, prompt_word_count=5, response_word_count=0, success=False)
    metrics.record_call(latency_s=1.0, prompt_word_count=5, response_word_count=0, success=False)

    assert metrics.error_rate_pct == pytest.approx(200.0 / 3.0)
    # failed calls don't contribute to tokens_per_sec's sample set
    assert metrics.tokens_per_sec == pytest.approx(5.0)


def test_agent_metrics_uptime_pct_health_proxy(tmp_path):
    metrics = AgentMetrics("coder", runtime_dir=str(tmp_path))

    metrics.record_tick(True)
    metrics.record_tick(True)
    metrics.record_tick(False)
    metrics.record_tick(True)

    assert metrics.uptime_pct == pytest.approx(75.0)


def test_agent_metrics_messages_sent_counter(tmp_path):
    metrics = AgentMetrics("coder", runtime_dir=str(tmp_path))
    metrics.record_message_sent()
    metrics.record_message_sent()
    assert metrics.messages_sent == 2


# --- AgentMetrics.write_snapshot: atomic file contract ----------------------

def test_write_snapshot_writes_contract_a_json(tmp_path):
    metrics = AgentMetrics("coder", runtime_dir=str(tmp_path))
    metrics.record_call(latency_s=2.0, prompt_word_count=10, response_word_count=20, success=True)
    metrics.record_message_sent()

    data = metrics.write_snapshot()

    path = tmp_path / "metrics_coder.json"
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == data
    assert on_disk["worker_id"] == "coder"
    assert on_disk["messages_sent"] == 1
    assert on_disk["tokens_per_sec"] == pytest.approx(10.0)


def test_write_snapshot_leaves_no_temp_file_behind(tmp_path):
    metrics = AgentMetrics("coder", runtime_dir=str(tmp_path))
    metrics.write_snapshot()
    assert not (tmp_path / "metrics_coder.json.tmp").exists()


def test_write_snapshot_creates_runtime_dir_if_missing(tmp_path):
    runtime_dir = tmp_path / "nested" / "panes"
    metrics = AgentMetrics("coder", runtime_dir=str(runtime_dir))
    metrics.write_snapshot()
    assert (runtime_dir / "metrics_coder.json").exists()


# --- InstrumentedLLMClient: contract B behavior -----------------------------

def test_instrumented_llm_client_publishes_agent_thinking_and_strips_block(tmp_path):
    producer = FakeProducer()
    wrapped = FakeLLM(response="<thinking>plan the fix</thinking>Fixing it now.")
    client = InstrumentedLLMClient(wrapped, producer, "coder", runtime_dir=str(tmp_path))

    reply = client.complete("You are KODI-7.", [{"role": "user", "content": "go"}])

    assert reply == "Fixing it now."
    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["from"] == "coder"
    assert sent["to"] == "broadcast"
    assert sent["type"] == "agent_thinking"
    assert sent["payload"] == {"text": "plan the fix"}


def test_instrumented_llm_client_injects_thinking_instruction_into_system_prompt(tmp_path):
    producer = FakeProducer()
    wrapped = FakeLLM(response="no tags here")
    client = InstrumentedLLMClient(wrapped, producer, "coder", runtime_dir=str(tmp_path))

    client.complete("You are KODI-7.", [{"role": "user", "content": "go"}])

    sent_system_prompt = wrapped.calls[0][0]
    assert sent_system_prompt.startswith("You are KODI-7.")
    assert "<thinking>" in sent_system_prompt


def test_instrumented_llm_client_no_thinking_block_falls_back_without_crash(tmp_path):
    producer = FakeProducer()
    wrapped = FakeLLM(response="Just narrating, no tags.")
    client = InstrumentedLLMClient(wrapped, producer, "coder", runtime_dir=str(tmp_path))

    reply = client.complete("sys", [{"role": "user", "content": "go"}])

    assert reply == "Just narrating, no tags."
    # No agent_thinking published on fallback.
    assert producer.sent == []


def test_instrumented_llm_client_same_signature_and_return_type_as_wrapped(tmp_path):
    producer = FakeProducer()
    wrapped = FakeLLM(response="plain string reply")
    client = InstrumentedLLMClient(wrapped, producer, "coder", runtime_dir=str(tmp_path))

    reply = client.complete("sys", [{"role": "user", "content": "hi"}])

    assert isinstance(reply, str)


def test_instrumented_llm_client_writes_metrics_snapshot_after_call(tmp_path):
    producer = FakeProducer()
    wrapped = FakeLLM(response="<thinking>x</thinking>ok")
    client = InstrumentedLLMClient(wrapped, producer, "coder", runtime_dir=str(tmp_path))

    client.complete("sys", [{"role": "user", "content": "hi"}])

    path = tmp_path / "metrics_coder.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["worker_id"] == "coder"


def test_instrumented_llm_client_propagates_error_and_records_failed_call(tmp_path):
    producer = FakeProducer()
    wrapped = FakeLLM(error=RuntimeError("connection refused"))
    client = InstrumentedLLMClient(wrapped, producer, "coder", runtime_dir=str(tmp_path))

    with pytest.raises(RuntimeError, match="connection refused"):
        client.complete("sys", [{"role": "user", "content": "hi"}])

    assert client.metrics.total_calls == 1
    assert client.metrics.failed_calls == 1
    assert client.metrics.error_rate_pct == pytest.approx(100.0)
    # Snapshot is still written on failure so the pane doesn't go stale.
    assert (tmp_path / "metrics_coder.json").exists()
    # No agent_thinking published for a failed call.
    assert producer.sent == []


# --- MetricsProducerWrapper --------------------------------------------------

def test_metrics_producer_wrapper_forwards_send_and_counts(tmp_path):
    real_producer = FakeProducer()
    metrics = AgentMetrics("coder", runtime_dir=str(tmp_path))
    wrapper = MetricsProducerWrapper(real_producer, metrics)

    message = {"type": "task_complete"}
    result = wrapper.send(message)

    assert result is message
    assert real_producer.sent == [message]
    assert metrics.messages_sent == 1


def test_metrics_producer_wrapper_forwards_unknown_attributes(tmp_path):
    real_producer = FakeProducer()
    real_producer.topic = "vtuber-events"
    metrics = AgentMetrics("coder", runtime_dir=str(tmp_path))
    wrapper = MetricsProducerWrapper(real_producer, metrics)

    assert wrapper.topic == "vtuber-events"
