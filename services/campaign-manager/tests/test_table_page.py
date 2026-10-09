"""Live table page (/table): run the GM + character-agent table from the browser.

table_control talks to message-api only (send a control message to the GM,
read its table_status reply back from /logs/messages). These tests fake
message-api with an httpx MockTransport, so they exercise the real request /
reply matching, and fake the GM's reply the way message-logger stores it.
"""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import main as api
import table_control as tc


class FakeMessageApi:
    """POST /messages records + answers like the GM; GET /logs/messages replays."""

    def __init__(self, reply=None, lines=(), answer=True):
        self.sent = []
        self.log = []          # rows as message-logger stores them
        self.reply = reply or {}
        self.answer = answer
        for seat, text, kind in lines:
            self.log.append({"from": "tuber_0", "to": "roundtable", "type": "table_line",
                             "payload": {"seat": seat, "text": text, "kind": kind},
                             "timestamp": "2026-10-09T22:00:01.000000+00:00"})

    def handler(self, request: httpx.Request):
        if request.method == "POST" and request.url.path == "/messages":
            body = json.loads(request.content)
            self.sent.append(body)
            if self.answer:
                result = {"table_status_request": "status", "table_run_start": "started",
                          "table_run_stop": "stopped", "scene_stop": "stopped",
                          "scene_request": "started"}[body["type"]]
                payload = dict(self.reply, result=result)
                self.log.append({"from": "tuber_0", "to": "operator", "type": "table_status",
                                 "payload": json.dumps(payload),
                                 "timestamp": "2026-10-09T22:00:02.000000+00:00"})
            return httpx.Response(200, json={"id": "m1"})
        if request.method == "GET" and request.url.path == "/logs/messages":
            assert request.url.params.get("since")
            return httpx.Response(200, json={"messages": list(self.log)})
        return httpx.Response(404)


STATUS = {"state": "running", "scene_id": "seg.leaf.slot-001", "round": 2, "phase": "speak",
          "index": 4, "next_scene_id": "seg.leaf.slot-002", "total": 47, "mode": "auto",
          "error": "", "seat_names": {"tuber_0": "GM", "tuber_1": "Chadwick"},
          "run": {"active": True, "elapsed_s": 754.0, "duration_s": 3600.0, "remaining_s": 2846.0,
                  "played": 4, "halted": False, "finished": False,
                  "story_run": "ashiorid_1_20261009_034648_e309"},
          "scenes": [{"index": 0, "scene_id": "a", "goal": "Four strangers approach the manor."},
                     {"index": 1, "scene_id": "b", "goal": "The doors open."}]}


@pytest.fixture
def fake(monkeypatch):
    f = FakeMessageApi(reply=STATUS, lines=[("tuber_0", "The gates groan open.", "direction"),
                                            ("tuber_1", "Stand back.", "reply")])
    monkeypatch.setattr(api, "table_http", httpx.AsyncClient(transport=httpx.MockTransport(f.handler)))
    monkeypatch.setattr(tc, "MESSAGE_API_URL", "http://message-api")
    return f


@pytest.fixture(scope="module")
def client():
    c = TestClient(api.app)
    c.__enter__()
    yield c
    c.__exit__(None, None, None)


# ── build_action ─────────────────────────────────────────────────────────────
def test_build_action_run_start_variants():
    assert tc.build_action("run_start", duration_min="60") == \
        ("table_run_start", {"from": "next", "duration_min": 60.0})
    assert tc.build_action("run_start", start_from="beginning") == \
        ("table_run_start", {"from": "beginning"})
    assert tc.build_action("run_start", start_from="index", start_index="12", duration_min="30") == \
        ("table_run_start", {"from": 12, "duration_min": 30.0})


@pytest.mark.parametrize("kw", [{"duration_min": "0"}, {"duration_min": "abc"},
                                {"start_from": "index", "start_index": ""},
                                {"start_from": "middle"}])
def test_build_action_rejects_bad_input(kw):
    with pytest.raises(tc.TableControlError):
        tc.build_action("run_start", **kw)


def test_build_action_stop_next_skip():
    assert tc.build_action("run_stop") == ("table_run_stop", {"now": False})
    assert tc.build_action("run_stop_now") == ("table_run_stop", {"now": True})
    assert tc.build_action("next") == ("scene_request", {"next": True})
    assert tc.build_action("skip") == ("scene_stop", {})
    with pytest.raises(tc.TableControlError):
        tc.build_action("reboot")


# ── reply matching ───────────────────────────────────────────────────────────
def test_an_action_ignores_a_plain_status_reply_from_another_tab():
    f = FakeMessageApi(answer=False)
    f.log.append({"from": "tuber_0", "to": "operator", "type": "table_status",
                  "payload": {"result": "status"}, "timestamp": "x"})
    c = httpx.AsyncClient(transport=httpx.MockTransport(f.handler))
    got = asyncio.run(tc.wait_status(c, "2026-10-09T00:00:00+00:00", 0.05, poll_s=0.01, action=True))
    assert got is None
    got = asyncio.run(tc.wait_status(c, "2026-10-09T00:00:00+00:00", 0.05, poll_s=0.01))
    assert got == {"result": "status"}


def test_describe_formats_the_run():
    d = tc.describe(dict(STATUS, result="status"))
    assert d["run_state"] == "running unattended" and d["elapsed"] == "12:34"
    assert d["remaining"] == "47:26" and d["duration"] == "1:00:00"
    assert tc.describe(None) == {"ok": False}
    halted = dict(STATUS, run=dict(STATUS["run"], halted=True, active=False))
    assert tc.describe(halted)["run_state"].startswith("halted")


# ── routes ───────────────────────────────────────────────────────────────────
def test_table_page_lists_scenes_for_the_start_picker(client, fake):
    r = client.get("/table")
    assert r.status_code == 200
    assert "Four strangers approach the manor." in r.text and "Start run" in r.text
    assert fake.sent == [{"to": "tuber_0", "type": "table_status_request", "payload": {"scenes": True}}]


def test_start_run_posts_table_run_start_and_shows_the_result(client, fake):
    r = client.post("/table/action", data={"action": "run_start", "duration_min": "60",
                                           "start_from": "index", "start_index": "1"})
    assert r.status_code == 200
    assert fake.sent[-1] == {"to": "tuber_0", "type": "table_run_start",
                             "payload": {"from": 1, "duration_min": 60.0}}
    assert "Started." in r.text and "running unattended" in r.text and "47:26" in r.text


def test_stop_buttons(client, fake):
    client.post("/table/action", data={"action": "run_stop"})
    client.post("/table/action", data={"action": "run_stop_now"})
    assert [m["payload"] for m in fake.sent] == [{"now": False}, {"now": True}]


def test_bad_form_input_is_reported_and_nothing_is_sent(client, fake):
    r = client.post("/table/action", data={"action": "run_start", "duration_min": "-5"})
    assert "more than 0" in r.text and fake.sent == []


def test_no_gm_reply_says_so(client, monkeypatch):
    f = FakeMessageApi(answer=False)
    monkeypatch.setattr(api, "table_http", httpx.AsyncClient(transport=httpx.MockTransport(f.handler)))
    monkeypatch.setattr(api, "TABLE_ACTION_TIMEOUT_S", 0.05)
    r = client.post("/table/action", data={"action": "skip"})
    assert "did not answer" in r.text


def test_status_partial_and_lines(client, fake):
    r = client.get("/table/status")
    assert "seg.leaf.slot-001" in r.text and "round 2" in r.text
    r = client.get("/table/lines", params={"names": json.dumps(STATUS["seat_names"])})
    assert "Chadwick" in r.text and "Stand back." in r.text and "The gates groan open." in r.text


def test_message_api_down_is_an_error_banner(client, monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(api, "table_http", httpx.AsyncClient(transport=httpx.MockTransport(down)))
    r = client.post("/table/action", data={"action": "next"})
    assert "message-api unreachable" in r.text
