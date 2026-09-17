"""Campaign-manager Pack Viewer / Editor routes (Phase 1.4 + 2.2).

These routes are pure proxies over the generator API's /packs/* surface —
no DB, no filesystem. So we test them by monkeypatching main.http_client
with a fake async client that returns pre-built httpx.Response objects in
exactly the shapes the real generator API produces (verified by reading
generator_api.py's handlers, not by spinning up a server).

The point of these tests is not to re-test the generator API (that's
test_api.py over there) — it's to prove:
  1. each GET route renders the right template with the right variables;
  2. each POST/PUT save-route calls the right generator PUT endpoint with
     the right body shape;
  3. a 4xx from the generator surfaces as `save_error` (re-rendered with
     the operator's text still there, not a hard error page) so the plan's
     "the GUI in Task 2.2 keeps the textarea populated with what they typed"
     is actually true;
  4. the error path when the generator API is unreachable renders the error
     template (pack_name=None branch) rather than a 500.
"""
import httpx
import pytest
from unittest.mock import AsyncMock, patch

import main


def _ok(status=200, body=None, text=None):
    """A minimal httpx.Response — enough for main.py's .is_success / .json() /
    .text / .status_code surface (it never follows redirects, streams, or
    reads a body a second time)."""
    if body is not None:
        return httpx.Response(status, json=body)
    return httpx.Response(status, text=text if text is not None else "")


class FakeGenClient:
    """Stands in for main.http_client. Each method can be overridden per-test
    via its own AsyncMock so the exact request path + body the route sent is
    asserted, not just the outcome."""

    def __init__(self):
        self.get = AsyncMock(return_value=_ok(200, body=[]))
        self.put = AsyncMock(return_value=_ok(200, body={"status": "saved"}))
        self.post = AsyncMock(return_value=_ok(200, body={}))
        self.aclose = AsyncMock()


@pytest.fixture
def fclient():
    return FakeGenClient()


@pytest.fixture(autouse=True)
def client(fclient):
    with patch.object(main, "http_client", fclient):
        yield __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(main.app)


# ---------------------------------------------------------------------------
# GET routes — render the right template with the right variables.
# ---------------------------------------------------------------------------

def test_packs_list_renders_names(client, fclient):
    fclient.get.return_value = _ok(200, body=["ashiorid_1", "test_pack"])
    resp = client.get("/packs")
    assert resp.status_code == 200
    assert "ashiorid_1" in resp.text
    assert "test_pack" in resp.text
    # It must have asked for a plain list (the plan's decision: /packs stays
    # a plain list) — the fake returns it verbatim; this call shape is
    # asserted below via AsyncMock args.
    fclient.get.assert_awaited_once_with("/packs")


def test_packs_list_surfaces_a_generator_api_error(client, fclient):
    # _fetch_packs (main.py's own helper, not /packs/{name}) formats a
    # non-2xx as "API error: {status_code}" — that is the operator-facing
    # message, so assert on it rather than on the fake's raw body.
    fclient.get.return_value = _ok(500, text="boom")
    resp = client.get("/packs")
    assert resp.status_code == 200      # the route rendered, not a crash
    assert "API error: 500" in resp.text


def test_pack_viewer_renders_all_sections(client, fclient):
    fclient.get.return_value = _ok(200, body={
        "name": "alpha", "title": "A Test Pack", "genre": "fantasy",
        "start_scene": "intro", "gm_id": "gm", "player_ids": ["chadwick"],
        "scenes": [{"id": "intro", "title": "The Intro", "ambient": False,
                    "default_next": "intro"}],
        "cast": [{"id": "gm", "name": "GM Alpha", "role": "gm",
                  "archetype": "narrator", "worker_id": "manager"}],
        "lore_names": ["the-incident"],
    })
    resp = client.get("/packs/alpha")
    assert resp.status_code == 200
    for expected in ["The Intro", "GM Alpha", "the-incident", "The Intro".join([]) or "manager",
                     "start", "ambient"]:
        # 'ambient' and 'start' are the badge words on the scene table
        pass
    # the core content assertions
    assert "The Intro" in resp.text
    assert "GM Alpha" in resp.text
    assert "the-incident" in resp.text
    assert "manager" in resp.text          # worker_id surfaced on the cast row
    assert "start" in resp.text            # scene marked as start scene
    fclient.get.assert_awaited_once_with("/packs/alpha")


def test_pack_viewer_404_from_generator_renders_error_path(client, fclient):
    fclient.get.return_value = _ok(404, text="pack 'nope' not found")
    resp = client.get("/packs/nope")
    assert resp.status_code == 200            # route rendered, no server crash
    assert "not found" in resp.text


def test_pack_viewer_unreachable_renders_error_path(client, fclient):
    fclient.get.side_effect = httpx.RequestError("connection refused")
    resp = client.get("/packs/alpha")
    assert resp.status_code == 200
    assert "unreachable" in resp.text.lower() or "refused" in resp.text


# ---------------------------------------------------------------------------
# Scene editor — the load-bearing "keep the operator's text on a 422" case.
# ---------------------------------------------------------------------------

def test_save_pack_scene_success(client, fclient):
    fclient.put.return_value = _ok(200, body={"status": "saved"})
    resp = client.post("/packs/alpha/scenes/intro",
                       data={"scene_yaml": "id: intro\ntitle: New\n"})
    assert resp.status_code == 200
    assert "Saved." in resp.text
    assert "id: intro" in resp.text          # the operator's text is re-shown
    fclient.put.assert_awaited_once_with(
        "/packs/alpha/scenes/intro",
        json={"scene_yaml": "id: intro\ntitle: New\n"},
    )


def test_save_pack_scene_422_keeps_text_and_shows_reason(client, fclient):
    """The plan's Task 2.2 guarantee: an edit rejected with 422 re-renders
    the form WITH the operator's typed text (not lost to a bare error page)
    and with the generator API's own `detail` as the visible reason."""
    fclient.put.return_value = _ok(422, body={
        "detail": "this edit would make pack 'alpha' invalid, nothing was saved: "
                  "beat in intro missing 'type'"
    })
    my_yaml = "id: intro\ntitle: Broken\nbeats:\n  - {speaker: gm}\n"
    resp = client.post("/packs/alpha/scenes/intro", data={"scene_yaml": my_yaml})
    assert resp.status_code == 200            # rendered, not a server crash
    assert "Not saved." in resp.text
    # load_pack's own message, rendered with HTML-escaped quotes (&#39;), so
    # assert on the quote-free portion rather than the literal apostrophes.
    assert "beat in intro missing" in resp.text
    assert my_yaml in resp.text               # the operator's text is preserved


def test_save_pack_scene_unreachable_uses_error_path(client, fclient):
    fclient.put.side_effect = httpx.RequestError("no route to host")
    resp = client.post("/packs/alpha/scenes/intro", data={"scene_yaml": "id: intro\n"})
    assert resp.status_code == 200
    assert "unreachable" in resp.text


# ---------------------------------------------------------------------------
# Cast editor — the worker_id field is a first-class part of the form.
# ---------------------------------------------------------------------------

def test_save_pack_cast_member_sends_worker_id(client, fclient):
    fclient.put.return_value = _ok(200, body={"status": "saved"})
    resp = client.post("/packs/alpha/cast/gm",
                       data={"member_yaml": "name: GM Alpha\n",
                             "worker_id": "manager"})
    assert resp.status_code == 200
    fclient.put.assert_awaited_once_with(
        "/packs/alpha/cast/gm",
        json={"member_yaml": "name: GM Alpha\n", "worker_id": "manager"},
    )
    assert "manager" in resp.text


def test_save_pack_cast_member_blank_worker_id_becomes_null(client, fclient):
    fclient.put.return_value = _ok(200, body={"status": "saved"})
    client.post("/packs/alpha/cast/gm",
                data={"member_yaml": "name: GM Alpha\n", "worker_id": ""})
    # Blank form field -> null body, which generation_store treats as
    # "keep the existing mapping" via its COALESCE upsert.
    assert fclient.put.call_args.kwargs["json"]["worker_id"] is None
