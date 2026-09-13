"""Acceptance tests for src/concurrent_llm.py — the pooled, timeout-configurable
Ollama client the concurrent layers drive.

`app/llm_client.py`'s OllamaClient calls module-level `httpx.post` with a
hardcoded `timeout=120`. Two things break at scale:

1. Batching raises throughput BY RAISING PER-REQUEST LATENCY. At parallel-8 a
   request can take several times the measured 65s and cross 120s.
2. Every one of ~14,300 calls opens a fresh connection.

And the failure is silent: `LLMImproviser.generate_scene` wraps the call in a
bare `except Exception` that logs "LLM call failed" without the exception, so a
timeout is indistinguishable from "the model wrote nothing". A run would burn
hours writing empty takes. This client therefore logs its own failure detail at
ERROR before raising, so the cause survives that swallow.

Per CLAUDE.md's shared-utilities rule this WRAPS `app/llm_client.py` by
subclassing. No file under `app/` is modified.
"""
import threading

import httpx
import pytest

import concurrent_llm
from llm_client import LLMError, OllamaClient


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload if payload is not None else {
            "message": {"content": "Leena: The fire is low."}}
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=self)

    def json(self):
        return self._payload


class FakeHTTPClient:
    """Stands in for httpx.Client. Records every post it is handed."""

    def __init__(self, response=None, raises=None):
        self.calls = []
        self.closed = False
        self._response = response or FakeResponse()
        self._raises = raises
        self._lock = threading.Lock()

    def post(self, url, json=None, **kwargs):
        with self._lock:
            self.calls.append({"url": url, "json": json, "kwargs": kwargs})
        if self._raises is not None:
            raise self._raises
        return self._response

    def close(self):
        self.closed = True


@pytest.fixture
def http():
    return FakeHTTPClient()


@pytest.fixture
def client(http):
    return concurrent_llm.PooledOllamaClient(
        base_url="http://localhost:11434", model="hermes3:70b",
        temperature=0.9, max_tokens=1024, timeout_s=600, num_ctx=8192,
        http_client=http)


# ── it really is the project's client, not a parallel implementation ──────────

def test_pooled_client_is_an_ollama_client_subclass():
    """Anything that accepts the live client must accept this one."""
    assert issubclass(concurrent_llm.PooledOllamaClient, OllamaClient)


def test_it_carries_the_same_attributes_the_parent_exposes(client):
    assert client.base_url == "http://localhost:11434"
    assert client.model == "hermes3:70b"
    assert client.temperature == 0.9
    assert client.max_tokens == 1024


def test_trailing_slash_on_base_url_is_stripped_like_the_parent(http):
    client = concurrent_llm.PooledOllamaClient(
        "http://localhost:11434/", "m", 0.7, 512, http_client=http)
    client.complete("sys", [{"role": "user", "content": "hi"}])
    assert http.calls[0]["url"] == "http://localhost:11434/api/chat"


# ── the request it actually sends ─────────────────────────────────────────────

def test_complete_posts_the_chat_payload_and_returns_the_content(client, http):
    reply = client.complete("You generate ambient scenes.",
                            [{"role": "user", "content": "A quiet moment."}])
    assert reply == "Leena: The fire is low."

    call = http.calls[0]
    assert call["url"] == "http://localhost:11434/api/chat"
    assert call["json"]["model"] == "hermes3:70b"
    assert call["json"]["stream"] is False
    assert call["json"]["messages"][0] == {
        "role": "system", "content": "You generate ambient scenes."}
    assert call["json"]["messages"][1] == {
        "role": "user", "content": "A quiet moment."}


def test_options_carry_temperature_num_predict_and_num_ctx(client, http):
    client.complete("sys", [{"role": "user", "content": "hi"}])
    options = http.calls[0]["json"]["options"]
    assert options["temperature"] == 0.9
    assert options["num_predict"] == 1024
    assert options["num_ctx"] == 8192


def test_num_ctx_is_omitted_when_not_configured(http):
    """num_ctx unset must leave the server's own default alone, not send None."""
    client = concurrent_llm.PooledOllamaClient(
        "http://localhost:11434", "m", 0.7, 512, http_client=http)
    client.complete("sys", [{"role": "user", "content": "hi"}])
    assert "num_ctx" not in http.calls[0]["json"]["options"]


def test_the_configured_timeout_is_sent_with_every_request(client, http):
    """The whole point of the wrapper: not the parent's hardcoded 120."""
    client.complete("sys", [{"role": "user", "content": "hi"}])
    assert http.calls[0]["kwargs"].get("timeout") == 600


# ── pooling ───────────────────────────────────────────────────────────────────

def test_the_same_http_client_is_reused_across_calls(client, http):
    for _ in range(5):
        client.complete("sys", [{"role": "user", "content": "hi"}])
    assert len(http.calls) == 5
    assert client.http_client is http


def test_close_closes_an_http_client_it_built_itself():
    client = concurrent_llm.PooledOllamaClient(
        "http://localhost:11434", "m", 0.7, 512)
    client.close()
    assert client.http_client.is_closed is True


def test_close_leaves_an_injected_http_client_alone(client, http):
    """Ownership contract: a caller may hand the SAME httpx.Client to two
    clients on different model profiles to share one connection pool. Closing
    a borrowed client would silently break its sibling mid-run."""
    client.close()
    assert http.closed is False


def test_close_is_idempotent():
    client = concurrent_llm.PooledOllamaClient(
        "http://localhost:11434", "m", 0.7, 512)
    client.close()
    client.close()
    assert client.http_client.is_closed is True


def test_it_works_as_a_context_manager():
    with concurrent_llm.PooledOllamaClient(
            "http://localhost:11434", "m", 0.7, 512) as client:
        assert client.http_client.is_closed is False
    assert client.http_client.is_closed is True


def test_the_context_manager_does_not_suppress_exceptions(http):
    with pytest.raises(ValueError):
        with concurrent_llm.PooledOllamaClient(
                "http://localhost:11434", "m", 0.7, 512, http_client=http):
            raise ValueError("boom")


def test_a_default_constructed_client_builds_its_own_pooled_http_client():
    """No injected client -> it must make one, and it must be an httpx.Client."""
    client = concurrent_llm.PooledOllamaClient(
        "http://localhost:11434", "m", 0.7, 512, timeout_s=42)
    try:
        assert isinstance(client.http_client, httpx.Client)
    finally:
        client.close()


# ── failure surfaces loudly (it is swallowed downstream) ──────────────────────

def test_http_error_raises_llmerror_carrying_the_response_body():
    """The parent surfaces Ollama's body text; losing it loses the diagnosis."""
    http = FakeHTTPClient(response=FakeResponse(
        status_code=404, text="model 'nope' not found, try pulling it first"))
    client = concurrent_llm.PooledOllamaClient(
        "http://localhost:11434", "nope", 0.7, 512, http_client=http)
    with pytest.raises(LLMError) as excinfo:
        client.complete("sys", [{"role": "user", "content": "hi"}])
    assert "not found" in str(excinfo.value)


def test_a_timeout_raises_llmerror_rather_than_escaping_as_httpx(caplog):
    """A raw httpx.TimeoutException would land in generate_scene's bare
    `except Exception` and vanish. It must arrive as the project's own error."""
    http = FakeHTTPClient(raises=httpx.ReadTimeout("timed out"))
    client = concurrent_llm.PooledOllamaClient(
        "http://localhost:11434", "m", 0.7, 512, timeout_s=600, http_client=http)
    caplog.set_level("ERROR")
    with pytest.raises(LLMError):
        client.complete("sys", [{"role": "user", "content": "hi"}])
    assert caplog.records, "a timeout was raised without an ERROR log line"


def test_a_connection_error_raises_llmerror_too():
    http = FakeHTTPClient(raises=httpx.ConnectError("connection refused"))
    client = concurrent_llm.PooledOllamaClient(
        "http://localhost:11434", "m", 0.7, 512, http_client=http)
    with pytest.raises(LLMError):
        client.complete("sys", [{"role": "user", "content": "hi"}])


def test_a_malformed_reply_body_raises_llmerror_not_keyerror():
    """Ollama returning an unexpected shape must not crash a pool worker."""
    http = FakeHTTPClient(response=FakeResponse(payload={"unexpected": "shape"}))
    client = concurrent_llm.PooledOllamaClient(
        "http://localhost:11434", "m", 0.7, 512, http_client=http)
    with pytest.raises(LLMError):
        client.complete("sys", [{"role": "user", "content": "hi"}])


# ── thread safety: the pool drives one client from N workers ──────────────────

def test_concurrent_completes_all_succeed_and_are_all_recorded(client, http):
    errors = []

    def worker():
        try:
            client.complete("sys", [{"role": "user", "content": "hi"}])
        except Exception as exc:  # noqa: BLE001 - the assertion is the point
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(http.calls) == 8


# ── construction from a resolved config profile ───────────────────────────────

def test_from_profile_builds_a_pooled_client_for_an_ollama_profile():
    profile = {"provider": "ollama", "base_url": "http://localhost:11434",
               "model": "hermes3:70b", "temperature": 0.9, "max_tokens": 1024,
               "timeout_s": 600, "num_ctx": 8192}
    client = concurrent_llm.from_profile(profile)
    try:
        assert isinstance(client, concurrent_llm.PooledOllamaClient)
        assert client.model == "hermes3:70b"
        assert client.timeout_s == 600
        assert client.num_ctx == 8192
    finally:
        client.close()


def test_from_profile_applies_sane_defaults_for_an_omitted_timeout():
    client = concurrent_llm.from_profile(
        {"provider": "ollama", "base_url": "http://x", "model": "m"})
    try:
        assert client.timeout_s > 120, "must not inherit the parent's 120s"
    finally:
        client.close()


def test_from_profile_rejects_an_unknown_provider():
    with pytest.raises(LLMError):
        concurrent_llm.from_profile({"provider": "carrier-pigeon", "model": "m"})


# ── complete_streaming: the arc-stage-only token-progress path ────────────────

class FakeStreamResponse:
    """Stands in for the object httpx.Client.stream()'s context manager
    yields: raise_for_status() plus an iter_lines() generator over
    pre-baked NDJSON lines, exactly like Ollama's streaming /api/chat."""

    def __init__(self, lines, status_code=200, text=""):
        self._lines = lines
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=self)

    def iter_lines(self):
        yield from self._lines


class FakeStreamContextManager:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises

    def __enter__(self):
        if self._raises is not None:
            raise self._raises
        return self._response

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeStreamingHTTPClient:
    """Stands in for httpx.Client for the .stream() call path. Records every
    stream() call it is handed, same pattern as FakeHTTPClient.post()."""

    def __init__(self, lines=None, status_code=200, text="", raises=None):
        self.calls = []
        self.closed = False
        self._lines = lines if lines is not None else [
            '{"message": {"content": "Leena: "}}',
            '{"message": {"content": "The fire is low."}}',
        ]
        self._status_code = status_code
        self._text = text
        self._raises = raises

    def stream(self, method, url, json=None, **kwargs):
        self.calls.append({"method": method, "url": url, "json": json, "kwargs": kwargs})
        if self._raises is not None:
            return FakeStreamContextManager(raises=self._raises)
        response = FakeStreamResponse(self._lines, self._status_code, self._text)
        return FakeStreamContextManager(response=response)

    def close(self):
        self.closed = True


def _streaming_client(http=None, **kwargs):
    defaults = dict(base_url="http://localhost:11434", model="hermes3:70b",
                    temperature=0.9, max_tokens=1024, timeout_s=600, num_ctx=8192)
    defaults.update(kwargs)
    return concurrent_llm.PooledOllamaClient(
        http_client=http if http is not None else FakeStreamingHTTPClient(), **defaults)


def test_complete_streaming_concatenates_content_across_ndjson_lines():
    client = _streaming_client()
    reply = client.complete_streaming("sys", [{"role": "user", "content": "hi"}])
    assert reply == "Leena: The fire is low."


def test_complete_streaming_sends_stream_true_unlike_complete():
    http = FakeStreamingHTTPClient()
    client = _streaming_client(http=http)
    client.complete_streaming("sys", [{"role": "user", "content": "hi"}])
    assert http.calls[0]["json"]["stream"] is True


def test_complete_streaming_posts_to_the_same_chat_endpoint():
    http = FakeStreamingHTTPClient()
    client = _streaming_client(http=http)
    client.complete_streaming("sys", [{"role": "user", "content": "hi"}])
    assert http.calls[0]["url"] == "http://localhost:11434/api/chat"
    assert http.calls[0]["json"]["model"] == "hermes3:70b"


def test_complete_streaming_skips_unparsable_lines_without_failing():
    http = FakeStreamingHTTPClient(lines=[
        '{"message": {"content": "A"}}',
        "not json at all",
        '{"message": {"content": "B"}}',
    ])
    client = _streaming_client(http=http)
    assert client.complete_streaming("sys", [{"role": "user", "content": "hi"}]) == "AB"


def test_complete_streaming_ignores_lines_with_no_content_field():
    http = FakeStreamingHTTPClient(lines=[
        '{"done": false}',
        '{"message": {"content": "A"}}',
    ])
    client = _streaming_client(http=http)
    assert client.complete_streaming("sys", [{"role": "user", "content": "hi"}]) == "A"


def test_complete_streaming_raises_llmerror_on_empty_content():
    http = FakeStreamingHTTPClient(lines=['{"done": true}'])
    client = _streaming_client(http=http)
    with pytest.raises(LLMError):
        client.complete_streaming("sys", [{"role": "user", "content": "hi"}])


def test_complete_streaming_raises_llmerror_on_http_error():
    http = FakeStreamingHTTPClient(status_code=500, text="internal error")
    client = _streaming_client(http=http)
    with pytest.raises(LLMError):
        client.complete_streaming("sys", [{"role": "user", "content": "hi"}])


def test_complete_streaming_raises_llmerror_on_timeout():
    http = FakeStreamingHTTPClient(raises=httpx.ReadTimeout("timed out"))
    client = _streaming_client(http=http)
    with pytest.raises(LLMError):
        client.complete_streaming("sys", [{"role": "user", "content": "hi"}])


def test_complete_streaming_calls_on_progress_at_least_once_for_a_slow_stream(monkeypatch):
    """The 2s throttle means a normal fast test stream never fires a
    callback — force the clock so we can prove the throttle logic itself
    (not just 'on_progress is never called')."""
    fake_time = [1000.0]

    def fake_monotonic():
        fake_time[0] += 3.0  # jump 3s on every read, past the 2s threshold
        return fake_time[0]

    monkeypatch.setattr(concurrent_llm.time, "monotonic", fake_monotonic)

    http = FakeStreamingHTTPClient(lines=[
        '{"message": {"content": "A"}}',
        '{"message": {"content": "B"}}',
    ])
    client = _streaming_client(http=http)
    seen = []
    client.complete_streaming("sys", [{"role": "user", "content": "hi"}],
                              on_progress=seen.append)
    assert len(seen) >= 1
    assert seen[0]["model"] == "hermes3:70b"
    assert seen[0]["n_decoded"] >= 1
    assert "tokens_per_s" in seen[0]


def test_complete_streaming_never_calls_on_progress_more_than_once_per_2s():
    """A fast test stream (no monkeypatched clock) generates all lines
    within microseconds — well under the throttle window — so on_progress
    must not fire at all here. This is the companion to the monkeypatched
    test above: together they pin the throttle at exactly 2s, not 0."""
    http = FakeStreamingHTTPClient(lines=[
        '{"message": {"content": "A"}}',
        '{"message": {"content": "B"}}',
        '{"message": {"content": "C"}}',
    ])
    client = _streaming_client(http=http)
    seen = []
    client.complete_streaming("sys", [{"role": "user", "content": "hi"}],
                              on_progress=seen.append)
    assert seen == []


def test_complete_streaming_with_no_on_progress_callback_does_not_crash():
    client = _streaming_client()
    reply = client.complete_streaming("sys", [{"role": "user", "content": "hi"}],
                                      on_progress=None)
    assert reply == "Leena: The fire is low."


# ── complete_streaming: generated text visible in logs, not just counts ───────

def test_complete_streaming_logs_generated_text_even_with_no_on_progress(caplog):
    """The whole point: the actual text must reach the log even when the
    caller supplied no on_progress callback at all (arc-stage caller may
    have none set — e.g. tests, or a caller that only wants the log)."""
    client = _streaming_client()
    caplog.set_level("INFO", logger="concurrent_llm")
    client.complete_streaming("sys", [{"role": "user", "content": "hi"}])
    joined = "\n".join(r.message for r in caplog.records)
    assert "Leena" in joined
    assert "fire is low" in joined


def test_complete_streaming_final_flush_logs_text_that_never_hit_the_2s_tick():
    """A fast fake stream finishes well under 2s, so the throttled log
    inside the loop never fires — the final flush after the loop is the
    ONLY thing that can put this text in the log. This is the regression
    test for silently losing the whole response when a call is fast."""
    client = _streaming_client()

    import logging as _logging
    records = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    concurrent_llm.log.addHandler(handler)
    concurrent_llm.log.setLevel("INFO")
    try:
        client.complete_streaming("sys", [{"role": "user", "content": "hi"}])
    finally:
        concurrent_llm.log.removeHandler(handler)

    joined = "\n".join(records)
    assert "final_text" in joined
    assert "Leena" in joined
