"""The Rerun Theater replay list has three actions per row: view, play,
delete. `play` posts a `replay_request` bus message via message-api's
generic /messages proxy, addressed to the worker chosen in that row's
`to` select. Covers both the request shape sent upstream and the
success/failure banner rendering — no LLM/bus/DB involved, /messages is
mocked at the panel's own _mapi_request seam.
"""
import panel
from fastapi.testclient import TestClient


def _client(monkeypatch, mapi_request):
    async def _worker_status(worker_id):
        return {"id": worker_id, "enabled": True, "error": None}

    async def _log_filter_status(message_type):
        return {"enabled": True, "error": None}

    monkeypatch.setattr(panel, "_mapi_request", mapi_request)
    monkeypatch.setattr(panel, "_worker_status", _worker_status)
    monkeypatch.setattr(panel, "_log_filter_status", _log_filter_status)
    return TestClient(panel.app)


def test_replay_row_has_view_play_and_delete_buttons(monkeypatch):
    async def _mapi_request(method, path, **kwargs):
        class _R:
            ok = True
            data = {"episodes": [{"name": "ashiorid_smoke", "event_count": 10}]} if path == "/replays" else {}
            error = None
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.get("/")
    assert resp.status_code == 200
    assert ">View<" in resp.text
    assert ">Play<" in resp.text
    assert ">Delete<" in resp.text
    assert 'hx-post="/replays/ashiorid_smoke/play"' in resp.text


def test_play_sends_replay_request_to_the_chosen_worker(monkeypatch):
    calls = []

    async def _mapi_request(method, path, **kwargs):
        calls.append((method, path, kwargs))

        class _R:
            ok = True
            error = None
            data = {"episodes": []} if path == "/replays" else {"to": kwargs.get("json", {}).get("to")}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.post("/replays/ashiorid_smoke/play", data={"to": "coder"})
    assert resp.status_code == 200

    play_calls = [c for c in calls if c[1] == "/messages"]
    assert len(play_calls) == 1
    method, path, kwargs = play_calls[0]
    assert method == "POST"
    assert kwargs["json"] == {
        "to": "coder",
        "type": "replay_request",
        "payload": {"episode": "ashiorid_smoke"},
    }
    assert "queued" in resp.text
    assert "ashiorid_smoke" in resp.text
    assert "coder" in resp.text


def test_play_defaults_to_broadcast_when_no_worker_chosen(monkeypatch):
    calls = []

    async def _mapi_request(method, path, **kwargs):
        calls.append((method, path, kwargs))

        class _R:
            ok = True
            error = None
            data = {"episodes": []} if path == "/replays" else {"to": kwargs.get("json", {}).get("to")}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.post("/replays/ashiorid_smoke/play")
    assert resp.status_code == 200
    play_calls = [c for c in calls if c[1] == "/messages"]
    assert play_calls[0][2]["json"]["to"] == "broadcast"
    # Solo/broadcast targets get the bare payload — no cast injected.
    assert "cast" not in play_calls[0][2]["json"]["payload"]


def test_play_to_roundtable_auto_attaches_the_worker_to_slot_cast(monkeypatch):
    """The one case that isn't a bare replay_request: tuber_0's tile grid
    only lights up via the duet director path, which requires payload.cast
    (see panel.WORKER_TO_TUBER_SLOT's docstring). Every other `to` value
    must NOT get a cast — it would turn an ordinary solo airing into an
    (empty, ready-satisfied-trivially, still-solo-looking) duet directed
    at itself, which is pointless indirection at best.
    """
    calls = []

    async def _mapi_request(method, path, **kwargs):
        calls.append((method, path, kwargs))

        class _R:
            ok = True
            error = None
            data = {"episodes": []} if path == "/replays" else {"to": kwargs.get("json", {}).get("to")}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.post("/replays/ashiorid_smoke/play", data={"to": "tuber_0"})
    assert resp.status_code == 200

    play_calls = [c for c in calls if c[1] == "/messages"]
    assert len(play_calls) == 1
    sent = play_calls[0][2]["json"]
    assert sent["to"] == "tuber_0"
    assert sent["payload"]["episode"] == "ashiorid_smoke"
    assert sent["payload"]["cast"] == panel.WORKER_TO_TUBER_SLOT
    assert sent["payload"]["cast"]["coder"] == "tuber_1"
    assert sent["payload"]["cast"]["manager"] == "tuber_6"


def test_play_failure_renders_error_banner_not_a_500(monkeypatch):
    async def _mapi_request(method, path, **kwargs):
        class _R:
            ok = False
            error = "worker unreachable"
            data = {"episodes": []} if path == "/replays" else {}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.post("/replays/ashiorid_smoke/play", data={"to": "coder"})
    assert resp.status_code == 200
    assert "worker unreachable" in resp.text
    assert "ashiorid_smoke" in resp.text
