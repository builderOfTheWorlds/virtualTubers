"""Phase 4: campaign-manager's dashboard renders its outgoing control-panel
link, driven by the same env-derived constant the publish banner already
uses (one constant, two surfaces). The FakeGen pattern from
test_pack_viewer.py — every generator-API call returns a valid empty
payload, so the dashboard renders its error-free path and the navbar is
the thing under test.
"""
import httpx
import pytest
from unittest.mock import AsyncMock, patch

import main
from fastapi.testclient import TestClient


def _ok(body):
    return httpx.Response(200, json=body)


class FakeGen:
    def __init__(self):
        self.get = AsyncMock(return_value=_ok([]))
        self.post = AsyncMock(return_value=_ok({}))
        self.put = AsyncMock(return_value=_ok({}))
        self.aclose = AsyncMock()


@pytest.fixture
def fclient():
    return FakeGen()


@pytest.fixture
def client(fclient):
    with patch.object(main, "http_client", fclient):
        yield TestClient(main.app)


def test_dashboard_renders_the_control_panel_link(client, monkeypatch):
    resp = client.get("/")
    assert resp.status_code == 200
    assert f'href="{main.CONTROL_PANEL_URL}" class="btn btn-outline-light btn-sm" target="_blank"' in resp.text
    # The outgoing link is the constant, not something hard-coded in the
    # template — prove it moves when the constant does.
    monkeypatch.setattr(main, "CONTROL_PANEL_URL", "http://example.test:9998")
    resp2 = client.get("/")
    assert 'href="http://example.test:9998"' in resp2.text
