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
import replay_pane  # noqa: E402
from replay_pane import (  # noqa: E402
    list_episodes,
    load_worker_config,
    perform_request,
    publish_narration,
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


def test_perform_request_voiced_show_prepared_from_config(library, capsys, monkeypatch):
    prepared = {}

    def fake_prepare(script, config, workdir, **kwargs):
        prepared["config"] = config
        return [{"events": script["events"], "narration": "tonight's line", "audio": None}]

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", fake_prepare)
    config = {"voice": {"provider": "piper"}}
    ok = perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None,
                         config=config)
    out = capsys.readouterr().out
    assert ok is True
    assert prepared["config"] is config
    assert "tonight's line" in out and "hello stream" in out


def test_perform_request_voice_false_skips_preparation(library, capsys, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("voice preparation should not run")

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", explode)
    ok = perform_request({"episode": "ep1", "speed": 0, "voice": False},
                         library, "KODI-7", None, config={"voice": {"provider": "piper"}})
    assert ok is True
    assert "hello stream" in capsys.readouterr().out


def test_perform_request_voice_failure_still_airs_silent(library, capsys, monkeypatch):
    def broken_prepare(*args, **kwargs):
        raise RuntimeError("ollama exploded")

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", broken_prepare)
    ok = perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None,
                         config={"voice": {"provider": "piper"}})
    captured = capsys.readouterr()
    assert ok is True
    assert "hello stream" in captured.out  # the show aired anyway
    assert "silent show" in captured.err


# ── publish_narration: the durable transcript (docs/revoice.md) ─────────────
class FakeBusProducer:
    def __init__(self, bootstrap_servers, topic):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic

    def send(self, message):
        FakeBusProducer.sent.append(message)
        return message


def _voiced_show():
    return [
        {"kind": "boss", "speaker": "boss", "narration": "ship the login fix"},
        {"kind": "coder_talk", "speaker": "coder", "narration": "on it"},
    ]


def test_publish_narration_sends_one_message_with_all_scenes(monkeypatch):
    FakeBusProducer.sent = []
    monkeypatch.setattr(replay_pane, "MessageProducer", FakeBusProducer)
    config = {"message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages",
                              "worker_id": "coder"}}

    publish_narration(_voiced_show(), config, "ep1", "KODI-7")

    assert len(FakeBusProducer.sent) == 1
    msg = FakeBusProducer.sent[0]
    assert msg["type"] == "replay_narration"
    assert msg["from"] == "coder"
    assert msg["payload"]["episode"] == "ep1"
    assert [s["text"] for s in msg["payload"]["scenes"]] == ["ship the login fix", "on it"]
    assert [s["speaker"] for s in msg["payload"]["scenes"]] == ["boss", "coder"]


def test_publish_narration_skips_when_show_is_none(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("must not construct a producer for a silent show")

    monkeypatch.setattr(replay_pane, "MessageProducer", explode)
    publish_narration(None, {"message_bus": {"bootstrap_servers": "k", "topic": "t"}}, "ep1", "KODI-7")


def test_publish_narration_skips_without_message_bus_config(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("must not construct a producer without message_bus config")

    monkeypatch.setattr(replay_pane, "MessageProducer", explode)
    publish_narration(_voiced_show(), {"voice": {"provider": "piper"}}, "ep1", "KODI-7")


def test_publish_narration_failure_is_swallowed(monkeypatch, capsys):
    class ExplodingProducer:
        def __init__(self, *a, **kw):
            raise RuntimeError("kafka down")

    monkeypatch.setattr(replay_pane, "MessageProducer", ExplodingProducer)
    config = {"message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages"}}

    publish_narration(_voiced_show(), config, "ep1", "KODI-7")  # must not raise

    assert "publish failed" in capsys.readouterr().err


def test_perform_request_publishes_narration_after_voiced_show(library, monkeypatch):
    def fake_prepare(script, config, workdir, **kwargs):
        return [dict(scene, events=[]) for scene in _voiced_show()]

    FakeBusProducer.sent = []
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", fake_prepare)
    monkeypatch.setattr(replay_pane, "MessageProducer", FakeBusProducer)
    config = {"voice": {"provider": "piper"},
              "message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages"}}

    ok = perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None, config=config)

    assert ok is True
    assert len(FakeBusProducer.sent) == 1
    assert FakeBusProducer.sent[0]["payload"]["episode"] == "ep1"


def test_load_worker_config_missing_or_bad_returns_none(tmp_path, capsys):
    assert load_worker_config(tmp_path / "nope.yaml") is None
    bad = tmp_path / "bad.yaml"
    bad.write_text("{{not yaml", encoding="utf-8")
    assert load_worker_config(bad) is None


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
