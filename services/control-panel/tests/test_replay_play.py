"""The Rerun Theater replay list has three actions per row: view, play,
delete. `play` now airs on EVERY stream in one click — the six character
channels AND the roundtable — since a bare `to: "broadcast"` request left
the roundtable playing audio-only with no tile text/status update (root
cause: the roundtable's tile grid only updates via the duet director path,
which requires payload.cast — see panel.py's play_replay docstring).

This is also the regression test for WHO the roundtable request is addressed
to. The director lives in its own container (worker-roundtable, worker id
"roundtable") since the GM character was split onto its own tuber_base
channel; addressing the old "tuber_0" worker id would air nothing at all,
and silently — a non-director never polls the request file.

No LLM/bus/DB involved; /messages is mocked at the panel's own _mapi_request
seam.
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


def test_play_sends_a_replay_request_to_every_character_worker_and_the_roundtable(monkeypatch):
    """One click must reach all 7 audiences — no operator has to know the
    worker list or remember the roundtable needs a different payload."""
    calls = []

    async def _mapi_request(method, path, **kwargs):
        calls.append((method, path, kwargs))

        class _R:
            ok = True
            error = None
            data = {"episodes": []} if path == "/replays" else {}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.post("/replays/ashiorid_smoke/play")
    assert resp.status_code == 200

    message_calls = [c for c in calls if c[1] == "/messages"]
    assert len(message_calls) == len(panel.WORKER_IDS) + 1  # 6 characters + roundtable

    sent_by_worker = {c[2]["json"]["to"]: c[2]["json"] for c in message_calls}
    assert set(sent_by_worker) == set(panel.WORKER_IDS) | {panel.ROUNDTABLE_WORKER_ID}

    # Every character channel gets the bare payload — no cast.
    for worker_id in panel.WORKER_IDS:
        msg = sent_by_worker[worker_id]
        assert msg["type"] == "replay_request"
        assert msg["payload"] == {"episode": "ashiorid_smoke"}

    # The roundtable alone gets the worker->slot cast attached, or its tile
    # grid never lights up (app/replay_pane.py perform_director_request
    # only runs when payload.cast is present).
    assert panel.ROUNDTABLE_WORKER_ID == "roundtable"
    rt_msg = sent_by_worker[panel.ROUNDTABLE_WORKER_ID]
    assert rt_msg["payload"]["episode"] == "ashiorid_smoke"
    assert rt_msg["payload"]["cast"] == panel.WORKER_TO_TUBER_SLOT
    assert rt_msg["payload"]["cast"]["coder"] == "tuber_1"
    # manager carries the GM/narrator's lines in every episode-building
    # script (build_campaign_episode.py's "gm": "manager", build_generated_
    # episode.py's "ashiorid": "manager") — it must map to the GM's OWN
    # tile (tuber_0), not MAX-1's (tuber_6), or the GM's lines get no
    # bubble/status update even though the audio (correctly) plays as the
    # GM slot's own voice. Regression guard for the real reported bug:
    # "I can hear the voice but the Game Master shows no text."
    #
    # The cast's VALUES are tuber SLOT ids, not container worker ids, so this
    # stays "tuber_0" even though the director's worker id is now
    # "roundtable" — the two were the same string before the split.
    assert rt_msg["payload"]["cast"]["manager"] == "tuber_0"
    assert rt_msg["payload"]["cast"]["manager"] != panel.ROUNDTABLE_WORKER_ID

    assert "all 7 streams" in resp.text or "all" in resp.text


def test_play_never_sends_a_bare_broadcast_message(monkeypatch):
    """Regression guard for the actual production bug: a "to": "broadcast"
    replay_request reaches the roundtable's agent too, racing its own
    cast-bearing request (agent.py's request-file write has no de-dupe/clobber
    guard for a plain replay_request the way handle_replay_invite does). Every
    play must address workers BY NAME so nothing but the one call below
    ever writes to the roundtable's request file."""
    calls = []

    async def _mapi_request(method, path, **kwargs):
        calls.append((method, path, kwargs))

        class _R:
            ok = True
            error = None
            data = {"episodes": []} if path == "/replays" else {}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    client.post("/replays/ashiorid_smoke/play")

    message_calls = [c for c in calls if c[1] == "/messages"]
    assert all(c[2]["json"]["to"] != "broadcast" for c in message_calls)


def test_play_partial_failure_still_reports_which_streams_failed(monkeypatch):
    """One unreachable worker must not stop the episode airing on the
    other six/seven, and the operator must be told exactly what failed."""
    async def _mapi_request(method, path, **kwargs):
        if path == "/messages" and kwargs.get("json", {}).get("to") == "tester":
            class _Fail:
                ok = False
                error = "worker unreachable"
                data = None
            return _Fail()

        class _Ok:
            ok = True
            error = None
            data = {"episodes": []} if path == "/replays" else {}
        return _Ok()

    client = _client(monkeypatch, _mapi_request)
    resp = client.post("/replays/ashiorid_smoke/play")
    assert resp.status_code == 200
    assert "tester" in resp.text
    assert "worker unreachable" in resp.text
    assert "ashiorid_smoke" in resp.text
