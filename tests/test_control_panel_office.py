"""Tests for services/control-panel/panel.py's office show mapping (OB-32):
CONTROL_PANEL_SHOW=office swaps the dev-team worker ids for the office
seats tuber_0..tuber_7 (+ worker-observer); anything else leaves the
dev-team lists exactly as they were.

Same seam as tests/test_control_panel.py: panel._mapi_request is replaced
with an AsyncMock, and the office mapping is applied with monkeypatch.
"""
import pathlib
import sys
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "control-panel"))

import panel  # noqa: E402

SEATS = [f"tuber_{i}" for i in range(8)]
DEV_TEAM_IDS = ["coder", "coder-native", "coder-opencode", "coder-aider", "manager", "tester"]


def mapi_result(ok=True, data=None):
    return panel.MapiResult(ok=ok, status_code=200, data=data, error=None)


@pytest.fixture
def office_panel(monkeypatch):
    """The panel as it runs under docker-compose.office.yml."""
    mapping = panel.show_mapping(panel.SHOW_OFFICE)
    for name, value in mapping.items():
        monkeypatch.setattr(panel, name, value)
    monkeypatch.setattr(panel, "SHOW", panel.SHOW_OFFICE)
    monkeypatch.setattr(panel, "_THEME_NAMES_CACHE", None)
    mock = AsyncMock(return_value=mapi_result())
    monkeypatch.setattr(panel, "_mapi_request", mock)
    with TestClient(panel.app) as client:
        client.mapi = mock
        yield client


@pytest.mark.parametrize("raw,expected", [
    ("office", "office"), (" Office ", "office"), ("OFFICE", "office"),
    ("", "dev_team"), ("dev_team", "dev_team"), ("offices", "dev_team"),
])
def test_resolve_show_values(raw, expected):
    assert panel.resolve_show(raw) == expected


def test_resolve_show_reads_env(monkeypatch):
    monkeypatch.setenv(panel.CONTROL_PANEL_SHOW_ENV, "office")
    assert panel.resolve_show() == panel.SHOW_OFFICE
    monkeypatch.delenv(panel.CONTROL_PANEL_SHOW_ENV)
    assert panel.resolve_show() == panel.SHOW_DEV_TEAM


def test_default_import_is_dev_team_unchanged():
    """No CONTROL_PANEL_SHOW in the test env: the module-level lists are the
    dev-team ones, byte-for-byte what they were before the office mapping."""
    assert panel.SHOW == panel.SHOW_DEV_TEAM
    assert panel.WORKER_IDS == DEV_TEAM_IDS
    assert panel.PLAY_WORKER_IDS == DEV_TEAM_IDS
    assert panel.THEME_WORKER_IDS == DEV_TEAM_IDS + ["tuber_0", "roundtable"]
    assert panel.WORKER_TO_TUBER_SLOT["manager"] == "tuber_0"
    assert panel.WORKER_TO_SERVICE["coder-aider"] == "worker-coder-aider"
    assert panel.show_mapping(panel.SHOW_DEV_TEAM)["WORKER_IDS"] == DEV_TEAM_IDS


def test_office_mapping_seats_and_observer():
    mapping = panel.show_mapping(panel.SHOW_OFFICE)
    assert mapping["WORKER_IDS"] == SEATS
    assert mapping["PLAY_WORKER_IDS"] == SEATS[:7]            # never the Party Member
    assert mapping["WORKER_TO_TUBER_SLOT"] == {s: s for s in SEATS}
    assert mapping["WORKER_TO_SERVICE"]["tuber_7"] == "worker-observer"
    assert mapping["THEME_WORKER_IDS"] == SEATS + ["roundtable"]
    # Asking for the dev-team mapping afterwards is unaffected.
    assert panel.show_mapping(panel.SHOW_DEV_TEAM)["WORKER_IDS"] == DEV_TEAM_IDS


def test_office_seat_services_match_the_compose_override():
    """Every office seat's service really re-seats that WORKER_ID in
    docker-compose.office.yml, and the override sets CONTROL_PANEL_SHOW."""
    compose = yaml.safe_load((ROOT / "docker-compose.office.yml").read_text())
    services = compose["services"]
    for seat, service in panel.OFFICE_SEAT_TO_SERVICE.items():
        assert services[service]["environment"]["WORKER_ID"] == seat, service
    assert services["control-panel"]["environment"]["CONTROL_PANEL_SHOW"] == "office"


def test_office_workers_partial_lists_every_seat(office_panel):
    office_panel.mapi.return_value = mapi_result(data={"workers": []})
    resp = office_panel.get("/partials/workers")
    assert resp.status_code == 200
    for seat in SEATS:
        assert seat in resp.text
    assert "coder-aider" not in resp.text


def test_office_play_targets_speaking_seats_and_roundtable(office_panel):
    office_panel.mapi.return_value = mapi_result(data={"episodes": []})
    resp = office_panel.post("/replays/office-ep/play", data={"record": "none"})
    assert resp.status_code == 200
    requests = [kw["json"] for args, kw in office_panel.mapi.await_args_list
                if args == ("POST", "/messages") and kw["json"]["type"] == "replay_request"]
    targets = [r["to"] for r in requests]
    assert targets == SEATS[:7] + ["roundtable"]
    assert "tuber_7" not in targets[:-1]
    roundtable = requests[-1]["payload"]
    assert roundtable["cast"] == {s: s for s in SEATS}
    assert "all 8 streams (7 channels + roundtable)" in resp.text
