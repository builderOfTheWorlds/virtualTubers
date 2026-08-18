"""
Tests for services/control-panel/panel.py.
Requires services/control-panel/requirements.txt installed (fastapi, jinja2,
httpx, python-multipart) in addition to the root requirements.txt.

panel._mapi_request is the single chokepoint every route uses to talk to
message-api (see panel.py's docstring) — these tests monkeypatch it directly
with an AsyncMock rather than mocking httpx itself, the same "mock the one
seam" spirit tests/test_message_api.py uses for api.producer.send.
"""
import asyncio
import io
import pathlib
import sys
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "control-panel"))

import panel  # noqa: E402


def mapi_result(ok=True, status_code=200, data=None, error=None):
    return panel.MapiResult(ok=ok, status_code=status_code, data=data, error=error)


@pytest.fixture(autouse=True)
def _reset_log_types():
    """KNOWN_LOG_TYPES is a module-level list mutated by /log-filter/add —
    reset it around every test so tests don't leak state into each other."""
    original = list(panel.KNOWN_LOG_TYPES)
    yield
    panel.KNOWN_LOG_TYPES[:] = original


@pytest.fixture
def client(monkeypatch):
    mock = AsyncMock(return_value=mapi_result())
    monkeypatch.setattr(panel, "_mapi_request", mock)
    with TestClient(panel.app) as c:
        c.mapi = mock  # stash for assertions
        yield c


# ── dashboard ────────────────────────────────────────────────────────────
def test_dashboard_renders_worker_and_replay_data(client):
    async def side_effect(method, path, **kwargs):
        if path.startswith("/workers/"):
            return mapi_result(data={"worker_id": path.split("/")[-1], "enabled": True})
        if path.startswith("/log-filter/"):
            return mapi_result(data={"type": "status_update", "excluded": True})
        if path == "/replays":
            return mapi_result(data={"episodes": [{"name": "ep1", "project": "demo", "event_count": 3}]})
        raise AssertionError(f"unexpected call {method} {path}")

    client.mapi.side_effect = side_effect
    resp = client.get("/")
    assert resp.status_code == 200
    assert "coder-native" in resp.text
    assert "ep1" in resp.text
    assert "enabled" in resp.text


def test_dashboard_survives_message_api_unreachable(client):
    client.mapi.side_effect = None
    client.mapi.return_value = mapi_result(ok=False, status_code=0, error="message-api unreachable: connect timeout")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "error" in resp.text


# ── workers ──────────────────────────────────────────────────────────────
def test_enable_worker_forwards_and_renders_enabled(client):
    client.mapi.return_value = mapi_result(data={"worker_id": "coder", "enabled": True})
    resp = client.post("/workers/coder/enable")
    assert resp.status_code == 200
    assert "enabled" in resp.text
    client.mapi.assert_awaited_once_with("POST", "/workers/coder/enable")


def test_disable_worker_error_renders_error_badge(client):
    client.mapi.return_value = mapi_result(ok=False, status_code=503, error="redis unavailable: timeout")
    resp = client.post("/workers/coder/disable")
    assert resp.status_code == 200
    assert "redis unavailable" in resp.text
    assert "error" in resp.text


# ── message composer ────────────────────────────────────────────────────
def test_send_message_forwards_parsed_json_payload(client):
    client.mapi.return_value = mapi_result(
        data={"id": "abc123", "from": "operator", "to": "coder", "type": "operator_message", "payload": {"task": "hi"}})
    resp = client.post("/messages", data={"to": "coder", "type": "operator_message", "payload": '{"task": "hi"}'})
    assert resp.status_code == 200
    assert "abc123" in resp.text
    args, kwargs = client.mapi.await_args
    assert args == ("POST", "/messages")
    assert kwargs["json"] == {"to": "coder", "type": "operator_message", "payload": {"task": "hi"}}


def test_send_message_invalid_json_never_calls_message_api(client):
    resp = client.post("/messages", data={"to": "coder", "type": "operator_message", "payload": "not json"})
    assert resp.status_code == 200
    assert "not a valid JSON object" in resp.text
    client.mapi.assert_not_awaited()


def test_send_message_non_object_payload_rejected(client):
    resp = client.post("/messages", data={"to": "coder", "type": "operator_message", "payload": "[1, 2, 3]"})
    assert resp.status_code == 200
    assert "not a valid JSON object" in resp.text
    client.mapi.assert_not_awaited()


def test_send_message_error_from_message_api_shown(client):
    client.mapi.return_value = mapi_result(ok=False, status_code=422, error="field required")
    resp = client.post("/messages", data={"to": "coder", "type": "operator_message", "payload": "{}"})
    assert "field required" in resp.text


# ── log filter ───────────────────────────────────────────────────────────
def test_log_filter_add_type_tracks_it_and_fetches_status(client):
    client.mapi.return_value = mapi_result(data={"type": "task_assignment", "excluded": False})
    resp = client.post("/log-filter/add", data={"message_type": "task_assignment"})
    assert resp.status_code == 200
    assert "task_assignment" in resp.text
    assert "task_assignment" in panel.KNOWN_LOG_TYPES


def test_log_filter_exclude_forwards_correct_path(client):
    client.mapi.return_value = mapi_result(data={"type": "status_update", "excluded": True})
    resp = client.post("/log-filter/status_update/exclude")
    assert resp.status_code == 200
    client.mapi.assert_awaited_once_with("POST", "/log-filter/status_update/exclude")


# ── log pruning ──────────────────────────────────────────────────────────
def test_prune_logs_shows_deleted_count(client):
    client.mapi.return_value = mapi_result(data={"deleted": 42})
    resp = client.post("/logs/prune", data={"after": "2026-07-01T00:00", "before": "2026-07-02T00:00"})
    assert "42" in resp.text
    args, kwargs = client.mapi.await_args
    assert kwargs["json"] == {"after": "2026-07-01T00:00", "before": "2026-07-02T00:00"}


def test_prune_logs_error_surfaced(client):
    client.mapi.return_value = mapi_result(ok=False, status_code=400, error="at least one of after/before is required")
    resp = client.post("/logs/prune", data={"after": "", "before": ""})
    assert "at least one of after/before is required" in resp.text


# ── replays ──────────────────────────────────────────────────────────────
def test_partial_replays_lists_episodes(client):
    client.mapi.return_value = mapi_result(data={"episodes": [{"name": "ep1"}, {"name": "ep2"}]})
    resp = client.get("/partials/replays")
    assert "ep1" in resp.text and "ep2" in resp.text


def test_delete_replay_success_returns_empty_body_to_remove_row(client):
    client.mapi.return_value = mapi_result(data={"name": "ep1", "deleted": True})
    resp = client.post("/replays/ep1/delete")
    assert resp.status_code == 200
    assert resp.text == ""


def test_delete_replay_error_keeps_row_with_error(client):
    client.mapi.return_value = mapi_result(ok=False, status_code=503, error="postgres unavailable")
    resp = client.post("/replays/ep1/delete")
    assert "postgres unavailable" in resp.text


def test_view_replay_renders_pretty_json(client):
    client.mapi.return_value = mapi_result(data={"source": "ep1", "events": [1, 2, 3]})
    resp = client.get("/replays/ep1/view")
    # Jinja HTML-escapes quotes inside <pre> (&#34;) — check content, not exact punctuation.
    assert "source" in resp.text and "ep1" in resp.text and "events" in resp.text


def test_view_replay_missing_shows_error(client):
    client.mapi.return_value = mapi_result(ok=False, status_code=404, error="no episode named 'ep1'")
    resp = client.get("/replays/ep1/view")
    assert "no episode named" in resp.text


def test_upload_replay_forwards_raw_body_and_params(client):
    client.mapi.return_value = mapi_result(data={"name": "ep1", "event_count": 5, "byte_size": 100, "created": True})
    resp = client.post(
        "/replays/upload",
        data={"name": "", "overwrite": "true"},
        files={"file": ("ep1.json", io.BytesIO(b'{"source": "ep1"}'), "application/json")},
    )
    assert resp.status_code == 200
    assert "uploaded" in resp.text and "ep1" in resp.text
    # First call is the upload itself; the handler follows it with a GET
    # /replays to refresh the table, so don't assume it's the last call.
    args, kwargs = client.mapi.await_args_list[0]
    assert args == ("POST", "/replays")
    assert kwargs["content"] == b'{"source": "ep1"}'
    assert kwargs["params"]["overwrite"] == "true"


# ── _mapi_request itself (run via asyncio.run — no pytest-asyncio in this
# repo's dependencies, and these are the only async tests, so a dedicated
# plugin isn't worth adding) ─────────────────────────────────────────────
def test_mapi_request_ok_parses_json(monkeypatch):
    fake_response = httpx.Response(200, json={"status": "ok"})
    fake_client = AsyncMock()
    fake_client.request = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(panel, "http_client", fake_client)
    result = asyncio.run(panel._mapi_request("GET", "/healthz"))
    assert result.ok and result.data == {"status": "ok"}


def test_mapi_request_http_error_extracts_detail(monkeypatch):
    fake_response = httpx.Response(503, json={"detail": "redis unavailable"})
    fake_client = AsyncMock()
    fake_client.request = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(panel, "http_client", fake_client)
    result = asyncio.run(panel._mapi_request("POST", "/workers/coder/enable"))
    assert not result.ok
    assert result.status_code == 503
    assert result.error == "redis unavailable"


def test_mapi_request_connection_error_fails_soft(monkeypatch):
    fake_client = AsyncMock()
    fake_client.request = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    monkeypatch.setattr(panel, "http_client", fake_client)
    result = asyncio.run(panel._mapi_request("GET", "/workers/coder"))
    assert not result.ok
    assert result.status_code == 0
    assert "unreachable" in result.error


# ── basic auth ───────────────────────────────────────────────────────────
def test_no_auth_required_when_env_unset(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_basic_auth_rejects_missing_credentials(client, monkeypatch):
    monkeypatch.setattr(panel, "BASIC_AUTH_USER", "op")
    monkeypatch.setattr(panel, "BASIC_AUTH_PASS", "secret")
    resp = client.get("/")
    assert resp.status_code == 401


def test_basic_auth_accepts_correct_credentials(client, monkeypatch):
    monkeypatch.setattr(panel, "BASIC_AUTH_USER", "op")
    monkeypatch.setattr(panel, "BASIC_AUTH_PASS", "secret")
    client.mapi.side_effect = None
    client.mapi.return_value = mapi_result(data={"episodes": []})
    resp = client.get("/", auth=("op", "secret"))
    assert resp.status_code == 200


def test_basic_auth_always_skips_healthz(client, monkeypatch):
    monkeypatch.setattr(panel, "BASIC_AUTH_USER", "op")
    monkeypatch.setattr(panel, "BASIC_AUTH_PASS", "secret")
    resp = client.get("/healthz")
    assert resp.status_code == 200
