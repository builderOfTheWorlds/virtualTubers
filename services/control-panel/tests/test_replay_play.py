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
import pytest
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

    message_calls = [c for c in calls if c[1] == "/messages"
                     and c[2]["json"]["type"] == "replay_request"]
    assert len(message_calls) == len(panel.WORKER_IDS) + 1  # 6 characters + roundtable
    # ...each preceded by a replay_stop so Play preempts instead of queuing
    stops = [c for c in calls if c[1] == "/messages" and c[2]["json"]["type"] == "replay_stop"]
    assert len(stops) == len(panel.WORKER_IDS) + 1

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
    assert rt_msg["payload"]["cast"] == {**panel.WORKER_TO_TUBER_SLOT,
                                         **panel.TUBER_SLOT_IDENTITY_CAST}
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


def test_play_response_includes_a_log_viewer_pointed_at_the_new_log_route(monkeypatch):
    async def _mapi_request(method, path, **kwargs):
        class _R:
            ok = True
            error = None
            data = {"episodes": []} if path == "/replays" else {}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.post("/replays/ashiorid_smoke/play")
    assert resp.status_code == 200
    assert 'hx-get="/replays/ashiorid_smoke/log?since=' in resp.text
    assert 'hx-trigger="load, every 3s"' in resp.text


def test_replay_log_merges_container_and_message_logs_chronologically(monkeypatch):
    async def _mapi_request(method, path, **kwargs):
        class _R:
            ok = True
            error = None
            data = None
        r = _R()
        if path == "/logs/containers":
            r.data = {"logs": [
                {"container_name": "virtualtubers-worker-coder-1", "stream": "stdout",
                 "message": "preparing narration", "log_timestamp": "2026-08-01T00:00:02+00:00"},
            ]}
        elif path == "/logs/messages":
            r.data = {"messages": [
                {"from": "operator", "to": "coder", "type": "replay_request",
                 "payload": {"episode": "ashiorid_smoke"}, "timestamp": "2026-08-01T00:00:01+00:00"},
            ]}
        else:
            r.data = {}
        return r

    client = _client(monkeypatch, _mapi_request)
    resp = client.get("/replays/ashiorid_smoke/log")
    assert resp.status_code == 200
    # message (00:00:01) must render before the log line (00:00:02)
    assert resp.text.index("replay_request") < resp.text.index("preparing narration")


def test_replay_log_requests_every_play_target_by_service_and_worker_id(monkeypatch):
    calls = []

    async def _mapi_request(method, path, **kwargs):
        calls.append((path, kwargs))

        class _R:
            ok = True
            error = None
            data = {"logs": []} if path == "/logs/containers" else {"messages": []}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.get("/replays/ashiorid_smoke/log")
    assert resp.status_code == 200

    container_call = next(c for c in calls if c[0] == "/logs/containers")
    services = [v for k, v in container_call[1]["params"] if k == "service"]
    assert set(services) == set(panel.WORKER_TO_SERVICE.values()) | {panel.ROUNDTABLE_SERVICE}

    message_call = next(c for c in calls if c[0] == "/logs/messages")
    worker_ids = [v for k, v in message_call[1]["params"] if k == "worker_id"]
    assert set(worker_ids) == set(panel.WORKER_IDS) | {panel.ROUNDTABLE_WORKER_ID}


def test_replay_log_reports_error_when_both_sources_fail(monkeypatch):
    async def _mapi_request(method, path, **kwargs):
        class _Fail:
            ok = False
            error = "postgres unavailable"
            data = None
        return _Fail()

    client = _client(monkeypatch, _mapi_request)
    resp = client.get("/replays/ashiorid_smoke/log")
    assert resp.status_code == 200
    assert "postgres unavailable" in resp.text


# ── Replay progress bar ─────────────────────────────────────────────────
# Line shapes copied from a real roundtable airing's container_logs rows
# (roundtable_stream_check_double, 2026-09-27) — including the ANSI color
# codes Performer wraps its headers/narration in.
_PREVIOUS_AIRING_TAIL = [
    "\x1b[2m♪ an old line from the show being preempted\x1b[0m",
    "\x1b[1m\x1b[35m══ stopped ══\x1b[0m",
]
_QUEUED = ["[agent:roundtable] queued replay episode 'roundtable_stream_check_double'"]
_PREP = [f"[replay_pane] preparing: scene {i}/4: writing coder_talk line (~8w)" for i in range(1, 5)]
_HEADER = ["\x1b[1m\x1b[35m══ REPLAY: roundtable_stream_check_double (4 scenes) ══\x1b[0m"]
_SCENES = [f"\x1b[2m♪ line {i}\x1b[0m" for i in range(1, 5)]
_FIN = ["\x1b[1m\x1b[35m══ fin ══\x1b[0m"]


def test_progress_starts_at_requested_with_no_lines():
    snap = panel.parse_replay_progress([])
    assert snap["state"] == "running"
    assert snap["phase"] == "requested"


def test_progress_ignores_the_preempted_airings_stop_marker():
    """Play sends replay_stop first, so the OLD airing's "══ stopped ══"
    lands in this window — it must not end the new bar."""
    snap = panel.parse_replay_progress(_PREVIOUS_AIRING_TAIL + _QUEUED)
    assert snap["state"] == "running"
    assert snap["phase"] == "queued"


def test_progress_tracks_voice_prep_per_scene():
    snap = panel.parse_replay_progress(_QUEUED + _PREP[:2])
    assert snap["phase"] == "preparing"
    assert (snap["scene"], snap["total"]) == (2, 4)
    assert panel.PROGRESS_QUEUED_PCT < snap["percent"] < panel.PROGRESS_PREP_END_PCT


def test_progress_counts_aired_scenes_and_is_monotonic():
    lines = _QUEUED + _PREP + _HEADER + _SCENES
    percents = [panel.parse_replay_progress(lines[:n])["percent"] for n in range(len(lines) + 1)]
    assert percents == sorted(percents)
    snap = panel.parse_replay_progress(lines)
    assert snap["phase"] == "airing"
    assert (snap["scene"], snap["total"]) == (4, 4)
    assert snap["percent"] < 100


def test_progress_reaches_100_on_fin():
    snap = panel.parse_replay_progress(_PREVIOUS_AIRING_TAIL + _QUEUED + _PREP + _HEADER + _SCENES + _FIN)
    assert snap["state"] == "done"
    assert snap["percent"] == 100


def test_progress_reports_a_stop_during_this_airing():
    snap = panel.parse_replay_progress(_QUEUED + _HEADER + _SCENES[:2] + ["══ stopped ══"])
    assert snap["state"] == "stopped"
    assert "2/4" in snap["label"]


@pytest.mark.parametrize("line", [
    "[replay_pane] duet refused: timed out waiting for duet followers to become ready",
    "[replay_pane] episode not in the library: 'nope'",
])
def test_progress_reports_failures_after_the_request_was_queued(line):
    snap = panel.parse_replay_progress(_QUEUED + [line])
    assert snap["state"] == "failed"


def test_progress_route_filters_to_roundtable_milestones_and_stops_polling_when_done(monkeypatch):
    calls = []

    async def _mapi_request(method, path, **kwargs):
        calls.append((path, kwargs))

        class _R:
            ok = True
            error = None
            data = {"logs": [{"message": m} for m in _QUEUED + _HEADER + _SCENES + _FIN]}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.get("/replays/roundtable_stream_check_double/progress",
                      params={"since": "2026-09-27T16:40:43+00:00"})
    # 286 = htmx's "stop polling" status
    assert resp.status_code == 286
    assert 'width: 100%' in resp.text
    assert "finished" in resp.text

    path, kwargs = calls[0]
    assert path == "/logs/containers"
    params = kwargs["params"]
    assert [v for k, v in params if k == "service"] == [panel.ROUNDTABLE_SERVICE]
    assert set(v for k, v in params if k == "contains") == set(panel.PROGRESS_MARKERS.values())
    assert ("since", "2026-09-27T16:40:43+00:00") in params


def test_progress_route_keeps_polling_while_running_or_unreachable(monkeypatch):
    async def _mapi_request(method, path, **kwargs):
        class _Fail:
            ok = False
            error = "postgres unavailable"
            data = None
        return _Fail()

    client = _client(monkeypatch, _mapi_request)
    resp = client.get("/replays/x/progress")
    assert resp.status_code == 200
    assert "postgres unavailable" in resp.text


def test_play_response_includes_a_progress_bar_polling_the_progress_route(monkeypatch):
    async def _mapi_request(method, path, **kwargs):
        class _R:
            ok = True
            error = None
            data = {"episodes": []} if path == "/replays" else {}
        return _R()

    client = _client(monkeypatch, _mapi_request)
    resp = client.post("/replays/ashiorid_smoke/play")
    assert 'hx-get="/replays/ashiorid_smoke/progress?since=' in resp.text


def test_progress_ignores_the_preempted_airings_replay_stop_refusal_after_queued():
    snap = panel.parse_replay_progress(_QUEUED + [
        "[replay_pane] duet refused: operator replay_stop received before duet cast was ready"])
    assert snap["state"] == "running"


# ── Save to file (docs/stream_recorder.md) ───────────────────────────────
def _recording_mapi(calls, reserve_ok=True):
    async def _mapi_request(method, path, **kwargs):
        calls.append((method, path, kwargs))

        class _R:
            ok = True
            error = None
            status_code = 200
            data = {"episodes": []} if path == "/replays" else {}
        r = _R()
        if path == "/recordings" and method == "POST":
            if reserve_ok:
                r.data = {"recording_id": "20260927T000000Z_ep_abcdef", "max_bytes_per_stream": 12345,
                          "estimated_bytes": 1_000_000_000, "estimated_duration_s": 1800,
                          "remaining_bytes": 4_000_000_000, "reserved_bytes": 1_200_000_000,
                          "limit_bytes": 5_000_000_000}
            else:
                r.ok, r.status_code = False, 409
                r.error = "message-api returned HTTP 409"
                r.data = {"detail": {"allowed": False, "reason": "estimated 9.00 GB exceeds the 4.00 GB left"}}
        elif path == "/recordings":
            r.data = {"recordings": [], "used_bytes": 0, "limit_bytes": 5_000_000_000,
                      "remaining_bytes": 5_000_000_000}
        return r
    return _mapi_request


def test_play_row_offers_save_to_file_choices(monkeypatch):
    calls = []
    client = _client(monkeypatch, _recording_mapi(calls))
    monkeypatch.setattr(panel, "_episode_lists", _one_episode)
    resp = client.get("/partials/replays")
    assert 'name="record"' in resp.text
    for choice in panel.RECORD_CHOICES:
        assert f'value="{choice}"' in resp.text


async def _one_episode():
    return {"replays": [{"name": "ep1"}], "replays_error": None, "drafts": [], "drafts_error": None}


def test_play_without_record_never_touches_recordings(monkeypatch):
    calls = []
    client = _client(monkeypatch, _recording_mapi(calls))
    client.post("/replays/ep1/play")
    assert not any(c[0] == "POST" and c[1] == "/recordings" for c in calls)
    reqs = [c[2]["json"] for c in calls if c[1] == "/messages" and c[2]["json"]["type"] == "replay_request"]
    assert all("record" not in r["payload"] for r in reqs)


def test_play_record_roundtable_reserves_then_tags_only_the_roundtable(monkeypatch):
    calls = []
    client = _client(monkeypatch, _recording_mapi(calls))
    resp = client.post("/replays/ep1/play", data={"record": "roundtable"})
    assert resp.status_code == 200

    reserve = next(i for i, c in enumerate(calls) if c[0] == "POST" and c[1] == "/recordings")
    first_msg = next(i for i, c in enumerate(calls) if c[1] == "/messages")
    assert reserve < first_msg  # estimated + reserved BEFORE anything is stopped/queued
    assert calls[reserve][2]["json"] == {"episode": "ep1", "streams": [panel.ROUNDTABLE_WORKER_ID]}

    reqs = {c[2]["json"]["to"]: c[2]["json"]["payload"] for c in calls
            if c[1] == "/messages" and c[2]["json"]["type"] == "replay_request"}
    assert reqs[panel.ROUNDTABLE_WORKER_ID]["record"] == {
        "recording_id": "20260927T000000Z_ep_abcdef", "max_bytes": 12345}
    assert reqs[panel.ROUNDTABLE_WORKER_ID]["cast"]  # cast still attached
    assert all("record" not in reqs[w] for w in panel.WORKER_IDS)
    assert "20260927T000000Z_ep_abcdef" in resp.text
    assert "1.00 GB" in resp.text


def test_play_record_all_tags_all_seven_streams(monkeypatch):
    calls = []
    client = _client(monkeypatch, _recording_mapi(calls))
    client.post("/replays/ep1/play", data={"record": "all"})
    reserve = next(c for c in calls if c[0] == "POST" and c[1] == "/recordings")
    assert set(reserve[2]["json"]["streams"]) == set(panel.WORKER_IDS) | {panel.ROUNDTABLE_WORKER_ID}
    reqs = [c[2]["json"]["payload"] for c in calls
            if c[1] == "/messages" and c[2]["json"]["type"] == "replay_request"]
    assert len(reqs) == 7 and all("record" in p for p in reqs)


def test_play_refused_recording_airs_nothing(monkeypatch):
    """Over budget: the operator must be told why, and NOTHING is stopped or
    queued — not a silent unrecorded airing."""
    calls = []
    client = _client(monkeypatch, _recording_mapi(calls, reserve_ok=False))
    resp = client.post("/replays/ep1/play", data={"record": "all"})
    assert resp.status_code == 200
    assert not any(c[1] == "/messages" for c in calls)
    assert "recording refused" in resp.text
    assert "exceeds the 4.00 GB left" in resp.text


def test_play_unknown_record_value_is_treated_as_none(monkeypatch):
    calls = []
    client = _client(monkeypatch, _recording_mapi(calls))
    client.post("/replays/ep1/play", data={"record": "../../etc"})
    assert not any(c[0] == "POST" and c[1] == "/recordings" for c in calls)
