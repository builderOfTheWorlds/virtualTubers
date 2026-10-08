import httpx
import pytest

from llm_client import LLMError, OllamaClient, ClaudeClient, build_llm_client


def test_build_llm_client_defaults_to_ollama():
    client = build_llm_client({})
    assert isinstance(client, OllamaClient)
    assert client.model == "mistral"
    assert client.base_url == "http://localhost:11434"


def test_build_llm_client_ollama_reads_config():
    config = {"llm": {"provider": "ollama", "base_url": "http://host:11434", "model": "llama3", "temperature": 0.2, "max_tokens": 500}}
    client = build_llm_client(config)
    assert isinstance(client, OllamaClient)
    assert client.base_url == "http://host:11434"
    assert client.model == "llama3"
    assert client.temperature == 0.2
    assert client.max_tokens == 500


def test_build_llm_client_claude_defaults_model_when_not_claude_named():
    config = {"llm": {"provider": "claude", "model": "mistral", "max_tokens": 800}}
    client = build_llm_client(config)
    assert isinstance(client, ClaudeClient)
    assert client.model == "claude-opus-4-8"
    assert client.max_tokens == 800


def test_build_llm_client_claude_respects_explicit_claude_model():
    config = {"llm": {"provider": "claude", "model": "claude-haiku-4-5"}}
    client = build_llm_client(config)
    assert isinstance(client, ClaudeClient)
    assert client.model == "claude-haiku-4-5"


def test_build_llm_client_unknown_provider_raises():
    with pytest.raises(LLMError):
        build_llm_client({"llm": {"provider": "bogus"}})


def test_ollama_client_complete_parses_response(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "hello from ollama"}}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("llm_client.httpx.post", fake_post)

    client = OllamaClient("http://localhost:11434", "mistral", 0.7, 1024)
    result = client.complete("system prompt", [{"role": "user", "content": "hi"}])

    assert result == "hello from ollama"
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["json"]["messages"][0] == {"role": "system", "content": "system prompt"}


def test_ollama_client_complete_includes_response_body_on_http_error(monkeypatch):
    class FakeResponse:
        status_code = 500
        text = "model 'qwen2.5:14b' not found, try pulling it first"

        def raise_for_status(self):
            raise httpx.HTTPStatusError("Server error", request=None, response=self)

    monkeypatch.setattr("llm_client.httpx.post", lambda url, json, timeout: FakeResponse())

    client = OllamaClient("http://localhost:11434", "qwen2.5:14b", 0.7, 1024)

    with pytest.raises(LLMError, match="not found, try pulling it first"):
        client.complete("system prompt", [{"role": "user", "content": "hi"}])


# ── P0.3: VLLMClient (OpenAI-compatible, native reasoning) ─────────────────
# The "fake SSE server" is an httpx.MockTransport: real httpx request/
# response objects and real SSE framing, no network.
import json as _json

from llm_client import VLLMClient

SECRET = "sk-test-SECRET-do-not-log-123"


def _sse(events, done=True):
    lines = [": vllm keep-alive comment", ""]
    for ev in events:
        lines.append("data: " + _json.dumps(ev))
        lines.append("")
    if done:
        lines.append("data: [DONE]")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _delta(finish=None, **delta):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _client(handler, **kw):
    kw.setdefault("api_key", SECRET)
    return VLLMClient(
        "http://vllm:8092", "qwen-test", 0.6, 512,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kw,
    )


def test_vllm_stream_splits_reasoning_and_content():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, content=_sse([
            _delta(role="assistant"),
            _delta(reasoning_content="I should "),
            _delta(reasoning="greet them."),   # newer vLLM field name
            _delta(content="Hello"),
            _delta(content=", traveller.", finish="stop"),
        ]), headers={"content-type": "text/event-stream"})

    reasoning_chunks, content_chunks = [], []
    reasoning, content = _client(handler).complete_stream(
        "sys", [{"role": "user", "content": "hi"}],
        on_reasoning=reasoning_chunks.append, on_content=content_chunks.append,
    )

    assert reasoning == "I should greet them."
    assert content == "Hello, traveller."
    assert reasoning_chunks == ["I should ", "greet them."]
    assert content_chunks == ["Hello", ", traveller."]
    assert seen["url"] == "http://vllm:8092/v1/chat/completions"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"]["stream"] is True
    assert seen["body"]["model"] == "qwen-test"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_vllm_stream_per_call_max_tokens_override():
    seen = {}

    def handler(request):
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, content=_sse([_delta(content="ok", finish="stop")]))

    _client(handler).complete_stream("s", [], max_tokens=64)
    assert seen["body"]["max_tokens"] == 64


def test_vllm_stream_empty_content_after_reasoning_raises_with_finish_reason():
    # Pitfall from the handover: the model spends the budget thinking.
    def handler(request):
        return httpx.Response(200, content=_sse([
            _delta(reasoning_content="thinking forever"),
            _delta(finish="length"),
        ]))

    with pytest.raises(LLMError, match="length"):
        _client(handler).complete_stream("s", [{"role": "user", "content": "x"}])


def test_vllm_stream_skips_malformed_sse_line():
    def handler(request):
        body = b"data: {not json\n\n" + _sse([_delta(content="fine", finish="stop")])
        return httpx.Response(200, content=body)

    assert _client(handler).complete_stream("s", []) == ("", "fine")


def test_vllm_complete_non_stream_returns_content_only():
    seen = {}

    def handler(request):
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "reasoning_content": "secret plan", "content": "Spoken line."}}]})

    assert _client(handler).complete("s", [{"role": "user", "content": "x"}]) == "Spoken line."
    assert seen["body"]["stream"] is False


def test_vllm_http_error_surfaces_body_but_never_the_key(caplog):
    def handler(request):
        return httpx.Response(401, text="invalid api key")

    caplog.set_level("DEBUG")
    with pytest.raises(LLMError) as excinfo:
        _client(handler).complete("s", [])
    assert "401" in str(excinfo.value) and "invalid api key" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)
    assert SECRET not in caplog.text


def test_vllm_connection_error_is_llm_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMError, match="refused"):
        _client(handler).complete_stream("s", [])


def test_vllm_key_never_in_repr_or_str():
    client = _client(lambda r: httpx.Response(200))
    assert SECRET not in repr(client)
    assert SECRET not in str(client)
    assert SECRET not in repr(vars(client))  # stored privately, still not echoed


def test_vllm_no_key_sends_no_auth_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=_sse([_delta(content="x", finish="stop")]))

    _client(handler, api_key=None).complete_stream("s", [])
    assert seen["auth"] is None


def test_vllm_extra_body_merged_into_request():
    seen = {}

    def handler(request):
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, content=_sse([_delta(content="x", finish="stop")]))

    _client(handler, extra_body={"chat_template_kwargs": {"enable_thinking": False}}
            ).complete_stream("s", [])
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_vllm_supports_native_reasoning_flag():
    assert VLLMClient.supports_native_reasoning is True
    assert not getattr(OllamaClient, "supports_native_reasoning", False)


def test_build_llm_client_vllm_reads_key_from_named_env_var(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("MY_VLLM_KEY", SECRET)
    client = build_llm_client({"llm": {
        "provider": "vllm", "base_url": "http://h:8092", "model": "m",
        "api_key_env": "MY_VLLM_KEY", "max_tokens": 300, "temperature": 0.3,
        "timeout_s": 90,
    }})
    assert isinstance(client, VLLMClient)
    assert client.base_url == "http://h:8092"
    assert client.model == "m"
    assert client.max_tokens == 300
    assert client.temperature == 0.3
    assert client.timeout_s == 90
    assert client.has_api_key


def test_build_llm_client_vllm_defaults_and_env_overrides(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "vllm")
    monkeypatch.setenv("LLM_BASE_URL", "http://host.docker.internal:8092")
    monkeypatch.setenv("LLM_MODEL", "served-name")
    monkeypatch.setenv("VLLM_API_KEY", SECRET)
    client = build_llm_client({"llm": {"provider": "ollama", "model": "mistral"}})
    assert isinstance(client, VLLMClient)
    assert client.base_url == "http://host.docker.internal:8092"
    assert client.model == "served-name"   # LLM_MODEL only applies to vllm
    assert client.has_api_key               # default api_key_env = VLLM_API_KEY


def test_build_llm_client_ollama_ignores_llm_model_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_MODEL", "served-name")
    client = build_llm_client({"llm": {"provider": "ollama", "model": "mistral"}})
    assert client.model == "mistral"


def test_build_llm_client_vllm_missing_key_env_is_allowed(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)
    client = build_llm_client({"llm": {"provider": "vllm"}})
    assert not client.has_api_key
    assert client.base_url == "http://localhost:8092"


def test_vllm_stream_reasoning_budget_sent_as_thinking_token_budget():
    seen = {}

    def handler(request):
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, content=_sse([_delta(content="x", finish="stop")]))

    _client(handler).complete_stream("s", [], reasoning_budget=200)
    assert seen["body"]["thinking_token_budget"] == 200


def test_vllm_stream_no_reasoning_budget_omits_the_field():
    seen = {}

    def handler(request):
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, content=_sse([_delta(content="x", finish="stop")]))

    _client(handler).complete_stream("s", [])
    assert "thinking_token_budget" not in seen["body"]
