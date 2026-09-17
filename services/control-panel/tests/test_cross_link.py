"""Phase 4: the dashboard's cross-link to the campaign manager is actually
rendered into the HTML (and driven by the env-derived constant, not a
hard-coded string). Minimal coverage — this isn't a re-test of the whole
panel, just of this one new variable + template line."""
import panel
from fastapi.testclient import TestClient


def test_dashboard_renders_the_campaign_manager_link(monkeypatch):
    async def _mapi_request(method, path, **kwargs):
        class _R:
            ok = True
            data = {"episodes": []} if path == "/replays" else {}
            error = None
        return _R()

    async def _worker_status(worker_id):
        return {"id": worker_id, "enabled": True, "error": None}

    async def _log_filter_status(message_type):
        return {"enabled": True, "error": None}

    monkeypatch.setattr(panel, "_mapi_request", _mapi_request)
    monkeypatch.setattr(panel, "_worker_status", _worker_status)
    monkeypatch.setattr(panel, "_log_filter_status", _log_filter_status)

    client = TestClient(panel.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert f'href="{panel.CAMPAIGN_MANAGER_URL}"' in resp.text
    assert "Campaign manager" in resp.text

    # Prove the value flows through from the constant (not a coincidence of
    # the default): change the constant, the rendered href must follow.
    monkeypatch.setattr(panel, "CAMPAIGN_MANAGER_URL", "http://example.test:9999")
    resp2 = client.get("/")
    assert 'href="http://example.test:9999"' in resp2.text
