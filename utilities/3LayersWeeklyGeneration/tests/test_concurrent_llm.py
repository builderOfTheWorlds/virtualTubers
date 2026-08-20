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
            "message": {"content": "helen: The fire is low."}}
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
    assert reply == "helen: The fire is low."

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
