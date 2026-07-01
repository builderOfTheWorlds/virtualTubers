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
