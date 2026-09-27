"""Tests for services/3layer-generator/draft_submitter.py — the opt-in
auto-submit of a published episode to message-api as a review draft
(docs/draft_submitter.md).

No network and no database: every test injects a fake `http_post`, so the
suite runs anywhere. The runner-side wiring (when auto-submit fires, and that
a failure never fails the job) lives in test_service_runner.py.
"""
import json

import httpx
import pytest

import draft_submitter


class FakeResponse:
    def __init__(self, status_code, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def recording_post(response=None, exc=None):
    calls = []

    def post(url, content, headers, params, timeout):
        calls.append({"url": url, "content": content, "headers": headers,
                      "params": params, "timeout": timeout})
        if exc is not None:
            raise exc
        return response
    post.calls = calls
    return post


@pytest.fixture
def episode(tmp_path):
    path = tmp_path / "episode.json"
    path.write_text(json.dumps({"source": "alpha", "events": []}), encoding="utf-8")
    return path


# ── from_env ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", [
    {},
    {"AUTO_SUBMIT_DRAFTS": ""},
    {"AUTO_SUBMIT_DRAFTS": "false"},
    {"AUTO_SUBMIT_DRAFTS": "0"},
    {"AUTO_SUBMIT_DRAFTS": "nope", "MESSAGE_API_URL": "http://x:1"},
])
def test_from_env_off_unless_explicitly_enabled(env):
    assert draft_submitter.from_env(env) is None


@pytest.mark.parametrize("flag", ["true", "TRUE", "1", "yes", "on", " true "])
def test_from_env_on_with_defaults(flag):
    sub = draft_submitter.from_env({"AUTO_SUBMIT_DRAFTS": flag})
    assert isinstance(sub, draft_submitter.DraftSubmitter)
    assert sub.url == "http://127.0.0.1:8090/replays"
    assert sub.timeout_s == draft_submitter.DEFAULT_TIMEOUT_S


def test_from_env_reads_url_and_timeout():
    sub = draft_submitter.from_env({
        "AUTO_SUBMIT_DRAFTS": "true",
        "MESSAGE_API_URL": "http://message-api:8000/",
        "AUTO_SUBMIT_TIMEOUT_S": "15",
    })
    assert sub.url == "http://message-api:8000/replays"
    assert sub.timeout_s == 15.0


@pytest.mark.parametrize("bad", ["abc", "0", "-5"])
def test_from_env_bad_timeout_falls_back_to_default(bad):
    sub = draft_submitter.from_env({"AUTO_SUBMIT_DRAFTS": "1", "AUTO_SUBMIT_TIMEOUT_S": bad})
    assert sub is not None
    assert sub.timeout_s == draft_submitter.DEFAULT_TIMEOUT_S


# ── __call__ ────────────────────────────────────────────────────────────────

def test_submit_posts_raw_episode_as_draft(episode):
    post = recording_post(FakeResponse(200, {"name": "ep-1", "created": True, "status": "draft"}))
    sub = draft_submitter.DraftSubmitter("http://mapi:8090", timeout_s=5, http_post=post)

    outcome = sub(episode, "ep-1")

    assert outcome == {"status": "submitted", "name": "ep-1",
                       "url": "http://mapi:8090/replays", "http_status": 200}
    call = post.calls[0]
    assert call["url"] == "http://mapi:8090/replays"
    assert call["content"] == episode.read_bytes()
    assert call["headers"] == {"Content-Type": "application/json"}
    # Never overwrite, always draft: an auto-submit must not replace (or
    # un-review) anything already in the library.
    assert call["params"] == {"status": "draft", "uploaded_by": "3layer-generator",
                              "name": "ep-1"}
    assert "overwrite" not in call["params"]
    assert call["timeout"] == 5


def test_submit_without_name_lets_message_api_use_source(episode):
    post = recording_post(FakeResponse(200, {"name": "alpha"}))
    outcome = draft_submitter.DraftSubmitter(http_post=post)(episode, None)
    assert "name" not in post.calls[0]["params"]
    assert outcome["name"] == "alpha"


@pytest.mark.parametrize("status_code,data,expected_error", [
    (400, {"detail": "leak audit: rule aws_key matched"}, "leak audit"),
    (409, {"detail": "episode 'ep-1' already exists"}, "already exists"),
    (503, {"detail": "postgres unavailable: down"}, "postgres unavailable"),
    (502, None, "Bad Gateway"),
])
def test_submit_http_error_is_recorded_not_raised(episode, status_code, data, expected_error):
    post = recording_post(FakeResponse(status_code, data, text="Bad Gateway"))

    outcome = draft_submitter.DraftSubmitter(http_post=post)(episode, "ep-1")

    assert outcome["status"] == "failed"
    assert outcome["http_status"] == status_code
    assert expected_error in outcome["error"]


def test_submit_error_detail_is_truncated(episode):
    post = recording_post(FakeResponse(500, None, text="x" * 5000))
    outcome = draft_submitter.DraftSubmitter(http_post=post)(episode, "ep-1")
    assert len(outcome["error"]) == 500


def test_submit_unreachable_message_api_is_recorded_not_raised(episode):
    post = recording_post(exc=httpx.ConnectError("connection refused"))

    outcome = draft_submitter.DraftSubmitter(http_post=post)(episode, "ep-1")

    assert outcome["status"] == "failed"
    assert "unreachable" in outcome["error"]
    assert "http_status" not in outcome


def test_submit_unexpected_exception_is_recorded_not_raised(episode):
    post = recording_post(exc=RuntimeError("boom"))
    outcome = draft_submitter.DraftSubmitter(http_post=post)(episode, "ep-1")
    assert outcome["status"] == "failed"
    assert "boom" in outcome["error"]


def test_submit_missing_episode_file_is_recorded_without_posting(tmp_path):
    post = recording_post(FakeResponse(200, {}))
    outcome = draft_submitter.DraftSubmitter(http_post=post)(tmp_path / "nope.json", "ep-1")
    assert outcome["status"] == "failed"
    assert "cannot read" in outcome["error"]
    assert post.calls == []
