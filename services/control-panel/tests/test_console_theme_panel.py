"""
Console theme controls (2026-09-24 feature): the dashboard's Theme section,
and the /console-theme/{worker_id} set/clear routes that wrap message-api's
GET /console-themes and GET/POST/DELETE /console-theme/{worker_id}.

No LLM/bus/DB/Redis involved — message-api is mocked at the panel's own
_mapi_request seam, same pattern as test_replay_play.py.
"""
import panel
from fastapi.testclient import TestClient


def _client(monkeypatch, mapi_request, theme_names=None):
    async def _worker_status(worker_id):
        return {"id": worker_id, "enabled": True, "error": None}

    async def _log_filter_status(message_type):
        return {"enabled": True, "error": None}

    monkeypatch.setattr(panel, "_mapi_request", mapi_request)
    monkeypatch.setattr(panel, "_worker_status", _worker_status)
    monkeypatch.setattr(panel, "_log_filter_status", _log_filter_status)
    # _theme_names caches at module scope across tests; reset it so each
    # test's mocked list actually takes effect rather than a prior test's.
    monkeypatch.setattr(panel, "_THEME_NAMES_CACHE", theme_names)
    return TestClient(panel.app)


def _mapi_ok(replays=None, themes=None, theme_status=None):
    """A working message-api stub for GET /replays, GET /console-themes,
    and GET /console-theme/{worker_id} — enough for a dashboard render."""
    theme_status = theme_status or {}

    async def _mapi_request(method, path, **kwargs):
        class _R:
            ok = True
            error = None
            data = None
        r = _R()
        if path == "/replays":
            r.data = {"episodes": replays or []}
        elif path == "/console-themes":
            r.data = {"themes": themes or []}
        elif path.startswith("/console-theme/"):
            worker_id = path.rsplit("/", 1)[-1]
            r.data = theme_status.get(worker_id, {"theme": None, "overridden": False})
        else:
            r.data = {}
        return r
    return _mapi_request


def test_dashboard_renders_the_theme_section_and_worker_rows(monkeypatch):
    client = _client(monkeypatch, _mapi_ok(themes=["Dracula", "Builtin Solarized Dark"]))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Console theme" in resp.text
    for worker_id in panel.THEME_WORKER_IDS:
        assert f'theme-worker-row-{worker_id}' in resp.text


def test_theme_worker_ids_include_gm_and_roundtable_unlike_worker_ids():
    """Regression guard: WORKER_IDS (message composer target list)
    deliberately excludes tuber_0/roundtable, but both run
    theme_watcher.py and must still be retheme-able from this page."""
    assert "tuber_0" in panel.THEME_WORKER_IDS
    assert panel.ROUNDTABLE_WORKER_ID in panel.THEME_WORKER_IDS
    assert "tuber_0" not in panel.WORKER_IDS


def test_dashboard_reports_error_when_no_themes_available(monkeypatch):
    client = _client(monkeypatch, _mapi_ok(themes=[]))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "message-api unreachable or returned no themes" in resp.text


def test_set_theme_posts_to_message_api_and_renders_the_new_value(monkeypatch):
    calls = []

    async def _mapi_request(method, path, **kwargs):
        calls.append((method, path, kwargs))

        class _R:
            ok = True
            error = None
            data = None
        r = _R()
        if path == "/console-themes":
            r.data = {"themes": ["Dracula"]}
        elif path == "/console-theme/coder":
            r.data = {"worker_id": "coder", "theme": "Dracula"}
        return r

    client = _client(monkeypatch, _mapi_request, theme_names=["Dracula"])
    resp = client.post("/console-theme/coder", data={"theme": "Dracula"})
    assert resp.status_code == 200
    assert "Dracula" in resp.text
    assert '<span class="badge on"' in resp.text  # marked "live" (overridden)

    set_calls = [c for c in calls if c[1] == "/console-theme/coder" and c[0] == "POST"]
    assert len(set_calls) == 1
    assert set_calls[0][2]["json"] == {"theme": "Dracula"}


def test_set_theme_reports_message_api_error(monkeypatch):
    async def _mapi_request(method, path, **kwargs):
        class _R:
            ok = True
            error = None
            data = {"themes": []}
        r = _R()
        if path == "/console-theme/coder" and method == "POST":
            r = _R()
            r.ok = False
            r.error = "unknown theme 'Nope'"
            r.data = None
        return r

    client = _client(monkeypatch, _mapi_request, theme_names=[])
    resp = client.post("/console-theme/coder", data={"theme": "Nope"})
    assert resp.status_code == 200
    assert "unknown theme" in resp.text
    assert '<span class="badge err"' in resp.text


def test_clear_theme_deletes_and_re_resolves_the_fallback(monkeypatch):
    calls = []

    async def _mapi_request(method, path, **kwargs):
        calls.append((method, path, kwargs))

        class _R:
            ok = True
            error = None
            data = None
        r = _R()
        if path == "/console-themes":
            r.data = {"themes": []}
        elif path == "/console-theme/coder" and method == "DELETE":
            r.data = {"worker_id": "coder", "theme": None, "overridden": False}
        elif path == "/console-theme/coder" and method == "GET":
            # Re-resolved fallback: the worker's config file default.
            r.data = {"worker_id": "coder", "theme": "Builtin Solarized Dark", "overridden": False}
        return r

    client = _client(monkeypatch, _mapi_request, theme_names=[])
    resp = client.post("/console-theme/coder/clear")
    assert resp.status_code == 200
    assert "Builtin Solarized Dark" in resp.text
    assert '<span class="badge on"' not in resp.text  # no longer marked "live"

    delete_calls = [c for c in calls if c[1] == "/console-theme/coder" and c[0] == "DELETE"]
    assert len(delete_calls) == 1


def test_partial_theme_workers_endpoint_renders_the_table(monkeypatch):
    client = _client(monkeypatch, _mapi_ok(themes=["Dracula"]))
    resp = client.get("/partials/theme-workers")
    assert resp.status_code == 200
    assert "theme-workers-table" in resp.text
