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


@pytest.fixture(autouse=True)
def _reset_theme_cache(monkeypatch):
    """_theme_names caches GET /console-themes at module scope; without a
    reset, whether the dashboard calls it depends on which test ran first."""
    monkeypatch.setattr(panel, "_THEME_NAMES_CACHE", None)


@pytest.fixture
def client(monkeypatch):
    mock = AsyncMock(return_value=mapi_result())
    monkeypatch.setattr(panel, "_mapi_request", mock)
    with TestClient(panel.app) as c:
        c.mapi = mock  # stash for assertions
        yield c


# ── dashboard ────────────────────────────────────────────────────────────
def test_dashboard_renders_worker_and_replay_data(client):
    # The dashboard also renders the Console theme section (added after this
    # test was written), so the fake must answer /console-themes and
    # /console-theme/{id} too — plus the workers' /workers/health batch.
    async def side_effect(method, path, **kwargs):
        if path == "/workers/health":
            return mapi_result(data={"workers": []})
        if path == "/console-themes":
            return mapi_result(data={"themes": ["Dracula"]})
        if path.startswith("/console-theme/"):
            return mapi_result(data={"theme": None, "overridden": False})
        if path.startswith("/workers/"):
            return mapi_result(data={"worker_id": path.split("/")[-1], "enabled": True})
        if path.startswith("/log-filter/"):
            return mapi_result(data={"type": "status_update", "excluded": True})
        if path == "/replays":
            return mapi_result(data={"episodes": [{"name": "ep1", "project": "demo", "event_count": 3}]})
        if path == "/console-themes":
            return mapi_result(data={"themes": ["Dracula"]})
        if path.startswith("/console-theme/"):
            return mapi_result(data={"theme": None, "overridden": False})
        if path == "/music-moods":
            return mapi_result(data={"moods": ["neutral", "tension", "silence"]})
        if path.startswith("/music/"):
            return mapi_result(data={"override": None, "overridden": False, "running": False, "status": None})
        if path == "/logs/containers":
            return mapi_result(data={"logs": []})  # no current airing
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
    # The toggle, then a health re-read so the swapped row keeps its badges.
    calls = [c.args for c in client.mapi.await_args_list]
    assert calls == [("POST", "/workers/coder/enable"), ("GET", "/workers/coder/health")]


def test_disable_worker_error_renders_error_badge(client):
    client.mapi.return_value = mapi_result(ok=False, status_code=503, error="redis unavailable: timeout")
    resp = client.post("/workers/coder/disable")
    assert resp.status_code == 200
    assert "redis unavailable" in resp.text
    assert "error" in resp.text


# ── worker health (GET /workers/health) ─────────────────────────────────
def _health_row(worker_id, state, **fields):
    row = {"worker_id": worker_id, "state": state,
           "alive": None if state == "unknown" else state in ("alive", "stale"),
           "last_seen": None, "age_s": None, "local_override": None,
           "enabled": True, "ttl_s": 15}
    row.update(fields)
    return row


def _workers_partial(client, health_rows, health_ok=True):
    async def side_effect(method, path, **kwargs):
        if path == "/workers/health":
            if not health_ok:
                return mapi_result(ok=False, status_code=0, error="message-api unreachable")
            return mapi_result(data={"workers": health_rows})
        return mapi_result(data={"worker_id": path.split("/")[-1], "enabled": True})

    client.mapi.side_effect = side_effect
    resp = client.get("/partials/workers")
    assert resp.status_code == 200
    return resp.text


def _row_html(text, worker_id):
    start = text.index(f'id="worker-row-{worker_id}"')
    return text[start:text.index("</tr>", start)]


def test_workers_partial_renders_alive_with_last_seen_age(client):
    text = _workers_partial(client, [_health_row("coder", "alive", age_s=4.2, last_seen="2026-09-27T12:00:00+00:00")])
    row = _row_html(text, "coder")
    assert ">alive<" in row
    assert "last seen 4s ago" in row
    assert "kill-switch" not in row


def test_workers_partial_renders_stale_and_down(client):
    text = _workers_partial(client, [
        _health_row("coder", "stale", age_s=130),
        _health_row("tester", "down"),
    ])
    assert ">stale<" in _row_html(text, "coder")
    assert "last seen 2m 10s ago" in _row_html(text, "coder")
    assert ">down<" in _row_html(text, "tester")


def test_workers_partial_renders_local_kill_switch_badge(client):
    text = _workers_partial(client, [_health_row("coder", "alive", age_s=1, local_override=True)])
    row = _row_html(text, "coder")
    assert "local kill-switch engaged" in row
    assert "scripts/emergency_resume.sh coder" in row
    assert "local kill-switch" not in _row_html(text, "tester")


def test_workers_partial_health_unknown_when_message_api_fails(client):
    text = _workers_partial(client, [], health_ok=False)
    for worker_id in panel.WORKER_IDS:
        assert ">unknown<" in _row_html(text, worker_id)


def test_toggle_row_keeps_kill_switch_badge(client):
    async def side_effect(method, path, **kwargs):
        if path == "/workers/coder/health":
            return mapi_result(data=_health_row("coder", "alive", age_s=2, local_override=True))
        return mapi_result(data={"worker_id": "coder", "enabled": True})

    client.mapi.side_effect = side_effect
    resp = client.post("/workers/coder/enable")
    assert "local kill-switch engaged" in resp.text


@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"), (12.7, "12s"), (130, "2m 10s"), (7500, "2h 5m"), (None, "?"), ("x", "?"),
])
def test_format_age(seconds, expected):
    assert panel.format_age(seconds) == expected


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


def _airing_side_effect(queued_rows):
    async def side_effect(method, path, **kwargs):
        if path == "/logs/containers":
            return mapi_result(data={"logs": queued_rows})
        return mapi_result(data={"episodes": []})
    return side_effect


def test_partial_replays_reattaches_progress_to_current_airing(client):
    # A refresh / second browser has no play_result — the bar must still
    # come back, pointed at the roundtable's newest queued airing.
    client.mapi.side_effect = _airing_side_effect([{
        "container_name": "virtualtubers-worker-roundtable-1", "stream": "stdout",
        "message": "[agent:roundtable] queued replay episode 'cyber_police_day1'",
        "log_timestamp": "2026-10-01T14:40:40.923579+00:00"}])
    resp = client.get("/partials/replays")
    assert 'id="replay-progress"' in resp.text
    assert "/replays/cyber_police_day1/progress?since=2026-10-01T14%3A40%3A39.923579%2B00%3A00" in resp.text
    lookup = [c for c in client.mapi.call_args_list if c.args[1] == "/logs/containers"][0]
    assert ("contains", "queued replay episode") in lookup.kwargs["params"]
    assert ("service", panel.ROUNDTABLE_SERVICE) in lookup.kwargs["params"]


@pytest.mark.parametrize("rows", [[], [{"message": "garbage", "log_timestamp": "2026-10-01T00:00:00+00:00"}]])
def test_partial_replays_no_airing_no_progress_bar(client, rows):
    client.mapi.side_effect = _airing_side_effect(rows)
    resp = client.get("/partials/replays")
    assert 'id="replay-progress"' not in resp.text


def test_partial_replays_airing_lookup_failure_still_renders(client):
    async def side_effect(method, path, **kwargs):
        if path == "/logs/containers":
            return mapi_result(ok=False, status_code=503, error="postgres unavailable")
        return mapi_result(data={"episodes": [{"name": "ep1"}]})
    client.mapi.side_effect = side_effect
    resp = client.get("/partials/replays")
    assert resp.status_code == 200 and "ep1" in resp.text
    assert 'id="replay-progress"' not in resp.text

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
    # The panel upload never sends ?status= — operator uploads stay approved.
    assert "status" not in kwargs["params"]


# ── draft review ─────────────────────────────────────────────────────────
def _library_and_drafts(library, drafts, extra=None):
    """side_effect answering the two GET /replays listings separately."""
    async def side_effect(method, path, **kwargs):
        if extra is not None:
            hit = extra(method, path, **kwargs)
            if hit is not None:
                return hit
        if method == "GET" and path == "/recordings":
            return mapi_result(data={"recordings": [], "used_bytes": 0,
                                     "limit_bytes": 5_000_000_000, "remaining_bytes": 5_000_000_000})
        if method == "GET" and path == "/replays":
            params = kwargs.get("params") or {}
            if params.get("status") == "draft":
                return mapi_result(data={"episodes": drafts})
            return mapi_result(data={"episodes": library})
        if method == "GET" and path == "/logs/containers":
            return mapi_result(data={"logs": []})  # no current airing
        raise AssertionError(f"unexpected call {method} {path} {kwargs}")
    return side_effect


def test_partial_replays_renders_drafts_separately_with_approve_and_no_play(client):
    client.mapi.side_effect = _library_and_drafts(
        [{"name": "aired-ep"}], [{"name": "draft-ep", "uploaded_by": "3layer-generator"}])

    resp = client.get("/partials/replays")

    assert resp.status_code == 200
    assert 'id="draft-row-draft-ep"' in resp.text
    assert 'hx-post="/replays/draft-ep/approve"' in resp.text
    assert 'hx-post="/replays/draft-ep/reject"' in resp.text
    # A draft must never get a Play button.
    assert "/replays/draft-ep/play" not in resp.text
    assert "/replays/aired-ep/play" in resp.text
    assert "3layer-generator" in resp.text


def test_partial_replays_no_drafts_shows_empty_hint(client):
    client.mapi.side_effect = _library_and_drafts([{"name": "aired-ep"}], [])
    resp = client.get("/partials/replays")
    assert "no drafts awaiting review" in resp.text


def test_partial_replays_drafts_error_shown_library_still_renders(client):
    def extra(method, path, **kwargs):
        params = kwargs.get("params")
        if isinstance(params, dict) and params.get("status") == "draft":
            return mapi_result(ok=False, status_code=503, error="drafts listing broke")
        return None
    client.mapi.side_effect = _library_and_drafts([{"name": "aired-ep"}], [], extra)

    resp = client.get("/partials/replays")

    assert "drafts listing broke" in resp.text
    assert "aired-ep" in resp.text


def test_approve_replay_forwards_and_rerenders_section(client):
    calls = []

    def extra(method, path, **kwargs):
        calls.append((method, path))
        if method == "POST":
            return mapi_result(data={"name": "draft-ep", "status": "approved",
                                     "previous_status": "draft"})
        return None
    client.mapi.side_effect = _library_and_drafts([{"name": "draft-ep"}], [], extra)

    resp = client.post("/replays/draft-ep/approve")

    assert resp.status_code == 200
    assert calls[0] == ("POST", "/replays/draft-ep/approve")
    assert "approved" in resp.text
    assert 'id="replays-section"' in resp.text
    # Now in the library table, where Play lives.
    assert "/replays/draft-ep/play" in resp.text


def test_approve_replay_error_shows_banner(client):
    def extra(method, path, **kwargs):
        if method == "POST":
            return mapi_result(ok=False, status_code=404, error="no episode named 'gone'")
        return None
    client.mapi.side_effect = _library_and_drafts([], [], extra)

    resp = client.post("/replays/gone/approve")

    assert resp.status_code == 200
    assert "approve gone failed" in resp.text
    assert "no episode named" in resp.text


def test_reject_replay_success_deletes_and_returns_empty_body(client):
    client.mapi.return_value = mapi_result(data={"name": "draft-ep", "deleted": True})

    resp = client.post("/replays/draft-ep/reject")

    assert resp.status_code == 200
    assert resp.text == ""
    args, _ = client.mapi.await_args_list[0]
    assert args == ("DELETE", "/replays/draft-ep")


def test_reject_replay_error_keeps_draft_row_without_play(client):
    client.mapi.return_value = mapi_result(ok=False, status_code=503, error="postgres unavailable")

    resp = client.post("/replays/draft-ep/reject")

    assert "postgres unavailable" in resp.text
    assert 'id="draft-row-draft-ep"' in resp.text
    assert "/play" not in resp.text


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


# ── GM live music ────────────────────────────────────────────────────────
@pytest.fixture
def _reset_music_moods(monkeypatch):
    monkeypatch.setattr(panel, "_MUSIC_MOODS_CACHE", None)


PLAYING_STATUS = {"theme": "ashiorid", "mood": "tension", "intensity": 0.7, "source": "gm_override",
                  "scene_id": "", "playing_mood": "tension", "tempo_bpm": 132.0, "mode": "phrygian",
                  "bar": 42, "recording_session": None, "at": 0}


def _music_side_effect(calls, state, write_result=None):
    async def side_effect(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/music-moods":
            return mapi_result(data={"moods": ["neutral", "tension", "silence"]})
        if method == "GET" and path == "/music/roundtable":
            return mapi_result(data=state)
        if method in ("POST", "DELETE") and path == "/music/roundtable":
            return write_result or mapi_result(data={})
        raise AssertionError(f"unexpected call {method} {path}")
    return side_effect


def test_music_partial_renders_now_playing_and_override(client, _reset_music_moods):
    state = {"override": {"mood": "tension", "intensity": 0.7}, "overridden": True,
             "running": True, "status": PLAYING_STATUS}
    client.mapi.side_effect = _music_side_effect([], state)
    resp = client.get("/partials/music")
    assert resp.status_code == 200
    assert "GM override" in resp.text
    assert "132.0 bpm" in resp.text
    assert "phrygian" in resp.text
    assert "Follow scene" in resp.text


def test_music_partial_not_running_following_scene(client, _reset_music_moods):
    state = {"override": None, "overridden": False, "running": False, "status": None}
    client.mapi.side_effect = _music_side_effect([], state)
    resp = client.get("/partials/music")
    assert "not running" in resp.text
    assert "following scene moods" in resp.text
    assert "Follow scene" not in resp.text


def test_music_polling_targets_status_only_not_the_form(client, _reset_music_moods):
    # Regression: the whole card used to poll with outerHTML every 5s, which
    # reset the mood dropdown back to the current mood before "Hold mood".
    state = {"override": {"mood": "neutral", "intensity": 1.0}, "overridden": True,
             "running": True, "status": PLAYING_STATUS}
    client.mapi.side_effect = _music_side_effect([], state)
    card = client.get("/partials/music").text
    card_open = card[card.index('<div id="music-card"'):].split(">", 1)[0]
    assert "hx-trigger" not in card_open
    assert 'hx-get="/partials/music-status"' in card
    status = client.get("/partials/music-status")
    assert status.status_code == 200
    assert 'id="music-status"' in status.text
    assert "<form" not in status.text and "<select" not in status.text
    assert "Follow scene" in status.text


def test_music_set_forwards_mood_and_intensity(client, _reset_music_moods):
    calls = []
    state = {"override": {"mood": "silence", "intensity": 0.3}, "overridden": True,
             "running": True, "status": PLAYING_STATUS}
    client.mapi.side_effect = _music_side_effect(calls, state)
    resp = client.post("/music/set", data={"mood": "silence", "intensity": "0.3"})
    assert resp.status_code == 200
    post = [c for c in calls if c[0] == "POST"]
    assert post == [("POST", "/music/roundtable", {"json": {"mood": "silence", "intensity": 0.3}})]
    assert "silence @ 0.30" in resp.text


def test_music_set_error_is_shown(client, _reset_music_moods):
    state = {"override": None, "overridden": False, "running": True, "status": PLAYING_STATUS}
    client.mapi.side_effect = _music_side_effect(
        [], state, mapi_result(ok=False, status_code=503, error="redis unavailable: down"))
    resp = client.post("/music/set", data={"mood": "tension", "intensity": "0.5"})
    assert resp.status_code == 200
    assert "redis unavailable: down" in resp.text


def test_music_clear_forwards_delete(client, _reset_music_moods):
    calls = []
    state = {"override": None, "overridden": False, "running": True, "status": PLAYING_STATUS}
    client.mapi.side_effect = _music_side_effect(calls, state)
    resp = client.post("/music/clear")
    assert resp.status_code == 200
    assert ("DELETE", "/music/roundtable", {}) in calls
    assert "following scene moods" in resp.text


def test_music_moods_fall_back_when_message_api_down(client, _reset_music_moods):
    client.mapi.side_effect = None
    client.mapi.return_value = mapi_result(ok=False, status_code=0, error="message-api unreachable")
    resp = client.get("/partials/music")
    assert resp.status_code == 200
    assert "joyful_activation" in resp.text  # from MUSIC_MOODS_FALLBACK
    assert "message-api unreachable" in resp.text
