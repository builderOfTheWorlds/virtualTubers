"""WP-11 tests for app/character/llm.py: JSON completions from Ollama.

Frozen test list (playbook §4 WP-11, items 1-4, plus the gate's live smoke
test). D-15: every call goes to Ollama `/api/chat` with `format: "json"` and
`think` from the LLM profile (qwen3 reasoning otherwise eats the whole budget,
tools/qwen_worker/ollama_client.py:46-53). The reply is validated with
`character.shapes` and retried `max_retries` times with the errors appended to
the user message; then LLMError. The HTTP layer is an httpx.MockTransport
(tests/character/fakes_generator.py RecordingTransport); no network.
"""
import os

import httpx
import pytest

from fakes_generator import RecordingTransport, ollama_reply
from pending import require

llm = require("character.llm", "app/character/llm.py", wp="WP-11")
from character import config  # noqa: E402  (promoted in WP-03, before WP-11)

BASE_URL = "http://ollama.test:11434"
SHAPE = {"type": "object", "required": ["summary", "nodes"],
         "properties": {"summary": {"type": "str"},
                        "nodes": {"type": "list", "items": {
                            "type": "object", "required": ["name", "statement"],
                            "properties": {"name": {"type": "str"},
                                           "statement": {"type": "str"}}}}}}
GOOD = {"summary": "I spent the day chasing the Corvane latency graph.",
        "nodes": [{"name": "knows-corvane-renewal-is-due",
                   "statement": "I know Corvane's renewal is due."}]}
SYSTEM = "You write in the first person. Reply with JSON only."
USER = "Summarise my day."


@pytest.fixture
def profile():
    """The generator profile from the repo config (qwen3.8:27b, think false, D-15)."""
    return config.load(env={}).llm.profiles["generator"]


def _call(profile, replies, max_retries=2):
    transport = RecordingTransport(replies)
    with transport.client() as client:
        result = llm.complete_json(profile, SYSTEM, USER, SHAPE, base_url=BASE_URL,
                                   max_retries=max_retries, client=client)
    return result, transport.requests


# T11.1
def test_request_body_has_format_json_think_false_and_profile_model(profile):
    result, requests = _call(profile, [ollama_reply(GOOD)])
    assert result == GOOD
    assert len(requests) == 1
    request = requests[0]
    assert request["method"] == "POST"
    assert request["url"] == f"{BASE_URL}/api/chat"
    body = request["json"]
    assert body["format"] == "json"
    assert body["think"] is False
    assert body["stream"] is False
    assert body["model"] == profile.model == "qwen3.8:27b"
    assert body["options"]["temperature"] == profile.temperature
    assert body["options"]["num_ctx"] == profile.num_ctx
    assert body["messages"][0] == {"role": "system", "content": SYSTEM}
    assert body["messages"][-1] == {"role": "user", "content": USER}


# T11.2
def test_invalid_json_is_retried_and_second_attempt_succeeds(profile):
    result, requests = _call(profile, [ollama_reply("{not json at all"), ollama_reply(GOOD)])
    assert result == GOOD
    assert len(requests) == 2
    assert "json" in requests[1]["json"]["messages"][-1]["content"].lower()


# T11.2 (a JSON value that is not an object is also a bad reply)
def test_non_object_json_is_retried(profile):
    result, requests = _call(profile, [ollama_reply("[1, 2, 3]"), ollama_reply(GOOD)])
    assert result == GOOD and len(requests) == 2


# T11.3
def test_shape_error_is_retried_with_the_error_in_the_user_message(profile):
    missing_summary = {"nodes": []}
    result, requests = _call(profile, [ollama_reply(missing_summary), ollama_reply(GOOD)])
    assert result == GOOD
    assert len(requests) == 2
    retry_user = requests[1]["json"]["messages"][-1]
    assert retry_user["role"] == "user"
    assert retry_user["content"].startswith(USER)          # the original request is kept
    assert "$.summary" in retry_user["content"]            # the shapes error path is listed
    assert requests[1]["json"]["messages"][0] == {"role": "system", "content": SYSTEM}


# T11.4
def test_retries_run_out_and_raise_llm_error(profile):
    with pytest.raises(llm.LLMError) as err:
        _call(profile, [ollama_reply({"nodes": "wrong"})], max_retries=2)
    assert err.value.attempts == 3                          # the first call + max_retries
    assert err.value.errors                                 # the last attempt's reasons
    assert "$.summary" in str(err.value)


# T11.4 (HTTP failures count as failed attempts, then LLMError)
def test_http_errors_are_retried_then_raise_llm_error(profile):
    transport = RecordingTransport([(500, "model 'x' not found"),
                                    httpx.ConnectError("refused")])
    with transport.client() as client, pytest.raises(llm.LLMError):
        llm.complete_json(profile, SYSTEM, USER, SHAPE, base_url=BASE_URL,
                          max_retries=1, client=client)
    assert len(transport.requests) == 2


# T11.1 (the config-driven wrapper resolves the profile, base_url and max_retries)
def test_json_llm_uses_the_config_profile_and_base_url():
    cfg = config.load(env={})
    transport = RecordingTransport([ollama_reply(GOOD)])
    with transport.client() as client:
        json_llm = llm.JsonLLM(cfg.llm, client=client)
        result = json_llm.complete_json("summary", SYSTEM, USER, SHAPE)
        with pytest.raises(KeyError):
            json_llm.complete_json("no-such-profile", SYSTEM, USER, SHAPE)
    assert result == GOOD
    assert len(transport.requests) == 1                     # the unknown profile made no call
    body = transport.requests[0]["json"]
    assert transport.requests[0]["url"] == cfg.llm.base_url.rstrip("/") + "/api/chat"
    assert body["model"] == cfg.llm.profiles["summary"].model
    assert body["options"]["temperature"] == cfg.llm.profiles["summary"].temperature


# WP-11 gate: live smoke test against the gx10 Ollama (skipped unless asked for)
@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("CHARACTER_LIVE_LLM") != "1",
                    reason="live LLM smoke test: set CHARACTER_LIVE_LLM=1")
def test_live_smoke_returns_valid_json():
    cfg = config.load()
    shape = {"type": "object", "required": ["name", "statement"],
             "properties": {"name": {"type": "str"}, "statement": {"type": "str"}}}
    result = llm.JsonLLM(cfg.llm).complete_json(
        "generator",
        "You are the Tech Lead of a small fraud-detection company. Write in the first "
        "person. Reply with one JSON object with keys name and statement.",
        "Give one node: name is kebab-case and starts with 'trusts', statement is one "
        "first-person sentence.",
        shape)
    assert isinstance(result["name"], str) and isinstance(result["statement"], str)
