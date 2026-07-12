"""Tests for app/replay_pane.py and agent.py's replay_request handler —
the operator → agent → pane wiring for Rerun Theater.

Safety property under test: a bus payload can only ever select a
pre-built episode INSIDE the library — never a path outside it.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import agent  # noqa: E402
from agent import MESSAGE_HANDLERS, handle_replay_request  # noqa: E402
from replay_pane import (  # noqa: E402
    list_episodes,
    perform_request,
    read_request,
    resolve_episode,
)


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "replays"
    lib.mkdir()
    script = {"source": "ep1", "events": [{"type": "assistant_text", "text": "hello stream"}]}
    (lib / "ep1.json").write_text(json.dumps(script), encoding="utf-8")
    return lib


# ── resolve_episode: the traversal gate ──────────────────────────────────────
def test_resolve_episode_finds_with_and_without_extension(library):
    assert resolve_episode(library, "ep1") == library / "ep1.json"
    assert resolve_episode(library, "ep1.json") == library / "ep1.json"


@pytest.mark.parametrize("hostile", [
    "../../../etc/passwd",
    "..\\..\\secrets.json",
    "/etc/passwd",
    "c:\\Users\\dev\\.env",
])
def test_resolve_episode_never_escapes_library(library, hostile):
    resolved = resolve_episode(library, hostile)
    assert resolved is None or resolved.parent == library


def test_resolve_episode_missing_or_empty_returns_none(library):
    assert resolve_episode(library, "nope") is None
    assert resolve_episode(library, "") is None
    assert resolve_episode(library, None) is None


# ── read_request: consume-once, malformed-safe ───────────────────────────────
def test_read_request_consumes_file(tmp_path):
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"episode": "ep1"}), encoding="utf-8")
    assert read_request(req) == {"episode": "ep1"}
    assert not req.exists()
    assert read_request(req) is None  # nothing pending


@pytest.mark.parametrize("content", ["not json {", "true", "[1,2]"])
def test_read_request_discards_malformed_without_raising(tmp_path, content):
    req = tmp_path / "req.json"
    req.write_text(content, encoding="utf-8")
    assert read_request(req) is None
    assert not req.exists()  # consumed, not wedged in a crash loop


# ── perform_request ──────────────────────────────────────────────────────────
def test_perform_request_plays_existing_episode(library, capsys):
    ok = perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None,
                         default_speed=1.0)
    out = capsys.readouterr().out
    assert ok is True
    assert "hello stream" in out


def test_perform_request_unknown_episode_reports_false(library, capsys):
    ok = perform_request({"episode": "missing"}, library, "KODI-7", None)
    assert ok is False
    assert "not found" in capsys.readouterr().err


def test_list_episodes_sorted_and_empty_for_missing_dir(library, tmp_path):
    (library / "another.json").write_text("{}", encoding="utf-8")
    assert list_episodes(library) == ["another", "ep1"]
    assert list_episodes(tmp_path / "nope") == []


# ── agent handler: replay_request ────────────────────────────────────────────
@pytest.fixture
def request_file(tmp_path, monkeypatch):
    path = tmp_path / "replay_request.json"
    monkeypatch.setenv("REPLAY_REQUEST_FILE", str(path))
    return path


def test_replay_request_registered_in_dispatch_table():
    assert MESSAGE_HANDLERS["replay_request"] is handle_replay_request


def test_handle_replay_request_writes_request_and_confirms(request_file):
    producer = FakeProducer()
    msg = {"from": "operator", "type": "replay_request",
           "payload": {"episode": "2026-07-02_04-27-00_6ecdde82", "speed": 2}}

    handle_replay_request("coder", {}, None, producer, msg)

    request = json.loads(request_file.read_text(encoding="utf-8"))
    assert request == {"episode": "2026-07-02_04-27-00_6ecdde82", "speed": 2}
    assert len(producer.sent) == 1
    reply = producer.sent[0]
    assert reply["to"] == "operator"
    assert reply["type"] == "operator_reply"
    assert "2026-07-02_04-27-00_6ecdde82" in reply["payload"]["narration"]


def test_handle_replay_request_missing_episode_replies_error(request_file):
    producer = FakeProducer()
    msg = {"from": "operator", "type": "replay_request", "payload": {}}

    handle_replay_request("coder", {}, None, producer, msg)

    assert not request_file.exists()
    assert "episode" in producer.sent[0]["payload"]["error"]


def test_handle_replay_request_unwritable_path_replies_error(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAY_REQUEST_FILE", str(tmp_path / "no" / "such" / "dir" / "r.json"))
    producer = FakeProducer()
    msg = {"type": "replay_request", "payload": {"episode": "ep1"}}

    handle_replay_request("coder", {}, None, producer, msg)

    assert "could not queue replay" in producer.sent[0]["payload"]["error"]
