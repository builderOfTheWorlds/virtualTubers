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


# ── OpenAICompatClient: the vLLM / OpenAI-compatible wire path ────────────────
#
# vLLM (served via the vllm-qwen3.8-27b container on :8092) speaks the
# OpenAI /v1/chat/completions protocol, not Ollama's /api/chat. The
# generator's from_profile must build an OpenAICompatClient for it, and that
# client must implement the same complete / complete_streaming / close /
# context-manager surface with the same LLMError semantics as
# PooledOllamaClient, so the arc/segment/dialogue layers work unchanged.

OPENAI_CHAT_URL = "http://localhost:8092/v1/chat/completions"


class FakeOpenAIResponse:
    """Stands in for the httpx.Response returned by a non-streaming
    /v1/chat/completions call."""

    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"role": "assistant", "content": "Leena: The fire is low."}}]}
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=self)

    def json(self):
        return self._payload


def _openai_client(http=None, **kwargs):
    defaults = dict(base_url="http://localhost:8092", model="qwen3.8-27b",
                    temperature=0.9, max_tokens=1024, timeout_s=600,
                    api_key="test-key")
    defaults.update(kwargs)
    # The shared `http` fixture defaults to an Ollama-shaped payload; hand it
    # an OpenAI-shaped one when the caller didn't provide its own.
    if http is None:
        http = FakeHTTPClient(response=FakeOpenAIResponse())
    return concurrent_llm.OpenAICompatClient(http_client=http, **defaults)


def test_from_profile_builds_an_openai_client_for_a_vllm_profile():
    profile = {"provider": "vllm", "base_url": "http://localhost:8092",
               "model": "qwen3.8-27b", "temperature": 0.9, "max_tokens": 1024,
               "timeout_s": 600, "api_key": "k"}
    client = concurrent_llm.from_profile(profile)
    try:
        assert isinstance(client, concurrent_llm.OpenAICompatClient)
        assert client.model == "qwen3.8-27b"
        assert client.base_url == "http://localhost:8092"
        assert client.api_key == "k"
    finally:
        client.close()


def test_from_profile_reads_the_key_from_api_key_env(monkeypatch):
    """Committed configs name an env var instead of carrying the secret."""
    monkeypatch.setenv("TEST_VLLM_KEY", "from-env")
    client = concurrent_llm.from_profile(
        {"provider": "vllm", "model": "m", "base_url": "http://x",
         "api_key_env": "TEST_VLLM_KEY"})
    try:
        assert client.api_key == "from-env"
    finally:
        client.close()


def test_from_profile_inline_api_key_wins_over_api_key_env(monkeypatch):
    monkeypatch.setenv("TEST_VLLM_KEY", "from-env")
    client = concurrent_llm.from_profile(
        {"provider": "vllm", "model": "m", "base_url": "http://x",
         "api_key": "inline", "api_key_env": "TEST_VLLM_KEY"})
    try:
        assert client.api_key == "inline"
    finally:
        client.close()


def test_from_profile_unset_api_key_env_sends_no_key(monkeypatch):
    monkeypatch.delenv("TEST_VLLM_KEY", raising=False)
    client = concurrent_llm.from_profile(
        {"provider": "vllm", "model": "m", "base_url": "http://x",
         "api_key_env": "TEST_VLLM_KEY"})
    try:
        assert client.api_key is None
        assert "Authorization" not in client._headers()
    finally:
        client.close()


def test_from_profile_treats_openai_and_openai_compatible_the_same():
    for provider in ("openai", "openai-compatible"):
        client = concurrent_llm.from_profile(
            {"provider": provider, "model": "m", "base_url": "http://x"})
        try:
            assert isinstance(client, concurrent_llm.OpenAICompatClient)
        finally:
            client.close()


def test_openai_client_posts_the_chat_completions_payload_and_returns_content():
    http = FakeHTTPClient(response=FakeOpenAIResponse())
    reply = _openai_client(http=http).complete(
        "You plan segments.", [{"role": "user", "content": "hi"}])
    assert reply == "Leena: The fire is low."

    call = http.calls[0]
    assert call["url"] == OPENAI_CHAT_URL
    assert call["json"]["model"] == "qwen3.8-27b"
    assert call["json"]["stream"] is False
    assert call["json"]["temperature"] == 0.9
    assert call["json"]["max_tokens"] == 1024
    assert call["json"]["messages"][0] == {
        "role": "system", "content": "You plan segments."}
    assert call["json"]["messages"][1] == {"role": "user", "content": "hi"}


def test_openai_client_sends_the_bearer_auth_header():
    http = FakeHTTPClient(response=FakeOpenAIResponse())
    _openai_client(http=http).complete("sys", [{"role": "user", "content": "hi"}])
    headers = http.calls[0]["kwargs"]["headers"]
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"] == "application/json"


def test_openai_client_omits_the_auth_header_when_there_is_no_key():
    http = FakeHTTPClient(response=FakeOpenAIResponse())
    _openai_client(http=http, api_key=None).complete(
        "sys", [{"role": "user", "content": "hi"}])
    assert "Authorization" not in http.calls[0]["kwargs"]["headers"]


def test_openai_client_merges_extra_body_verbatim():
    """vLLM + Qwen3 thinking control lives in chat_template_kwargs — it must
    reach the wire body untouched."""
    http = FakeHTTPClient(response=FakeOpenAIResponse())
    _openai_client(http=http,
                   extra_body={"chat_template_kwargs": {"enable_thinking": False}}) \
        .complete("sys", [{"role": "user", "content": "hi"}])
    body = http.calls[0]["json"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    # Extra body must not clobber the fields the client owns.
    assert body["model"] == "qwen3.8-27b"
    assert body["stream"] is False


def test_openai_client_sends_the_configured_timeout():
    http = FakeHTTPClient(response=FakeOpenAIResponse())
    _openai_client(http=http).complete("sys", [{"role": "user", "content": "hi"}])
    assert http.calls[0]["kwargs"].get("timeout") == 600


def test_openai_client_trailing_slash_on_base_url_is_stripped():
    http = FakeHTTPClient(response=FakeOpenAIResponse())
    _openai_client(http=http, base_url="http://localhost:8092/") \
        .complete("sys", [{"role": "user", "content": "hi"}])
    assert http.calls[0]["url"] == OPENAI_CHAT_URL


def test_openai_client_reuses_the_pooled_http_client():
    http = FakeHTTPClient(response=FakeOpenAIResponse())
    client = _openai_client(http=http)
    for _ in range(4):
        client.complete("sys", [{"role": "user", "content": "hi"}])
    assert len(http.calls) == 4


def test_openai_client_http_error_raises_llmerror_carrying_the_body():
    http = FakeHTTPClient(response=FakeOpenAIResponse(
        status_code=401, text="Unauthorized"))
    client = _openai_client(http=http)
    with pytest.raises(LLMError) as excinfo:
        client.complete("sys", [{"role": "user", "content": "hi"}])
    assert "Unauthorized" in str(excinfo.value)


def test_openai_client_timeout_raises_llmerror(caplog):
    http = FakeHTTPClient(raises=httpx.ReadTimeout("timed out"))
    client = _openai_client(http=http)
    caplog.set_level("ERROR")
    with pytest.raises(LLMError):
        client.complete("sys", [{"role": "user", "content": "hi"}])
    assert caplog.records, "a timeout was raised without an ERROR log line"


def test_openai_client_connection_error_raises_llmerror():
    http = FakeHTTPClient(raises=httpx.ConnectError("connection refused"))
    client = _openai_client(http=http)
    with pytest.raises(LLMError):
        client.complete("sys", [{"role": "user", "content": "hi"}])


def test_openai_client_malformed_reply_raises_llmerror_not_keyerror():
    http = FakeHTTPClient(response=FakeOpenAIResponse(payload={"unexpected": "shape"}))
    client = _openai_client(http=http)
    with pytest.raises(LLMError):
        client.complete("sys", [{"role": "user", "content": "hi"}])


def test_openai_client_none_content_raises_llmerror():
    """A reasoning model that spends the whole budget on reasoning returns
    content=None — that must surface as LLMError, not crash the pool with a
    TypeError on the None."""
    http = FakeHTTPClient(response=FakeOpenAIResponse(
        payload={"choices": [{"message": {"role": "assistant", "content": None,
                                          "reasoning": "long chain of thought"}}]}))
    client = _openai_client(http=http)
    with pytest.raises(LLMError):
        client.complete("sys", [{"role": "user", "content": "hi"}])


def test_openai_client_works_as_a_context_manager():
    with concurrent_llm.OpenAICompatClient(
            "http://localhost:8092", "m", 0.7, 512, api_key="k") as client:
        assert client.http_client.is_closed is False
    assert client.http_client.is_closed is True


def test_openai_client_close_is_idempotent():
    """No injected client -> it builds a real httpx.Client it must own, and
    closing twice must be safe."""
    client = concurrent_llm.OpenAICompatClient(
        "http://localhost:8092", "m", 0.7, 512, api_key="k")
    try:
        client.close()
        client.close()
        assert client.http_client.is_closed is True
    finally:
        client.close()


# ── OpenAICompatClient.complete_streaming: SSE, not Ollama NDJSON ─────────────
#
# The SSE wire shape differs from Ollama: each line is "data: <json>" (or
# "data: [DONE]"), and the content delta lives at
# choices[0].delta.content, with Qwen3 reasoning in a separate
# delta.reasoning field that must NOT be accumulated.

_SSE_DEFAULT_LINES = [
    'data: {"choices": [{"delta": {"role": "assistant", "content": "Leena: "}}]}',
    'data: {"choices": [{"delta": {"content": "The fire is low."}}]}',
    "data: [DONE]",
]


def _sse_streaming_client(http=None, **kwargs):
    defaults = dict(base_url="http://localhost:8092", model="qwen3.8-27b",
                    temperature=0.9, max_tokens=1024, timeout_s=600,
                    api_key="test-key")
    defaults.update(kwargs)
    return concurrent_llm.OpenAICompatClient(
        http_client=http if http is not None else FakeStreamingHTTPClient(
            lines=list(_SSE_DEFAULT_LINES)), **defaults)


def test_openai_streaming_concatenates_content_across_sse_lines():
    client = _sse_streaming_client()
    reply = client.complete_streaming("sys", [{"role": "user", "content": "hi"}])
    assert reply == "Leena: The fire is low."


def test_openai_streaming_posts_to_chat_completions_with_stream_true():
    http = FakeStreamingHTTPClient(lines=list(_SSE_DEFAULT_LINES))
    client = _sse_streaming_client(http=http)
    client.complete_streaming("sys", [{"role": "user", "content": "hi"}])
    assert http.calls[0]["url"] == OPENAI_CHAT_URL
    assert http.calls[0]["json"]["stream"] is True
    assert http.calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer test-key"


def test_openai_streaming_ignores_comment_and_blank_lines():
    http = FakeStreamingHTTPClient(lines=[
        ": OPENAI is the default provider",
        "",
        'data: {"choices": [{"delta": {"content": "A"}}]}',
        "   ",
    ])
    client = _sse_streaming_client(http=http)
    assert client.complete_streaming("sys", [{"role": "user", "content": "hi"}]) == "A"


def test_openai_streaming_skips_unparsable_sse_payloads():
    http = FakeStreamingHTTPClient(lines=[
        "data: {not valid json",
        'data: {"choices": [{"delta": {"content": "A"}}]}',
        'data: {"choices": [{"delta": {"content": "B"}}]}',
    ])
    client = _sse_streaming_client(http=http)
    assert client.complete_streaming("sys", [{"role": "user", "content": "hi"}]) == "AB"


def test_openai_streaming_accumulates_only_content_not_reasoning():
    """Qwen3 with --reasoning-parser puts chain-of-thought in delta.reasoning.
    It must never leak into the parsed plan."""
    http = FakeStreamingHTTPClient(lines=[
        'data: {"choices": [{"delta": {"reasoning": "I should plan this carefully"}}]}',
        'data: {"choices": [{"delta": {"content": "id: seg-1"}}]}',
        'data: {"choices": [{"delta": {"reasoning": "now the title"}}]}',
        'data: {"choices": [{"delta": {"content": "\\ntitle: The Shift"}}]}',
        "data: [DONE]",
    ])
    client = _sse_streaming_client(http=http)
    reply = client.complete_streaming("sys", [{"role": "user", "content": "hi"}])
    assert "reasoning" not in reply.lower()
    assert "I should plan" not in reply
    assert reply == "id: seg-1\ntitle: The Shift"


def test_openai_streaming_ignores_deltas_with_no_content():
    http = FakeStreamingHTTPClient(lines=[
        'data: {"choices": [{"delta": {"role": "assistant"}}]}',
        'data: {"choices": [{"delta": {"content": "A"}}]}',
        'data: {"choices": []}',
    ])
    client = _sse_streaming_client(http=http)
    assert client.complete_streaming("sys", [{"role": "user", "content": "hi"}]) == "A"


def test_openai_streaming_raises_llmerror_on_empty_content():
    http = FakeStreamingHTTPClient(lines=[
        'data: {"choices": [{"delta": {"reasoning": "thoughts only"}}]}',
        "data: [DONE]",
    ])
    client = _sse_streaming_client(http=http)
    with pytest.raises(LLMError):
        client.complete_streaming("sys", [{"role": "user", "content": "hi"}])


def test_openai_streaming_raises_llmerror_on_http_error():
    http = FakeStreamingHTTPClient(status_code=500, text="internal error",
                                   lines=list(_SSE_DEFAULT_LINES))
    client = _sse_streaming_client(http=http)
    with pytest.raises(LLMError):
        client.complete_streaming("sys", [{"role": "user", "content": "hi"}])


def test_openai_streaming_raises_llmerror_on_timeout():
    http = FakeStreamingHTTPClient(raises=httpx.ReadTimeout("timed out"))
    client = _sse_streaming_client(http=http)
    with pytest.raises(LLMError):
        client.complete_streaming("sys", [{"role": "user", "content": "hi"}])


def test_openai_streaming_calls_on_progress_when_the_clock_is_forced(monkeypatch):
    fake_time = [1000.0]

    def fake_monotonic():
        fake_time[0] += 3.0
        return fake_time[0]

    monkeypatch.setattr(concurrent_llm.time, "monotonic", fake_monotonic)

    http = FakeStreamingHTTPClient(lines=list(_SSE_DEFAULT_LINES))
    client = _sse_streaming_client(http=http)
    seen = []
    client.complete_streaming("sys", [{"role": "user", "content": "hi"}],
                              on_progress=seen.append)
    assert len(seen) >= 1
    assert seen[0]["model"] == "qwen3.8-27b"
    assert seen[0]["n_decoded"] >= 1


def test_openai_streaming_logs_generated_text_even_with_no_on_progress(caplog):
    client = _sse_streaming_client()
    caplog.set_level("INFO", logger="concurrent_llm")
    client.complete_streaming("sys", [{"role": "user", "content": "hi"}])
    joined = "\n".join(r.message for r in caplog.records)
    assert "Leena" in joined
    assert "fire is low" in joined
